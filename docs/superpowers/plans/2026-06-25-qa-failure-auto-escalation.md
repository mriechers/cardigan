# QA-Failure Auto-Escalation (Spec B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A job whose validator returns `overall: "fail"` auto-re-runs the weak phases once on a stronger model family and only pauses for the editor if it still fails — and OpenRouter credit exhaustion surfaces a clear, actionable pause instead of failing silently.

**Architecture:** Add a shared `pause_and_suggest` terminal helper plus pure family/escalation logic in a new `api/services/escalation.py`. Wire three triggers in `api/services/worker.py`: QA-fail (new), credit-exhaustion (new typed error from `llm.py`), and truncation (migrate existing onto the shared helper). Target-model resolution reuses the existing `api/services/model_roster.py` catalog (fetch + 1h cache + family classification already shipped in Spec A / #235).

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy (async, SQLite), pytest + pytest-asyncio. OpenRouter via `httpx`. Lint: `ruff` 0.15.18 + `black` 26.5.1 (CI-pinned).

## Global Constraints

- Spec A is a hard prerequisite and is **already merged** (#235): config selection takes effect on the worker, the chunked formatter honors `model_override`, and `phases[].model` is accurate. Build on it; do not re-implement it.
- The chunked-formatter chunk-0 false-truncation bug (job 12) is fixed separately (PR #264) — escalation logic must not re-introduce a dependence on chunk-0 review notes.
- Family order is exactly `["haiku", "sonnet", "opus"]`; `opus` is terminal (no bump beyond it).
- Target-model resolution excludes variants `["fast", "fable"]` and selects the **newest** `anthropic/*` in the target family by OpenRouter `created`.
- Auto-escalation runs **at most once** per job (`max_auto_escalations: 1`), guarded by a persisted `auto_escalated_at` marker.
- Credit exhaustion **never** consumes a retry and is **never** swallowed by the optional-phase handler.
- Catalog-fetch failure must **fall through to pause-and-suggest**, never crash the job.
- All new code passes `ruff check` and `black --check` at the CI-pinned versions.
- The pipeline runs live on `cardigan01:8100`; every trigger must leave the job in a visible non-`completed` state, never a silent green.

---

## File Structure

- `api/services/escalation.py` *(new)* — pure helpers (`parse_model_family`, `bump_family`, `select_escalation_phases`) + async `resolve_escalated_model` (wraps `model_roster`) + async `pause_and_suggest` terminal helper. One responsibility: "what to do when a phase's output isn't good enough."
- `api/services/llm.py` *(modify)* — add `CreditExhaustedError`; detect 402 / credit body in `_call_openrouter`.
- `api/services/model_roster.py` *(modify)* — add `newest_in_family(family, exclude_variants)` using the already-cached roster + raw `created` timestamps.
- `api/services/worker.py` *(modify)* — wire Trigger A (QA-fail before the unconditional `completed`), Trigger B (route `CreditExhaustedError` to pause-and-suggest), Trigger C (migrate truncation onto `pause_and_suggest`).
- `api/models/job.py` + `api/services/database.py` *(modify)* — add the `auto_escalated_at` column/field + set it via the existing `update_job` path.
- `config/llm-config.json` *(modify)* — add the `qa_escalation` block; move `validator` to the default (Sonnet) backend.
- Tests: `tests/services/test_escalation.py` *(new)*, `tests/test_llm.py` *(modify/new)*, `tests/services/test_model_roster.py` *(modify)*, `tests/test_worker.py` *(new — integration triggers)*.

---

## Task 1: Model family parse + bump (pure functions)

**Files:**
- Create: `api/services/escalation.py`
- Test: `tests/services/test_escalation.py`

**Interfaces:**
- Produces: `parse_model_family(model_slug: str | None) -> str | None` (returns `"haiku" | "sonnet" | "opus" | None`); `bump_family(family: str | None) -> str | None` (returns next family, or `None` if `opus`/unknown).

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_escalation.py
import pytest
from api.services.escalation import parse_model_family, bump_family


@pytest.mark.parametrize("slug,expected", [
    ("anthropic/claude-4.5-haiku-20251001", "haiku"),
    ("anthropic/claude-4.6-sonnet-20260217", "sonnet"),
    ("anthropic/claude-sonnet-4.6", "sonnet"),       # word order varies
    ("anthropic/claude-opus-4-8", "opus"),
    ("openai/gpt-4o", None),
    (None, None),
    ("", None),
])
def test_parse_model_family(slug, expected):
    assert parse_model_family(slug) == expected


@pytest.mark.parametrize("family,expected", [
    ("haiku", "sonnet"),
    ("sonnet", "opus"),
    ("opus", None),        # terminal
    (None, None),
    ("mystery", None),
])
def test_bump_family(family, expected):
    assert bump_family(family) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/services/test_escalation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.services.escalation'`

- [ ] **Step 3: Write minimal implementation**

```python
# api/services/escalation.py
"""QA-failure escalation + shared pause-and-suggest terminal handling (Spec B)."""

from __future__ import annotations

FAMILY_ORDER = ["haiku", "sonnet", "opus"]


def parse_model_family(model_slug: str | None) -> str | None:
    """Return 'haiku' | 'sonnet' | 'opus' parsed from a model slug, else None.

    Robust to OpenRouter's mixed word order (claude-4.6-sonnet vs claude-sonnet-4.6).
    """
    if not model_slug:
        return None
    s = model_slug.lower()
    for family in FAMILY_ORDER:
        if family in s:
            return family
    return None


def bump_family(family: str | None) -> str | None:
    """Return the next-stronger family, or None if already opus / unknown."""
    if family not in FAMILY_ORDER:
        return None
    idx = FAMILY_ORDER.index(family)
    return FAMILY_ORDER[idx + 1] if idx + 1 < len(FAMILY_ORDER) else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/services/test_escalation.py -q`
Expected: PASS (12 cases)

- [ ] **Step 5: Commit**

```bash
git add api/services/escalation.py tests/services/test_escalation.py
git commit -m "feat(escalation): model-family parse + bump helpers (#243)"
```

---

## Task 2: Newest-in-family catalog resolution

**Files:**
- Modify: `api/services/model_roster.py` (add `newest_in_family` after `get_available_models`, ~line 170)
- Test: `tests/services/test_model_roster.py`

**Interfaces:**
- Consumes: existing `fetch_openrouter_models() -> Optional[List[dict]]` (each dict has `id` and `created`), `CACHE_TTL_SECONDS`.
- Produces: `async newest_in_family(family: str, exclude_variants: list[str]) -> str | None` — newest `anthropic/<...family...>` model id by `created`, excluding any id containing an excluded variant token; `None` on fetch failure or no match.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_model_roster.py  (add to existing file)
import pytest
from unittest.mock import AsyncMock, patch
from api.services import model_roster


@pytest.mark.asyncio
async def test_newest_in_family_picks_newest_excludes_variants():
    raw = [
        {"id": "anthropic/claude-opus-4-6", "created": 100},
        {"id": "anthropic/claude-opus-4-8", "created": 300},      # newest opus
        {"id": "anthropic/claude-opus-4-8-fast", "created": 400}, # excluded (fast)
        {"id": "anthropic/claude-fable-5", "created": 500},       # excluded (fable)
        {"id": "anthropic/claude-sonnet-4-6", "created": 200},
        {"id": "openai/gpt-4o", "created": 999},                  # wrong provider
    ]
    with patch.object(model_roster, "fetch_openrouter_models", AsyncMock(return_value=raw)):
        got = await model_roster.newest_in_family("opus", ["fast", "fable"])
    assert got == "anthropic/claude-opus-4-8"


@pytest.mark.asyncio
async def test_newest_in_family_none_on_fetch_failure():
    with patch.object(model_roster, "fetch_openrouter_models", AsyncMock(return_value=None)):
        assert await model_roster.newest_in_family("opus", ["fast"]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/services/test_model_roster.py -q -k newest_in_family`
Expected: FAIL — `AttributeError: module 'api.services.model_roster' has no attribute 'newest_in_family'`

- [ ] **Step 3: Write minimal implementation**

```python
# api/services/model_roster.py  (append near the other module functions)
async def newest_in_family(family: str, exclude_variants: list) -> Optional[str]:
    """Newest anthropic/* model id in `family` by OpenRouter `created`, excluding
    any id containing an excluded variant token (e.g. 'fast', 'fable').

    Returns None on fetch failure or no match — callers fall through to
    pause-and-suggest rather than guessing.
    """
    raw = await fetch_openrouter_models()
    if not raw:
        return None
    family = family.lower()
    candidates = []
    for m in raw:
        mid = (m.get("id") or "").lower()
        if not mid.startswith("anthropic/") or family not in mid:
            continue
        if any(v.lower() in mid for v in exclude_variants):
            continue
        candidates.append((m.get("created") or 0, m["id"]))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/services/test_model_roster.py -q -k newest_in_family`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/model_roster.py tests/services/test_model_roster.py
git commit -m "feat(model_roster): newest_in_family resolution for escalation (#243)"
```

---

## Task 3: CreditExhaustedError + 402 detection

**Files:**
- Modify: `api/services/llm.py` (error classes ~line 44; `_call_openrouter` error branch ~line 695)
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces: `class CreditExhaustedError(Exception)` with attrs `detail: str`, `backend: str | None`. Raised from `_call_openrouter` on HTTP 402 or a body indicating credit/quota exhaustion, **before** `raise_for_status()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm.py  (add)
import pytest
from api.services.llm import CreditExhaustedError, LLMClient


@pytest.mark.asyncio
async def test_call_openrouter_raises_credit_exhausted_on_402(monkeypatch):
    client = LLMClient.__new__(LLMClient)
    client.active_backend = "openrouter"

    class FakeResp:
        status_code = 402
        text = '{"error":{"message":"Insufficient credits"}}'
        def json(self):
            return {"error": {"message": "Insufficient credits"}}

    # _post_openrouter is the seam that performs the HTTP call; patch it to return 402.
    monkeypatch.setattr(client, "_post_openrouter", lambda *a, **k: FakeResp())

    with pytest.raises(CreditExhaustedError):
        await client._call_openrouter(model="anthropic/x", messages=[], job_id=1, phase="seo")
```

> NOTE: Step 3 introduces the `_post_openrouter` seam (extract the `httpx` POST out of `_call_openrouter`) so the HTTP boundary is patchable without a live network. If the codebase already isolates the POST, patch that instead and drop the extraction.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm.py -q -k credit_exhausted`
Expected: FAIL — `ImportError: cannot import name 'CreditExhaustedError'`

- [ ] **Step 3: Write minimal implementation**

```python
# api/services/llm.py — add near the other typed errors (after BackendUnavailableError)
class CreditExhaustedError(Exception):
    """OpenRouter reports insufficient credit/quota (HTTP 402 or credit body).

    Never swallowed (even in an optional phase) and never consumes a retry —
    the worker routes it to pause-and-suggest: 'add credit, then retry'.
    """

    def __init__(self, detail: str, backend: str | None = None):
        super().__init__(detail)
        self.detail = detail
        self.backend = backend
```

```python
# api/services/llm.py — in _call_openrouter, replace the existing error-log block
# (around line 695, the `if response.status_code >= 400:` ... `response.raise_for_status()`)
        if response.status_code >= 400:
            try:
                error_body = response.json()
            except Exception:
                error_body = {"raw": response.text[:500]}
            print(f"[LLM] OpenRouter API error status={response.status_code} model={model} error={error_body}")

            body_str = str(error_body).lower()
            if response.status_code == 402 or "insufficient" in body_str or (
                "credit" in body_str and ("exhaust" in body_str or "quota" in body_str or "balance" in body_str)
            ):
                raise CreditExhaustedError(
                    "OpenRouter credit exhausted — add credit, then retry.",
                    backend=self.active_backend,
                )

        response.raise_for_status()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm.py -q -k credit_exhausted`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/llm.py tests/test_llm.py
git commit -m "feat(llm): CreditExhaustedError on OpenRouter 402/credit body (#243)"
```

---

## Task 4: Escalate-once marker + shared pause-and-suggest helper

**Files:**
- Modify: `api/services/database.py` (`jobs_table` ~line 117 add column; `update_job` ~line 596 plumb field), `api/models/job.py` (`Job` + `JobUpdate` add `auto_escalated_at`)
- Modify: `api/services/escalation.py` (add async `pause_and_suggest`)
- Test: `tests/services/test_escalation.py`, `tests/api/test_database.py`

**Interfaces:**
- Consumes: `update_job(job_id, JobUpdate(...))`, `JobStatus.paused`.
- Produces: `async pause_and_suggest(job_id: int, *, trigger: str, message: str, mark_escalated: bool = False) -> None` — sets `JobStatus.paused`, writes the structured `error_message`, and (when `mark_escalated`) stamps `auto_escalated_at=now`. `Job.auto_escalated_at: datetime | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_escalation.py  (add; uses the test_db fixture pattern from tests/api/test_database.py)
import pytest
from api.models.job import JobCreate, JobStatus
from api.services.database import create_job, get_job
from api.services.escalation import pause_and_suggest
from tests.api.test_database import test_db  # reuse fixture


@pytest.mark.asyncio
async def test_pause_and_suggest_sets_paused_and_marker(test_db):
    job = await create_job(JobCreate(project_name="p", project_path="/p", transcript_file="/t.txt"))
    await pause_and_suggest(job.id, trigger="qa_fail", message="QA failed — review or retry.", mark_escalated=True)
    refreshed = await get_job(job.id)
    assert refreshed.status == JobStatus.paused
    assert "QA failed" in refreshed.error_message
    assert refreshed.auto_escalated_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/services/test_escalation.py -q -k pause_and_suggest`
Expected: FAIL — `ImportError: cannot import name 'pause_and_suggest'` (and, after that, `auto_escalated_at` attribute error)

- [ ] **Step 3: Write minimal implementation**

```python
# api/models/job.py — add to BOTH Job and JobUpdate model bodies
    auto_escalated_at: Optional[datetime] = None
```

```python
# api/services/database.py — in the jobs_table Column list (near error_timestamp, ~line 117)
    Column("auto_escalated_at", DateTime, nullable=True),
```

```python
# api/services/database.py — in update_job(), after the error_message block (~line 605)
        if job_update.auto_escalated_at is not None:
            update_values["auto_escalated_at"] = job_update.auto_escalated_at
```

```python
# api/services/database.py — in _row_to_job mapping (~line 1436, alongside error_timestamp)
        auto_escalated_at=row.auto_escalated_at,
```

```python
# api/services/escalation.py — append
from datetime import datetime, timezone

from api.models.job import JobStatus, JobUpdate
from api.services.database import update_job


async def pause_and_suggest(job_id: int, *, trigger: str, message: str, mark_escalated: bool = False) -> None:
    """Terminal handler shared by all failure triggers (QA-fail, credit, truncation).

    Leaves the job visibly NOT completed: status=paused with a structured,
    actionable error_message. Optionally stamps the escalate-once marker.
    """
    update = JobUpdate(
        status=JobStatus.paused,
        error_message=f"[{trigger}] {message}",
    )
    if mark_escalated:
        update.auto_escalated_at = datetime.now(timezone.utc)
    await update_job(job_id, update)
```

> NOTE: `jobs` is a SQLite table created via `metadata.create_all` (no Alembic in this repo — see `tests/api/test_database.py` fixture). For the **live** DB on cardigan01, add the column with `ALTER TABLE jobs ADD COLUMN auto_escalated_at TIMESTAMP NULL;` during deploy (document in the PR). New/test DBs get it automatically from the Column definition.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/services/test_escalation.py -q -k pause_and_suggest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/escalation.py api/services/database.py api/models/job.py tests/services/test_escalation.py
git commit -m "feat(escalation): auto_escalated_at marker + shared pause_and_suggest (#243)"
```

---

## Task 5: Escalation phase selection (earliest flagged + downstream)

**Files:**
- Modify: `api/services/escalation.py` (add `select_escalation_phases`)
- Test: `tests/services/test_escalation.py`

**Interfaces:**
- Produces: `select_escalation_phases(validation_result: dict, phase_order: list[str]) -> list[str]` — the earliest validator-flagged phase plus every phase after it in `phase_order` (mirrors the worker's reset-downstream logic in `api/routers/jobs.py:201-210`). Empty list if nothing flagged.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_escalation.py  (add)
from api.services.escalation import select_escalation_phases

PHASE_ORDER = ["analyst", "formatter", "seo", "validator", "timestamp"]


def test_selects_earliest_flagged_plus_downstream():
    vr = {"overall": "fail", "phase_results": {
        "analyst": {"status": "pass", "flags": []},
        "formatter": {"status": "fail", "flags": ["x"]},
        "seo": {"status": "pass", "flags": []},
    }}
    assert select_escalation_phases(vr, PHASE_ORDER) == ["formatter", "seo", "validator", "timestamp"]


def test_no_flags_returns_empty():
    vr = {"overall": "pass", "phase_results": {"formatter": {"status": "pass", "flags": []}}}
    assert select_escalation_phases(vr, PHASE_ORDER) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/services/test_escalation.py -q -k escalation_phases or selects_earliest`
Expected: FAIL — `ImportError: cannot import name 'select_escalation_phases'`

- [ ] **Step 3: Write minimal implementation**

```python
# api/services/escalation.py — append
def select_escalation_phases(validation_result: dict, phase_order: list) -> list:
    """Earliest validator-flagged phase + every downstream phase in run order."""
    results = (validation_result or {}).get("phase_results", {})
    flagged = {name for name, r in results.items() if r.get("status") == "fail" or r.get("flags")}
    for i, name in enumerate(phase_order):
        if name in flagged:
            return phase_order[i:]
    return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/services/test_escalation.py -q`
Expected: PASS (all escalation tests)

- [ ] **Step 5: Commit**

```bash
git add api/services/escalation.py tests/services/test_escalation.py
git commit -m "feat(escalation): earliest-flagged + downstream phase selection (#243)"
```

---

## Task 6: Resolve escalated model per phase (compose family + catalog)

**Files:**
- Modify: `api/services/escalation.py` (add async `resolve_escalated_model`)
- Test: `tests/services/test_escalation.py`

**Interfaces:**
- Consumes: `parse_model_family`, `bump_family` (Task 1); `model_roster.newest_in_family` (Task 2).
- Produces: `async resolve_escalated_model(current_model: str | None, exclude_variants: list[str]) -> str | None` — bumps the current model's family and resolves the newest catalog model in the bumped family; `None` if already `opus`, family unknown, or catalog unavailable (→ caller pauses).

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_escalation.py  (add)
import pytest
from unittest.mock import AsyncMock, patch
from api.services import escalation


@pytest.mark.asyncio
async def test_resolve_escalated_model_bumps_and_resolves():
    with patch.object(escalation.model_roster, "newest_in_family",
                      AsyncMock(return_value="anthropic/claude-sonnet-4-6")) as m:
        got = await escalation.resolve_escalated_model("anthropic/claude-4.5-haiku-20251001", ["fast", "fable"])
    assert got == "anthropic/claude-sonnet-4-6"
    m.assert_awaited_once_with("sonnet", ["fast", "fable"])


@pytest.mark.asyncio
async def test_resolve_escalated_model_none_when_opus():
    assert await escalation.resolve_escalated_model("anthropic/claude-opus-4-8", ["fast"]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/services/test_escalation.py -q -k resolve_escalated`
Expected: FAIL — `AttributeError: ... 'resolve_escalated_model'`

- [ ] **Step 3: Write minimal implementation**

```python
# api/services/escalation.py — add import at top and the function
from api.services import model_roster


async def resolve_escalated_model(current_model: str | None, exclude_variants: list) -> str | None:
    """Bump current_model's family one step and resolve the newest catalog model
    in that family. None if already opus / unknown family / catalog unavailable.
    """
    target_family = bump_family(parse_model_family(current_model))
    if target_family is None:
        return None
    return await model_roster.newest_in_family(target_family, exclude_variants)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/services/test_escalation.py -q -k resolve_escalated`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/escalation.py tests/services/test_escalation.py
git commit -m "feat(escalation): resolve_escalated_model (family bump + catalog) (#243)"
```

---

## Task 7: Wire Trigger A — QA-fail escalation in the worker

**Files:**
- Modify: `api/services/worker.py` (replace the unconditional completion at ~line 1096-1102 with a QA gate; add a `_escalate_and_revalidate` helper that re-runs selected phases via the existing `_run_phase(..., model_override=...)` path and re-parses the validator)
- Test: `tests/test_worker.py` *(new — integration, LLM + catalog stubbed)*

**Interfaces:**
- Consumes: `select_escalation_phases`, `resolve_escalated_model`, `pause_and_suggest` (Tasks 4-6); existing `self._run_phase(phase_name, context, ..., model_override=...)`, `self._parse_validation_result`, `update_job`, `end_run_tracking`.
- Produces: behavior only. After all phases run: `validation_result.overall == "pass"` → `completed`; `"fail"` + not yet escalated → escalate once, re-validate, then `completed` (pass) or `pause_and_suggest(mark_escalated=True)` (still fail / unresolvable); `"fail"` + already escalated → `pause_and_suggest`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker.py  (new file)
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from api.services import worker as worker_mod
from api.services.worker import JobWorker
from api.models.job import JobStatus


@pytest.mark.asyncio
async def test_qa_fail_then_pass_completes(monkeypatch):
    """Validator fail → escalate once → re-validate pass → completed with marker."""
    w = JobWorker.__new__(JobWorker)
    w.llm = MagicMock()
    w.llm.config = {"qa_escalation": {"on_validation_fail": True, "max_auto_escalations": 1,
                                      "exclude_variants": ["fast", "fable"]},
                    "agent_phases": ["analyst", "formatter", "seo", "validator"]}

    statuses = []
    monkeypatch.setattr(worker_mod, "update_job_status",
                        AsyncMock(side_effect=lambda jid, st, **k: statuses.append(st)))
    # First validation fails, re-validation passes.
    verdicts = [{"overall": "fail", "phase_results": {"seo": {"status": "fail", "flags": ["x"]}}},
                {"overall": "pass", "phase_results": {"seo": {"status": "pass", "flags": []}}}]
    monkeypatch.setattr(w, "_parse_validation_result", lambda out: verdicts.pop(0))
    monkeypatch.setattr(w, "_run_phase", AsyncMock(return_value={"success": True, "output": "{}", "model": "anthropic/claude-4.5-haiku-20251001"}))
    monkeypatch.setattr(worker_mod, "resolve_escalated_model", AsyncMock(return_value="anthropic/claude-sonnet-4-6"))

    result = await w._finalize_with_qa_gate(job_id=1, context={"validator_output": "{}"},
                                            validation_result=verdicts[0], phase_order=["seo", "validator"])
    assert result == "completed"
    assert JobStatus.completed in statuses
```

> NOTE: This test drives a new focused method `_finalize_with_qa_gate(job_id, context, validation_result, phase_order)` that Step 3 extracts from the inline completion block — keeping the QA gate unit-testable without standing up a full `process_job`. The integration wiring (calling it before the unconditional `completed`) is Step 3b.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_worker.py -q -k qa_fail_then_pass`
Expected: FAIL — `AttributeError: ... '_finalize_with_qa_gate'`

- [ ] **Step 3: Write minimal implementation**

```python
# api/services/worker.py — add imports
from api.services.escalation import (
    pause_and_suggest,
    resolve_escalated_model,
    select_escalation_phases,
)
```

```python
# api/services/worker.py — new method on JobWorker
    async def _finalize_with_qa_gate(self, job_id, context, validation_result, phase_order) -> str:
        """Decide the terminal state after all phases run. Returns 'completed' or 'paused'."""
        cfg = self.llm.config.get("qa_escalation", {})
        overall = (validation_result or {}).get("overall")

        if overall != "fail" or not cfg.get("on_validation_fail", True):
            return "completed"

        job = await get_job(job_id)
        if job is not None and job.auto_escalated_at is not None:
            await pause_and_suggest(job_id, trigger="qa_fail",
                                    message="QA failed again after escalation — review or retry on a stronger model.")
            return "paused"

        phases = select_escalation_phases(validation_result, phase_order)
        exclude = cfg.get("exclude_variants", ["fast", "fable"])
        reran = False
        for phase_name in phases:
            current_model = self._phase_model(context, phase_name)  # reads phases[].model
            target = await resolve_escalated_model(current_model, exclude)
            if target is None:
                continue  # already opus / catalog unavailable for this phase
            res = await self._run_phase(phase_name, context, model_override=target)
            reran = reran or bool(res and res.get("success"))

        if not reran:
            await pause_and_suggest(job_id, trigger="qa_fail",
                                    message="QA failed and no stronger model was available — review or retry.",
                                    mark_escalated=True)
            return "paused"

        # Re-validate once on the configured default (Sonnet).
        reval = await self._run_phase("validator", context)
        verdict = self._parse_validation_result(reval.get("output", "")) if reval and reval.get("success") else {"overall": "fail"}
        if verdict.get("overall") == "pass":
            return "completed"
        await pause_and_suggest(job_id, trigger="qa_fail",
                                message="QA failed after escalation — review or retry on a stronger model.",
                                mark_escalated=True)
        return "paused"
```

```python
# api/services/worker.py — replace the unconditional completion (~line 1096-1102)
            await self._create_manifest(job, project_path, phases, tracker)
            run_summary = await end_run_tracking(job_id)

            outcome = await self._finalize_with_qa_gate(
                job_id, context,
                (await get_job(job_id)).validation_result if await get_job(job_id) else None,
                [p.get("name") for p in (phases or [])],
            )
            if outcome == "completed":
                await update_job_status(job_id, JobStatus.completed,
                                        actual_cost=run_summary["total_cost"] if run_summary else 0)
                await clear_defer_state(job_id)
```

> NOTE: implement `_phase_model(context, phase_name)` as a 3-line reader of the persisted `phases[].model` (Spec A guarantees it is accurate). If a helper already exposes the running model, reuse it.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_worker.py -q -k qa_fail_then_pass`
Expected: PASS

- [ ] **Step 5: Add the "persistent fail → paused" and "already-escalated" tests, implement until green, then commit**

```bash
pytest tests/test_worker.py -q
git add api/services/worker.py tests/test_worker.py
git commit -m "feat(worker): Trigger A — QA-fail auto-escalation gate (#243)"
```

---

## Task 8: Wire Trigger B (credit) + Trigger C (truncation) onto pause-and-suggest

**Files:**
- Modify: `api/services/worker.py` (catch `CreditExhaustedError` around phase execution incl. the optional-phase handler ~line 1085; migrate the truncation block ~line 1009-1029 to call `pause_and_suggest`)
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `CreditExhaustedError` (Task 3), `pause_and_suggest` (Task 4).
- Produces: behavior — `CreditExhaustedError` anywhere (including optional phases) → `pause_and_suggest(trigger="credit", ...)`, retry count unchanged, never swallowed; truncation → same helper, message unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_worker.py  (add)
@pytest.mark.asyncio
async def test_credit_exhausted_not_swallowed_by_optional_phase(monkeypatch):
    from api.services.llm import CreditExhaustedError
    w = JobWorker.__new__(JobWorker)
    paused = {}
    monkeypatch.setattr(worker_mod, "pause_and_suggest",
                        AsyncMock(side_effect=lambda jid, **k: paused.update(k)))
    handled = await w._handle_optional_phase_error(job_id=1, error=CreditExhaustedError("no credit", "openrouter"))
    assert handled is True
    assert paused["trigger"] == "credit"
    assert "credit" in paused["message"].lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_worker.py -q -k credit_exhausted_not_swallowed`
Expected: FAIL — `AttributeError: ... '_handle_optional_phase_error'`

- [ ] **Step 3: Implement**

```python
# api/services/worker.py — extract the optional-phase error handling into a method that
# special-cases CreditExhaustedError BEFORE the swallow path.
    async def _handle_optional_phase_error(self, job_id, error) -> bool:
        """Return True if the job was terminally paused (caller must stop), else False."""
        from api.services.llm import CreditExhaustedError
        if isinstance(error, CreditExhaustedError):
            await pause_and_suggest(job_id, trigger="credit",
                                    message="OpenRouter credit exhausted — add credit, then retry.")
            return True
        return False
```

```python
# api/services/worker.py — truncation block (~line 1009): replace the inline
# update_job_status(paused, error_message=truncation_msg) with:
                                await pause_and_suggest(job_id, trigger="truncation", message=truncation_msg)
                                truncation_paused = True
                                break
```

> Also wrap the main phase loop's non-optional path so a `CreditExhaustedError` raised there routes to `pause_and_suggest(trigger="credit", ...)` and `return`s, rather than becoming a generic phase failure.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_worker.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/worker.py tests/test_worker.py
git commit -m "feat(worker): Triggers B/C onto shared pause-and-suggest (#243)"
```

---

## Task 9: Config — qa_escalation block + validator → Sonnet default

**Files:**
- Modify: `config/llm-config.json`
- Test: `tests/services/test_escalation.py` (config-shape guard)

**Interfaces:**
- Produces: `config["qa_escalation"]` block read by Task 7; `phase_backends["validator"]` pointing at the default (Sonnet) backend instead of cheapskate.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_escalation.py  (add)
import json
from pathlib import Path


def test_config_has_qa_escalation_and_sonnet_validator():
    cfg = json.loads((Path(__file__).resolve().parents[2] / "config" / "llm-config.json").read_text())
    qa = cfg["qa_escalation"]
    assert qa["on_validation_fail"] is True
    assert qa["max_auto_escalations"] == 1
    assert qa["exclude_variants"] == ["fast", "fable"]
    # validator no longer on the cheapskate tier
    assert cfg["phase_backends"]["validator"] != "openrouter-cheapskate"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/services/test_escalation.py -q -k config_has_qa`
Expected: FAIL — `KeyError: 'qa_escalation'`

- [ ] **Step 3: Implement (edit `config/llm-config.json`)**

```jsonc
// add at top level
"qa_escalation": {
  "on_validation_fail": true,
  "max_auto_escalations": 1,
  "family_order": ["haiku", "sonnet", "opus"],
  "exclude_variants": ["fast", "fable"]
},
// and change phase_backends.validator from "openrouter-cheapskate" to "openrouter" (the Sonnet default)
```

- [ ] **Step 4: Run to verify pass + full suite + lint**

```bash
pytest tests/services/test_escalation.py tests/test_worker.py tests/test_llm.py -q
pytest -q
uvx --from black==26.5.1 black --check . && uvx ruff@0.15.18 check .
```
Expected: all PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add config/llm-config.json tests/services/test_escalation.py
git commit -m "feat(config): qa_escalation block + validator→Sonnet default (#243)"
```

---

## Deployment notes (for the PR, not a code task)

- **DB column:** new/test DBs get `auto_escalated_at` from the Column definition; the live cardigan01 SQLite needs a one-line `ALTER TABLE jobs ADD COLUMN auto_escalated_at TIMESTAMP NULL;` (no Alembic in this repo).
- **Cost:** validator → Sonnet raises per-job cost modestly but is load-bearing (8→1 flag reduction in the spec's live re-run); auto-escalation adds at most one re-run of the flagged tail + one re-validation.
- **Structural flags survive escalation** (e.g. the REVIEW-NOTES block): pause-and-suggest on those is *expected* until the deferred criteria question (spec §"Deferred follow-up") is resolved. With PR #264, the chunk-0 *false* truncation flag is already gone, so the most common spurious pause is eliminated before this lands.

## Self-Review

- **Spec coverage:** Goal 1 (never silent-complete on fail) → Task 7. Goal 2 (auto re-run once on stronger model) → Tasks 5-7. Goal 3 (family from actual model, not tier) → Tasks 1, 6. Goal 4 (credit surfaces, no retry consumed) → Tasks 3, 8. Shared helper → Task 4. Trigger C migration → Task 8. Validator default + config → Task 9. Catalog reuse (model_roster) → Task 2. Escalate-once guard → Tasks 4, 7. Catalog-failure → pause → Tasks 2 (returns None) + 7 (pauses).
- **Type consistency:** `parse_model_family`/`bump_family` (str|None), `newest_in_family`/`resolve_escalated_model` (str|None), `select_escalation_phases` (list[str]), `pause_and_suggest` (signature stable across Tasks 4/7/8), `_finalize_with_qa_gate` → "completed"|"paused".
- **No placeholders:** every code step shows real code; seams (`_post_openrouter`, `_phase_model`, `_handle_optional_phase_error`, `_finalize_with_qa_gate`) are named and described where extraction is required.
