# QA-Failure Auto-Escalation — Design

**Date:** 2026-06-18
**Status:** Approved (design); pending implementation plan
**Author:** Claude Code (brainstormed with Mark)

## Problem

The pipeline runs a validator phase that produces a structured
`validation_result` with an `overall` verdict (`pass` / `fail`) and
per-phase flags. That verdict is **computed, stored, and then ignored**.

In `api/services/worker.py` (around line 1037) the worker calls
`update_job_status(job_id, JobStatus.completed)` unconditionally once all
phases have executed without throwing. Execution success ("no phase raised
an exception") is conflated with content success ("the validator approved
the output"). As a result a job whose validator returned
`overall: "fail"` still lands as a green **completed** job, with
`error_message: null`, indistinguishable in the queue from a clean run.

This was caught on the live system (cardigan01:8100): the two most recent
jobs both had `validation_result.overall == "fail"` yet `status ==
"completed"`:

| Job | Transcript | Status | Validator verdict | Flagged phases |
|-----|-----------|--------|-------------------|----------------|
| 10 | `6POL0114.srt` | completed | **fail** | formatter, seo |
| 9 | `6POL0114CLEAN.srt` | completed | **fail** | formatter |

Job 9 is set aside (raw transcript with stray audio — a data problem, not a
pipeline problem). Job 10 is the motivating case: a clean transcript that
should not have flagged so heavily. Investigation showed the failures are
**tier-correlated** — the flagged phases were the cheapest/most-fragmented
passes (SEO on Haiku; formatter run chunked across 2 chunks, which breaks
speaker attribution at chunk seams). The defects are the kind a stronger
model resolves.

The validator that produced the harsh verdict was *itself* on the cheapest
tier (Haiku) and was visibly unreliable — it flagged the SEO phase for
presenting a draft *and* a corrected version, and admitted uncertainty about
whether its own 300-character limit applied.

## Goals

1. A job whose validator returns `overall: "fail"` must never silently land
   as `completed`.
2. When QA fails, the system **automatically re-runs the weak work once on a
   stronger model**, and only escalates to the editor if it still fails.
3. "Stronger model" is decided by **model family** (`haiku → sonnet → opus`),
   parsed from the model the phase actually ran on — **not** by the
   `cheapskate / default / big-brain` tier labels, which we want to stop
   leaning on.

## Non-Goals

- Ripping the `cheapskate / default / big-brain` labels out of
  `phase_backends` system-wide. That is a separate, larger refactor. This
  feature is built family-first and adds **no new** dependence on those
  labels; it is the first brick toward removing them, not the teardown.
- Re-tuning the validator's editorial criteria (e.g. so it stops counting
  missing optional inputs like Media ID or air date as failures). Worth
  doing later; out of scope here. Moving the validator off Haiku is expected
  to reduce the worst of the noise on its own.

## Existing machinery this reuses

- **Truncation pause/suggest pattern** (`worker.py` ~900–960): when the
  formatter truncates a transcript, the job is set to `JobStatus.paused`
  with an actionable message ("Retry to escalate to a more capable model"),
  and the retry endpoint resets formatter + downstream phases. This is the
  proven template for "detect a problem → pause → suggest retry → reset the
  right phases." QA-failure escalation generalizes it to a new trigger.
- **Retry phase-reset logic** in `api/routers/jobs.py` `retry_job` — already
  computes "reset this phase and everything downstream."
- **Provenance headers** — each `<phase>_output.md` already carries a
  `<!-- model: ... -->` header recording the model that produced it.

## Design

### Trigger

After all phases run, **before** the unconditional `completed` transition,
inspect `validation_result.overall`:

- `"pass"` → `completed` (unchanged behavior).
- `"fail"` → enter escalation.

### Escalation (automatic, once)

1. **Select phases.** Find the earliest phase the validator flagged
   (`validation_result.phase_results[*].status == "fail"`) and include it
   plus every downstream phase, reusing the existing reset-downstream logic.
2. **Determine each phase's family.** Read the phase's actual running model
   from its provenance header, parse the family token (`haiku | sonnet |
   opus`) from the slug (robust to OpenRouter's mixed word order, e.g. both
   `claude-sonnet-4.6` and `claude-4.5-haiku-20251001`).
3. **Bump one family up** per phase: `haiku → sonnet → opus`. `opus` is
   terminal. A phase already at `opus` cannot escalate.
4. **Resolve the concrete target model from the live OpenRouter catalog:**
   query `GET https://openrouter.ai/api/v1/models`, filter to
   `anthropic/*` models in the target family, exclude `-fast` and `fable`
   variants, and pick the newest by the `created` timestamp. The catalog
   response is **cached** (TTL ~1h) so this is not a per-job network hit.
5. **Re-run** the selected phases on their escalated models, then
   **re-validate**. The validator runs on its configured default
   (latest Sonnet — see below).

### Terminal states

- Re-validation `"pass"` → `completed`. The escalation is recorded in the
  job's phase/run history (which phases re-ran, on what models).
- Re-validation still `"fail"`, **or** every flagged phase is already at
  `opus`, **or** the one auto-escalation has already been used → `paused`
  with a clear message: *"QA failed — review or retry on a stronger model,"*
  including the per-phase flags. The job is now visibly not-`completed`,
  which is what eliminates the original silent failure.
- Catalog fetch fails (network/parse error): log and fall through to the
  `paused`/suggest terminal state rather than blocking the job.

### Manual retry after pause

The editor's manual retry on a `paused` QA-failed job bumps each
not-yet-`opus` phase **one more family** (so phases that went `haiku →
sonnet` on the auto attempt go `sonnet → opus` on the click); phases already
at `opus` stay. `opus` is the ceiling; there are no further auto-bumps.

### "Escalate once" guard

A persisted marker on the job records that an automatic QA-escalation has
occurred (e.g. an `auto_escalated_at` timestamp, or reuse of existing
per-phase tier/retry tracking). The escalation loop checks this so neither
the loop nor a later manual retry re-triggers the automatic path.

### Validator default

Move the `validator` phase off the cheapskate tier to the default
(Sonnet 4.6 — currently the newest Sonnet in the catalog). Configurable.
A stronger judge is both more trustworthy and less likely to fail a job on
noise, which is what lets the auto-escalate loop actually converge.

### Configuration

New block in `config/llm-config.json` (family-first, no tier labels):

```jsonc
"qa_escalation": {
  "on_validation_fail": true,        // master switch
  "max_auto_escalations": 1,         // "once"
  "family_order": ["haiku", "sonnet", "opus"],
  "exclude_variants": ["fast", "fable"]
}
```

Plus the one-line change moving `validator` in `phase_backends` to the
default (Sonnet) tier.

## Folded-in data-integrity fixes

These are in scope because the escalation logic depends on them.

1. **`phases[].model` must record the model that actually ran.** On job 10
   the job record reported the validator on Haiku while
   `validator_output.md`'s header reported Sonnet 4.6 — two validator passes
   with only one recorded. Family parsing for escalation depends on this
   field being correct.
2. **`current_phase` must advance through the final phase.** Job 10's
   `current_phase` was left at `"seo"` even though `timestamp` ran and
   completed afterward. Cosmetic, but a paused/suggested job should display
   the correct phase.

## What "OpenRouter provides" (reference)

The OpenRouter catalog (`/api/v1/models`, public, no key) does **not**
expose a capability rank or successor pointer. It does expose, per model:
`id` (family parseable from the slug), `created` (pick newest in family),
`pricing`, `context_length`, `architecture`, `benchmarks`,
`knowledge_cutoff`, and more. Pricing is **not** a reliable ladder on its
own — `opus-*-fast` variants ($10–30/Mtok) and `fable-5` ($10/Mtok) price
above plain `opus` ($5/Mtok), so a "next most expensive" rule would jump to
the wrong model. Hence family parsing + an explicit family order, with
`-fast` and `fable` excluded.

## Testing

**Unit**
- Family parse from varied slugs (`claude-sonnet-4.6`,
  `claude-4.5-haiku-20251001`, `claude-opus-4.8`).
- Family bump + `opus` clamp.
- `-fast` / `fable` exclusion in catalog filtering.
- "Escalate once" guard prevents re-triggering.
- Earliest-flagged-phase + downstream selection.
- Catalog-fetch-failure falls back to pause/suggest.

**Integration** (LLM and catalog stubbed — no spend, no live network)
- Stubbed validator returning `fail` then `pass` ⇒ job ends `completed`
  with the escalation recorded.
- Stubbed validator returning persistent `fail` ⇒ job ends `paused` with the
  suggest message and per-phase flags.
- Flagged phase already at `opus` ⇒ straight to `paused`, no escalation
  attempt.

## Open questions

None outstanding. Defaults for manual-retry behavior and `-fast`/`fable`
exclusion were accepted during design.
