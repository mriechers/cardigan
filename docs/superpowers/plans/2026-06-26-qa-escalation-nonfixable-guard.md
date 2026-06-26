# QA-escalation Guard for Non-Model-Fixable Failures — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the QA auto-escalation gate from burning a futile Opus pass on failures a stronger model cannot fix; route them to a cheap, honest "paused for human review" state instead.

**Architecture:** Add a pure classifier (`classify_qa_failure`) to `api/services/escalation.py` that inspects validator flags + first-pass phase outputs. Wire it into `worker.py:_finalize_with_qa_gate` so that when ALL failing flags are non-model-fixable, the gate pauses (trigger `qa_review`) before escalating. A config flag gates the behavior. Failures fall back to today's escalation path otherwise.

**Tech Stack:** Python 3.13, FastAPI, SQLite (aiosqlite), pytest + pytest-asyncio, ruff.

## Global Constraints

- Python type hints required on all new functions.
- ruff clean (`ruff check api/ tests/`).
- The classifier MUST fail safe: unknown/empty input → `escalate=True` (no regression).
- Skip escalation ONLY when every failing flag is non-model-fixable.
- Do not change the terminal state to "completed" — paused only (contract resolution is deferred, out of scope).
- Commit attribution footer on every commit:
  `[Agent: Main Assistant]` then the `Co-Authored-By` / `Claude-Session` trailers used in this repo.
- Run all commands from the worktree: `/Users/mriechers/Developer/pbswi/cardigan-v4/.worktrees/fix-276-escalation-guard`.

---

## File Structure

- **Modify** `api/services/escalation.py` — add `NONFIXABLE_FLAG_PATTERNS`, `FORMATTER_CONTRACT_MARKERS`, `classify_qa_failure()`, `nonfixable_review_message()`. Pure functions, no I/O.
- **Modify** `api/services/worker.py` — import the two new functions; insert the guard branch in `_finalize_with_qa_gate`.
- **Modify** `config/llm-config.json` — add `skip_escalation_when_nonfixable: true` under `qa_escalation`.
- **Modify** `tests/services/test_escalation.py` — unit tests for the classifier + message + config key.
- **Modify** `tests/integration/test_escalation_e2e.py` — add a `nonfixable` mock scenario + e2e test asserting no escalation.

---

### Task 1: Pure classifier + honest message in `escalation.py`

**Files:**
- Modify: `api/services/escalation.py`
- Test: `tests/services/test_escalation.py`

**Interfaces:**
- Produces:
  - `NONFIXABLE_FLAG_PATTERNS: list[str]`
  - `FORMATTER_CONTRACT_MARKERS: list[str]`
  - `classify_qa_failure(validation_result: dict | None, context: dict | None = None) -> dict` returning `{"escalate": bool, "fixable": list[str], "nonfixable": list[str]}`
  - `nonfixable_review_message(nonfixable: list[str]) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/services/test_escalation.py`:

```python
from api.services.escalation import classify_qa_failure, nonfixable_review_message


def _vr(formatter_flags=None, seo_flags=None):
    return {
        "overall": "fail",
        "phase_results": {
            "analyst": {"status": "pass", "flags": []},
            "formatter": {"status": "fail" if formatter_flags else "pass", "flags": formatter_flags or []},
            "seo": {"status": "fail" if seo_flags else "pass", "flags": seo_flags or []},
        },
    }


def test_classify_review_notes_only_skips():
    out = classify_qa_failure(_vr(formatter_flags=["Review notes appear in transcript body"]), {})
    assert out["escalate"] is False
    assert out["nonfixable"] and not out["fixable"]


def test_classify_needs_review_text_skips():
    out = classify_qa_failure(_vr(formatter_flags=["Status field 'needs_review' indicates incomplete processing"]), {})
    assert out["escalate"] is False


def test_classify_artifact_marker_skips_vague_flag():
    # Flag text is vague, but the formatter OUTPUT carries the contract marker.
    vr = _vr(formatter_flags=["something is off"])
    ctx = {"formatter_output": "# Formatted Transcript\n<!-- REVIEW NOTES:\n- verify spelling\n-->\n"}
    out = classify_qa_failure(vr, ctx)
    assert out["escalate"] is False


def test_classify_mixed_escalates():
    out = classify_qa_failure(
        _vr(formatter_flags=["Review notes appear in transcript body"], seo_flags=["title exceeds 60 characters"]),
        {},
    )
    assert out["escalate"] is True
    assert out["fixable"] and out["nonfixable"]


def test_classify_truncation_only_escalates():
    out = classify_qa_failure(_vr(formatter_flags=["content ends abruptly mid-sentence (truncation)"]), {})
    assert out["escalate"] is True


def test_classify_empty_failsafe_escalates():
    assert classify_qa_failure({}, {})["escalate"] is True
    assert classify_qa_failure(None, None)["escalate"] is True


def test_nonfixable_review_message_includes_flags():
    msg = nonfixable_review_message(["Review notes appear in transcript body"])
    assert "human review" in msg.lower()
    assert "Review notes appear in transcript body" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mriechers/Developer/pbswi/cardigan-v4/.worktrees/fix-276-escalation-guard && python -m pytest tests/services/test_escalation.py -k "classify or nonfixable_review" -v`
Expected: FAIL with `ImportError: cannot import name 'classify_qa_failure'`.

- [ ] **Step 3: Write the implementation**

Append to `api/services/escalation.py`:

```python
# Flag-text substrings (case-insensitive) that denote a failure a stronger
# model cannot fix — editorial review notes or missing input data.
NONFIXABLE_FLAG_PATTERNS = [
    "review note",
    "needs_review",
    "needs review",
    "media id",
    "media_id",
]

# Markers the formatter writes into its OWN output when it surfaces an
# unresolved uncertainty. Their presence means the failure is a contract /
# editorial signal, not a model-quality defect. Matched case-insensitively.
FORMATTER_CONTRACT_MARKERS = [
    "<!-- review notes",
    "status:** needs_review",
    "status: needs_review",
]


def classify_qa_failure(validation_result: dict | None, context: dict | None = None) -> dict:
    """Split a failing validation_result's flags into model-fixable vs not.

    A flag is non-fixable when its text matches NONFIXABLE_FLAG_PATTERNS, or
    when the corresponding ``context["{phase}_output"]`` carries a formatter
    contract marker. Escalation is skipped only when EVERY failing flag is
    non-fixable. Empty/unknown input fails safe -> escalate=True.
    """
    context = context or {}
    results = (validation_result or {}).get("phase_results", {})
    fixable: list[str] = []
    nonfixable: list[str] = []

    for phase_name, r in results.items():
        flags = r.get("flags") or []
        if r.get("status") != "fail" and not flags:
            continue
        output = (context.get(f"{phase_name}_output") or "").lower()
        artifact_nonfixable = any(m in output for m in FORMATTER_CONTRACT_MARKERS)
        if not flags:
            # Phase failed with no flag text — only treat as non-fixable if the
            # artifact itself shows a contract marker; otherwise escalate.
            (nonfixable if artifact_nonfixable else fixable).append(f"{phase_name}: output failed")
            continue
        for flag in flags:
            ftext = (flag or "").lower()
            is_nonfixable = artifact_nonfixable or any(p in ftext for p in NONFIXABLE_FLAG_PATTERNS)
            (nonfixable if is_nonfixable else fixable).append(flag)

    escalate = bool(fixable) or not nonfixable
    return {"escalate": escalate, "fixable": fixable, "nonfixable": nonfixable}


def nonfixable_review_message(nonfixable: list[str]) -> str:
    """Build the honest pause message naming the human-review items."""
    items = "; ".join(nonfixable) if nonfixable else "items the formatter could not verify"
    return (
        "Paused for human review — the formatter flagged items it can't verify "
        f"and a stronger model won't resolve: {items}. "
        "Verify media_id + proper-noun spelling, then resume."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mriechers/Developer/pbswi/cardigan-v4/.worktrees/fix-276-escalation-guard && python -m pytest tests/services/test_escalation.py -v && ruff check api/services/escalation.py`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
cd /Users/mriechers/Developer/pbswi/cardigan-v4/.worktrees/fix-276-escalation-guard
git add api/services/escalation.py tests/services/test_escalation.py
git commit -F - <<'EOF'
feat(#276): classify_qa_failure — split QA flags into model-fixable vs not

Pure, fail-safe classifier + honest review message. Detects formatter
review-notes / needs_review / missing-media_id via flag text and the
formatter's own output markers. No I/O; unit-tested.

[Agent: Main Assistant]

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BWTYJ8ZxjZkHsaA9ftX9se
EOF
```

---

### Task 2: Wire the guard into the gate + config flag + e2e test

**Files:**
- Modify: `config/llm-config.json`
- Modify: `api/services/worker.py` (`_finalize_with_qa_gate`, ~line 1383–1388; import line near other `escalation` imports)
- Test: `tests/integration/test_escalation_e2e.py`
- Test: `tests/services/test_escalation.py` (config-key assertion)

**Interfaces:**
- Consumes: `classify_qa_failure`, `nonfixable_review_message` from Task 1; existing `pause_and_suggest`.

- [ ] **Step 1: Add the config flag**

In `config/llm-config.json`, the `qa_escalation` block becomes:

```json
  "qa_escalation": {
    "on_validation_fail": true,
    "max_auto_escalations": 1,
    "skip_escalation_when_nonfixable": true,
    "family_order": ["haiku", "sonnet", "opus"],
    "exclude_variants": ["fast", "fable"]
  },
```

- [ ] **Step 2: Write the failing e2e test (and config assertion)**

In `tests/integration/test_escalation_e2e.py`, update the validator branch of the mock handler (`_make_handler`, the `if is_validator:` block) so the `nonfixable` scenario flags the formatter:

```python
            if is_validator:
                state.validator_calls += 1
                if state.scenario in ("persistfail", "nonfixable"):
                    overall = "fail"
                else:
                    overall = "fail" if state.validator_calls == 1 else "pass"
                if state.scenario == "nonfixable":
                    phase_results = {
                        "analyst": {"status": "pass", "flags": []},
                        "formatter": {"status": "fail", "flags": ["Review notes appear in transcript body"]},
                        "seo": {"status": "pass", "flags": []},
                    }
                else:
                    phase_results = {
                        "analyst": {"status": "pass", "flags": []},
                        "formatter": {"status": "pass", "flags": []},
                        "seo": {
                            "status": "fail" if overall == "fail" else "pass",
                            "flags": ["weak keyword density"] if overall == "fail" else [],
                        },
                    }
                content = json.dumps({"overall": overall, "phase_results": phase_results})
```

Append the new test at the end of the file:

```python
@pytest.mark.asyncio
async def test_nonfixable_skips_escalation(e2e_env):
    """Review-notes-only QA fail -> paused (qa_review) WITHOUT an escalation pass."""
    tmp_path, cfg, cfg_path = e2e_env
    j, state = await _run_scenario("nonfixable", tmp_path, cfg, cfg_path)

    assert j.status.value == "paused", f"expected paused, got {j.status!r} / {j.error_message!r}"
    assert j.error_message is not None and j.error_message.startswith(
        "[qa_review]"
    ), f"error_message must start with [qa_review], got {j.error_message!r}"
    # No escalation: validator ran exactly once (no re-validation); single pipeline pass.
    assert state.validator_calls == 1, f"expected 1 validator call, got {state.validator_calls}: {state.phase_log}"
    assert state.all_calls < 6, f"expected single-pass (<6 calls), got {state.all_calls}: {state.phase_log}"
    assert j.auto_escalated_at is not None, "mark_escalated must stamp the marker (prevents resume re-loop)"
```

In `tests/services/test_escalation.py`, extend `test_config_has_qa_escalation_and_sonnet_validator` with:

```python
    assert qa["skip_escalation_when_nonfixable"] is True
```

- [ ] **Step 3: Run the e2e test to verify it fails**

Run: `cd /Users/mriechers/Developer/pbswi/cardigan-v4/.worktrees/fix-276-escalation-guard && python -m pytest tests/integration/test_escalation_e2e.py::test_nonfixable_skips_escalation -v`
Expected: FAIL — without the guard the job escalates, so `validator_calls == 2` (assertion fails) and/or status is `completed`/`paused` with `[qa_fail]`.

- [ ] **Step 4: Implement the guard in the gate**

In `api/services/worker.py`, add the imports alongside the existing escalation imports (find the line importing `pause_and_suggest, select_escalation_phases` / `resolve_escalated_model`) — include the two new names:

```python
from api.services.escalation import (
    apply_escalated_phase_models,
    classify_qa_failure,
    nonfixable_review_message,
    pause_and_suggest,
    resolve_escalated_model,
    select_escalation_phases,
)
```
*(Match the existing import style — if these are currently imported on separate lines, add two new `from api.services.escalation import classify_qa_failure, nonfixable_review_message` lines instead. Verify with `grep -n "from api.services.escalation" api/services/worker.py` first.)*

Then in `_finalize_with_qa_gate`, insert the guard immediately AFTER the already-escalated check (the block ending `return "paused"` at ~line 1382) and BEFORE the `phases = [p for p in select_escalation_phases(...)]` line:

```python
        # Non-model-fixable guard (#276): if every failing flag is something a
        # stronger model cannot fix (formatter review-notes / needs_review /
        # missing media_id), skip the futile escalation pass and route straight
        # to a cheap, honest human-review pause.
        if cfg.get("skip_escalation_when_nonfixable", True):
            classed = classify_qa_failure(validation_result, context)
            if not classed["escalate"]:
                await pause_and_suggest(
                    job_id,
                    trigger="qa_review",
                    message=nonfixable_review_message(classed["nonfixable"]),
                    mark_escalated=True,
                )
                return "paused"
```

- [ ] **Step 5: Run the full escalation suite to verify pass + no regression**

Run: `cd /Users/mriechers/Developer/pbswi/cardigan-v4/.worktrees/fix-276-escalation-guard && python -m pytest tests/integration/test_escalation_e2e.py tests/services/test_escalation.py tests/test_worker.py -v && ruff check api/ tests/`
Expected: all PASS (including the existing `test_fail_escalate_pass`, `test_persistent_fail`, and worker gate tests — proving the fall-through escalation path is intact), ruff clean.

- [ ] **Step 6: Commit**

```bash
cd /Users/mriechers/Developer/pbswi/cardigan-v4/.worktrees/fix-276-escalation-guard
git add api/services/worker.py config/llm-config.json tests/integration/test_escalation_e2e.py tests/services/test_escalation.py
git commit -F - <<'EOF'
feat(#276): skip QA escalation for non-model-fixable failures

The gate now classifies a validator fail before escalating. When every
failing flag is non-fixable (review-notes / needs_review / missing
media_id), it pauses with an honest [qa_review] message instead of
burning an Opus pass that re-fails. Mixed/fixable failures still escalate.
Gated by qa_escalation.skip_escalation_when_nonfixable (default true).

e2e: review-notes-only fail -> paused, single validator call, no re-run.

[Agent: Main Assistant]

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BWTYJ8ZxjZkHsaA9ftX9se
EOF
```

---

## Compatibility note (verified during planning)

`trigger="qa_fail"` is used only in `worker.py` pause paths; `pause_and_suggest`
formats it into the `error_message` display string (`[{trigger}] {message}`).
No code or UI branches on the prefix (`web/src` has zero `qa_fail` references;
`statusColors.ts` keys on job *status*, not error text). The new `qa_review`
trigger is therefore display-only and safe; it renders under the existing
`paused` status treatment. No UI change required.

## Self-Review

- **Spec coverage:** classifier (Task 1) ✓; gate guard + config (Task 2) ✓; honest `qa_review` message (Task 1 fn + Task 2 wiring) ✓; unit + e2e tests ✓; fail-safe default ✓; pause-not-complete preserved ✓.
- **Placeholders:** none — every step has full code/commands.
- **Type consistency:** `classify_qa_failure(validation_result, context) -> {"escalate","fixable","nonfixable"}` used identically in tests and gate; `nonfixable_review_message(list[str]) -> str` consistent; config key `skip_escalation_when_nonfixable` identical in config, gate, and test.
