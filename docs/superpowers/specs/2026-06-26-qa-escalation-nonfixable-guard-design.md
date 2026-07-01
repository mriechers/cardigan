# QA-escalation guard for non-model-fixable failures (#276)

**Date:** 2026-06-26
**Issue:** [#276](https://github.com/mriechers/cardigan/issues/276)
**Status:** Design approved — pending implementation plan

## Problem

The QA-fail auto-escalation gate (`worker.py:_finalize_with_qa_gate`, Spec B / #243)
escalates on *any* validator `overall: "fail"` without inspecting **why** it failed.
Some QA failures cannot be fixed by a stronger model:

- **Formatter review notes / `needs_review`** — the formatter is *designed* to surface
  uncertainties it cannot resolve (missing media_id, unverified proper-noun spelling)
  as an HTML `<!-- REVIEW NOTES -->` block + `**Status:** needs_review`. The validator
  treats these as a hard fail. A stronger model, doing its job well, re-emits the same
  honest notes. This is the documented formatter↔validator contract conflict
  (`docs/superpowers/specs/2026-06-18-qa-failure-auto-escalation-design.md`).
- **Missing input data** (e.g. media_id absent from the manifest) — no model can
  invent absent data.

### Live evidence (cardigan01)

- **Job 13** (`2WLIJingleDressesSM`, media_id null): failed QA on review-notes →
  escalated formatter to **Opus 4.8** → Opus re-emitted the notes → re-failed →
  paused. Cost ~$0.11 (≈2× a clean run), entirely wasted, and the pause message
  (*"retry on a stronger model"*) actively misdirected diagnosis.
- **Job 14** (same transcript, media_id populated): also escalated (Sonnet first
  pass trips the validator), but Opus happened to return clean output → completed.
  Same ~$0.11. So escalation fires on essentially every job of this shape — for
  Job 13 it was guaranteed-futile spend.

## Goal

Insert a classification step *before* escalation. When a job's failing QA flags are
**all** non-model-fixable, skip the escalation pass entirely and go straight to a
**cheap, honest pause for human review** — preserving the editorial checkpoint while
removing both the wasted Opus pass and the misleading "retry on a stronger model"
message.

**Non-goal (stays in #276 as follow-up):** resolving the deeper formatter↔validator
contract — i.e. moving review notes to a sidecar field, or letting the validator
emit a soft `needs_human_review` outcome so such jobs *complete* instead of pause.
This spec keeps the terminal state as **pause** (per product decision 2026-06-26).

## Approach (chosen: deterministic artifact + flag-pattern inspection)

Considered three classification mechanisms:

| Approach | Summary | Verdict |
|----------|---------|---------|
| **A. Deterministic artifact + flag patterns** | Pure function checks phase-output markers + a curated non-fixable flag-text allowlist | **Chosen** — deterministic, no LLM cost, no schema change, fails safe, unit-testable |
| B. Validator emits `model_fixable` per flag | Validator self-classifies in JSON | Rejected — relies on haiku judgment (new failure mode), large blast radius (prompt+schema+parser+tests) |
| C. Formatter-marker only | Detect review-notes/needs_review in formatter output only | Rejected — strict subset of A; misses non-formatter cases |

**Why A:** it fails *safe* — only **known** non-fixable patterns take the cheap path;
any novel/unrecognized flag still escalates exactly as today, so there is no behavior
regression for failure classes we haven't characterized.

## Design

### 1. New pure function — `api/services/escalation.py`

```
classify_qa_failure(validation_result: dict, context: dict) -> dict
    -> {"escalate": bool, "nonfixable": list[str], "fixable": list[str]}
```

- Collect every failing flag across phases (a phase contributes when
  `status == "fail"` or it carries a non-empty `flags` list).
- A flag is **non-fixable** if EITHER:
  - its text matches a curated substring pattern (case-insensitive):
    `NONFIXABLE_FLAG_PATTERNS` — review-notes/`needs_review`/`media_id`, plus the
    caption-quality / verification-reminder vocabulary observed on Here & Now web
    clips: `unresolved`, `not identified`, `unidentified`, `cannot be determined`,
    `unverified`, `unconfirmed`, `semrush`, `excerpt`, OR
  - the corresponding `context["{phase}_output"]` contains a formatter contract
    marker: `<!-- REVIEW NOTES` or `**Status:** needs_review` (also matched loosely
    as `status: needs_review`).
- `escalate = not nonfixable`. **Skip escalation whenever ANY failing flag is
  non-fixable** — a job passes only if *every* flag clears, so a single non-fixable
  flag makes escalation futile no matter how many fixable flags ride along.
- **Fail safe:** empty/missing `validation_result`, no failing flags, or all-fixable
  → `escalate = True` (give the stronger model a shot; preserve today's behavior).

> **Revision (2026-06-27):** the original rule skipped escalation *only when ALL*
> flags were non-fixable, so *mixed* failures still escalated. Live jobs 15–19
> (Here & Now web clips) proved that wrong — each had a non-fixable flag beside a
> fixable one, escalated to Opus, and *still failed*, wasting ~$0.10–0.12 apiece.
> The rule is now "skip if ANY non-fixable flag," which is strictly better: mixed
> jobs pause cheaply instead of escalate-then-pause, while all-fixable jobs still
> escalate. The pattern list was broadened with the caption-quality vocabulary so
> jobs lacking a literal review-notes line are still recognized.

Pure, side-effect-free, no I/O — trivially unit-testable.

### 2. Gate change — `worker.py:_finalize_with_qa_gate`

Insert after the `overall == "fail"` check and the already-escalated guard, but
**before** the escalation loop:

```
if cfg.get("skip_escalation_when_nonfixable", True):
    verdict = classify_qa_failure(validation_result, context)
    if not verdict["escalate"]:
        await pause_and_suggest(
            job_id,
            trigger="qa_review",
            message=_nonfixable_review_message(verdict["nonfixable"]),
            mark_escalated=True,
        )
        return "paused"
```

All-fixable or unrecognized failures (no non-fixable flag) fall through to the
existing escalation path unchanged. `mark_escalated=True` keeps a later manual
resume from re-entering escalation.

### 3. Honest messaging + distinct trigger

- Trigger `qa_review` (not `qa_fail`) so the error reads as a human-review request.
- Message names the actual items, e.g.:
  `[qa_review] Paused for human review — the formatter flagged items it can't verify
  and a stronger model won't resolve: <flags>. Verify media_id + proper-noun spelling,
  then resume.`
- **Compatibility:** audit any code/UI that keys on the `qa_fail` prefix (web
  dashboard status rendering, `pause_and_suggest` callers, tests) and ensure
  `qa_review` is handled equivalently (treated as a paused/needs-attention state).

### 4. Config — `config/llm-config.json`

Add one knob under `qa_escalation`:

```json
"qa_escalation": {
  "on_validation_fail": true,
  "max_auto_escalations": 1,
  "skip_escalation_when_nonfixable": true,
  "family_order": ["haiku", "sonnet", "opus"],
  "exclude_variants": ["fast", "fable"]
}
```

Default `true`. Lets the guard be disabled without a code change if it ever
misclassifies. (This is *not* a pause-vs-complete switch — that option was declined.)

## Testing

### Unit (`tests/` — pure function)
`classify_qa_failure`:
- review-notes-only flag → `escalate=False`
- `needs_review`-only (via flag text) → `escalate=False`
- formatter output carries `<!-- REVIEW NOTES` but flag text is vague → `escalate=False` (artifact path)
- mixed: review-note + a truncation flag → `escalate=True`
- truncation/garbled-only → `escalate=True`
- empty/missing validation_result → `escalate=True` (fail safe)

### Integration (`tests/integration/test_escalation_e2e.py`)
Per the worker-test blind-spot lesson, the worker's own unit tests stub the seams;
the e2e harness (real `process_job` + real SQLite, only the LLM HTTP boundary mocked)
is the correct place to assert escalation behavior. Add a case:
- Validator returns `overall: "fail"` with **only** a review-notes flag.
- Assert: job ends `paused`; **no escalated phase re-run occurred** (single-pass cost;
  no Opus/stronger-family call); `error_message` starts with `[qa_review]`.
- Keep an existing/added case where a *fixable* flag still escalates, to prove the
  fall-through path is intact.

## Files touched

- `api/services/escalation.py` — add `classify_qa_failure` + `_nonfixable_review_message` (or inline message builder) + `NONFIXABLE_FLAG_PATTERNS`.
- `api/services/worker.py` — insert the guard branch in `_finalize_with_qa_gate`.
- `config/llm-config.json` — add `skip_escalation_when_nonfixable`.
- `tests/` — unit tests for the classifier; e2e case in `test_escalation_e2e.py`.
- Web/UI — only if something keys on the `qa_fail` error prefix (audit during impl).

## Risks & mitigations

- **Keyword brittleness** → primary signal is the deterministic artifact marker; the
  pattern list is a secondary net; unknown phrasings fail safe (escalate).
- **Over-skipping** (a genuinely fixable failure mislabeled non-fixable) → only the
  narrow curated patterns + explicit formatter markers qualify; everything else
  escalates. Config flag allows instant disable.
- **`qa_review` trigger breaking downstream consumers** → audited and handled during
  implementation; treated as an equivalent paused state.
