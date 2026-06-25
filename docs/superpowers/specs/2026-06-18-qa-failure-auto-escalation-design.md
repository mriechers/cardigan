# QA-Failure Auto-Escalation + Pause-and-Suggest Failure Handling — Design

**Date:** 2026-06-18 (updated 2026-06-19)
**Status:** Approved (design); pending implementation plan
**Author:** Claude Code (brainstormed with Mark)
**Depends on:** `2026-06-19-model-selection-integrity-design.md` (Spec A) —
**hard prerequisite.** Escalation is meaningless until a selected/escalated
model actually runs and is recorded honestly.

## Problem

The pipeline runs a validator phase that produces a structured
`validation_result` with an `overall` verdict (`pass` / `fail`) and
per-phase flags. That verdict is **computed, stored, and then ignored**.

In `api/services/worker.py` (around line 1037) the worker calls
`update_job_status(job_id, JobStatus.completed)` unconditionally once all
phases have executed without throwing. Execution success ("no phase raised
an exception") is conflated with content success ("the validator approved
the output"). A job whose validator returned `overall: "fail"` still lands
as a green **completed** job, with `error_message: null`, indistinguishable
in the queue from a clean run.

Caught live (cardigan01:8100): the two most recent finished jobs both had
`validation_result.overall == "fail"` yet `status == "completed"`:

| Job | Transcript | Status | Validator verdict | Flagged phases |
|-----|-----------|--------|-------------------|----------------|
| 10 | `6POL0114.srt` | completed | **fail** | formatter, seo |
| 9 | `6POL0114CLEAN.srt` | completed | **fail** | formatter |

Job 9 is set aside (raw transcript with stray audio — a data problem). Job
10 is the motivating case: a clean transcript that flagged heavily. The
failures were **tier-correlated** — the flagged phases were the cheapest /
most-fragmented passes (SEO on Haiku; formatter chunked across 2 chunks).

A second failure mode surfaced in flight (job 11): **OpenRouter credit
exhaustion fails silently.** `_call_openrouter` (`llm.py:567`) has no branch
that recognizes a 402; it `print()`s the body and `raise_for_status()`es a
raw `httpx.HTTPStatusError`, which the optional-phase non-fatal handler then
swallows — leaving the job wedged `in_progress` with an empty
`error_message`.

QA-fail, credit-exhaustion, and the already-handled truncation case all want
the **same** outcome: stop, set a visible non-`completed` state, show the
editor a clear next step. This spec builds that shared machinery and wires
all three triggers into it.

### Live evidence from a controlled re-run (job 11, 2026-06-19)

To validate the design we re-ran the pipeline with `analyst→Opus-4.8` and
`seo/validator→Sonnet-4.6` (via per-phase `model_override`, the only channel
that works pre-Spec-A). Findings that shaped this spec:

- **Validator→Sonnet is the biggest lever.** On comparable content, the
  flag count dropped from **8 (Haiku judge) to 1 (Sonnet judge)**: SEO went
  `fail` (4 flags) → `pass`, and formatter went 4 flags → 1. The dropped
  items were exactly the noise (Sara/Sarah, "attribution uncertainty,"
  speculative truncation-risk, SEO draft/limit confusion). Hard evidence for
  the validator-default change below.
- **Escalation is not a cure-all.** The single flag that survived a Sonnet
  judge was the formatter's *intentional* `<!-- REVIEW NOTES -->` HTML
  comment block (invisible in rendered markdown; carries real editorial
  intel). No model bump clears it — it is a formatter↔validator contract
  question, not a quality problem. So the auto-escalate loop will still
  terminate at pause-and-suggest for structural flags.
- **Confound noted:** job 11's transcript (`6POL0114_retry.srt`) is a
  shorter cut (~11 min) than job 10's full 32 min, so content-specific
  comparisons (e.g. speaker attribution in the later word-frequency segment)
  are inconclusive — that segment isn't in job 11's transcript.

## Goals

1. A job whose validator returns `overall: "fail"` must never silently land
   as `completed`.
2. When QA fails, the system **automatically re-runs the weak work once on a
   stronger model**, and only escalates to the editor if it still fails.
3. "Stronger model" is decided by **model family** (`haiku → sonnet → opus`),
   parsed from the model the phase actually ran on — not the
   `cheapskate / default / big-brain` tier labels.
4. **OpenRouter credit exhaustion surfaces a clear, actionable message**
   ("add credit, then retry") instead of failing silently, and does not
   consume a retry.

## Non-Goals

- Removing the tier labels from `phase_backends` system-wide (separate
  refactor).
- **Model-selection integrity** (config propagation, chunked-formatter
  override, model recording) — moved to **Spec A**, which this depends on.
- Re-tuning the validator's editorial criteria so it stops counting missing
  optional inputs (Media ID, air date) or the REVIEW-NOTES block as
  failures. Deferred, but now with a concrete first case (see below).

## Prerequisite (Spec A)

This feature assumes the model an escalation picks **actually runs and is
recorded**. Two Spec A fixes are load-bearing here:

- **Config/model selection takes effect on the worker** — otherwise the
  baseline being judged didn't run on the configured models.
- **The chunked formatter honors a model override** — otherwise escalating
  the formatter (the most-flagged phase) is a silent no-op. Spec B's
  formatter escalation cannot work without Spec A Fix 2.
- **`phases[].model` is accurate** — family parsing for escalation reads it.

## Design

### Shared pause-and-suggest terminal helper

One helper all triggers call: set `JobStatus.paused`, write a structured,
actionable `error_message` (trigger + what to do), preserve per-phase flags
where relevant, ensure the job is visibly **not** `completed`. Truncation is
migrated onto this helper; QA-fail and credit-exhaustion are new callers.

### Trigger A — QA failure (validator `overall: "fail"`)

After all phases run, **before** the unconditional `completed` transition,
inspect `validation_result.overall`: `"pass"` → `completed`; `"fail"` →
escalate.

**Escalation (automatic, once):**
1. **Select phases:** earliest validator-flagged phase plus every downstream
   phase (reuse the existing reset-downstream logic).
2. **Determine each phase's family** from its actual running model's slug
   (`haiku | sonnet | opus`), robust to OpenRouter's mixed word order.
3. **Bump one family up** (`haiku → sonnet → opus`); `opus` is terminal.
4. **Resolve the target model from the live OpenRouter catalog:** newest
   `anthropic/*` in the target family by `created`, **excluding `-fast` and
   `fable`**. Cached (~1h TTL); on fetch failure, log and fall through to
   pause-and-suggest.
5. **Re-run** the selected phases on their escalated models (passed as
   `model_override` — works for the formatter only once Spec A Fix 2 lands),
   then **re-validate** on the configured default (Sonnet).
   **Optional:** re-run the formatter **un-chunked** on the bigger-context
   escalated model — removes chunk-seam risk and the override-drop path in
   one move (depends on Spec A Fix 2).

**Terminal states:**
- Re-validation `"pass"` → `completed`, escalation recorded.
- Still `"fail"`, or every flagged phase already `opus`, or the one
  auto-escalation already used → **pause-and-suggest**
  (*"QA failed — review or retry on a stronger model"* + per-phase flags).
  Note: **structural flags survive escalation** (e.g. the REVIEW-NOTES
  block), so this state is expected even on a healthy pipeline until the
  criteria question is resolved.

**Manual retry after pause:** bumps each not-yet-`opus` phase one more family;
`opus` is the ceiling.

**"Escalate once" guard:** a persisted marker (e.g. `auto_escalated_at`)
prevents the loop or a later manual retry from re-triggering the auto path.

### Trigger B — OpenRouter credit exhaustion

- In `_call_openrouter`, detect insufficient-credit responses (HTTP 402, or
  an error body indicating credit/quota exhaustion) and raise a new typed
  `CreditExhaustedError` (sibling of the existing typed LLM errors).
- It is **never swallowed**, even in an optional phase. Both handlers route
  to **pause-and-suggest**: *"OpenRouter credit exhausted — add credit, then
  retry."*
- It **does not consume a retry**.

### Trigger C — Truncation (existing)

Migrated onto the shared pause-and-suggest helper; behavior unchanged.

### Validator default

Move the `validator` phase off the cheapskate tier to the default
(Sonnet 4.6). Configurable. Evidence: the live re-run showed an **8 → 1**
flag reduction switching the judge from Haiku to Sonnet — a stronger judge
is both more trustworthy and lets the auto-escalate loop converge.

### Configuration

```jsonc
"qa_escalation": {
  "on_validation_fail": true,
  "max_auto_escalations": 1,
  "family_order": ["haiku", "sonnet", "opus"],
  "exclude_variants": ["fast", "fable"]
}
```

Plus moving `validator` to the default (Sonnet) model. Credit-exhaustion
handling has no tunables (always on).

## Deferred follow-up (was a non-goal, now has a concrete case)

The first criteria-tuning question to resolve: **is the formatter's
`<!-- REVIEW NOTES -->` HTML comment block actually a validator-level
defect?** It is invisible in rendered markdown and carries useful editorial
intel. Either the formatter should stop emitting it into the body, or the
validator should stop flagging it. Until decided, jobs with only this flag
will pause-and-suggest despite being publishable.

## What "OpenRouter provides" (reference)

`/api/v1/models` (public, no key) exposes per model: `id` (family from the
slug), `created` (newest in family), `pricing`, `context_length`,
`benchmarks`, etc. — but **no** capability rank or successor pointer.
Pricing is not a reliable ladder: `opus-*-fast` ($10–30/Mtok) and `fable-5`
($10/Mtok) price above plain `opus` ($5/Mtok), so a "next most expensive"
rule jumps wrong. Hence family parsing + explicit order, `-fast`/`fable`
excluded.

## Testing

**Unit**
- Family parse from varied slugs; family bump + `opus` clamp.
- `-fast` / `fable` exclusion.
- "Escalate once" guard; earliest-flagged-phase + downstream selection.
- Catalog-fetch-failure falls back to pause-and-suggest.
- `CreditExhaustedError` raised on 402 / credit body; **not** swallowed by
  the optional-phase handler.
- Pause-and-suggest helper writes the correct per-trigger message and never
  leaves the job `completed`.

**Integration** (LLM and catalog stubbed — no spend, no live network)
- Validator `fail` then `pass` ⇒ `completed` with escalation recorded.
- Validator persistent `fail` ⇒ `paused` with suggest message + flags.
- Flagged phase already `opus` ⇒ straight to `paused`.
- OpenRouter 402 mid-job ⇒ `paused` with credit message, retry count
  unchanged.

## Open questions

None outstanding. (Spec split into A/B, manual-retry behavior,
`-fast`/`fable` exclusion, and the job-11 evidence were settled during
design.)
