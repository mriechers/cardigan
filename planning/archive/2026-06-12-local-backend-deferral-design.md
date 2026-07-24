# Design: Defer-and-requeue when a local backend is busy

> **Note (2026-07-02):** written for "dougie"; the defer-and-requeue mechanism is
> generic and now serves the `local-llm` (oMLX) backend unchanged. See
> `planning/2026-07-02-local-llm-omlx-integration.md`.

**Date:** 2026-06-12
**Status:** Approved (brainstormed with Mark)
**Branch/PR:** new branch, stacked on `feat/local-dougie-backend-seam` (PR #210)
**Companion:** `dougie-local-agent/planning/handoff/2026-06-10-cardigan-busy-signal-needs.md`

## Problem

A backend can be **temporarily unavailable** rather than broken. The local MLX
backend (`local-dougie`) returns `503 {"detail": "memory pressure … refusing to
load"}` when the Mac Studio is under memory pressure — it's only meant to run when
the machine is relatively idle. Today Cardigan treats any phase error as fatal:
`_run_phase` catches the exception → `success: False` → the pipeline raises at
`worker.py:865` → **the whole job fails**. There is no fallback and no retry that
switches behavior. So "Studio busy" silently becomes "failed job."

## Goal

Treat "busy" as **"not now," not "failed."** When a `defer_when_unavailable`
backend reports unavailability, requeue the job and retry with backoff until the
Studio frees up; after a long ceiling, pause for a human. Stay 100% local (keep the
cost savings); never burn the job's failure-retry budget for being busy.

## Non-goals

- Cloud fallback (explicitly rejected — defeats the cost-savings reason for local).
- Per-phase deferral granularity (job-level requeue + phase-skip-on-resume is enough).
- A polished GUI badge (deferred jobs are visible via events + the `paused` state;
  badge polish is a follow-up).

## Approach

Reuse Cardigan's existing `pending` → poll → `claim_next_job` machinery and the
phase-skip-on-resume (`worker.py:821`), adding a backoff timer so a busy Studio
isn't hammered. Four moving parts:

### 1. Detect — `api/services/llm.py`

New typed exception:

```python
class BackendUnavailableError(Exception):
    """A backend that opted into deferral is temporarily unavailable
    (busy / loading / under memory pressure). Carries the upstream detail."""
    def __init__(self, detail: str, *, backend: str, retry_after_s: int | None = None):
        ...
```

In `_call_openai`, when `config.get("defer_when_unavailable")` is true, convert a
transient-unavailable signal into `BackendUnavailableError`:
- HTTP **503** (read `detail`, and a `Retry-After` header / `retry_after_s` body
  field if present — see dougie handoff).
- `httpx.ConnectError` (dougie down / not reachable).
- `httpx.ReadTimeout` (cold load or contention exceeded the per-request timeout).

Backends **without** the flag behave exactly as today (generic failure). Genuine
errors (4xx other than 429, malformed response) are NOT converted — they still fail.
Forward-compat: if dougie later sends `{"error": {"retryable": false}}` on a 503,
honor it (do NOT defer). Absent that field, treat a 503 from a deferrable backend
as retryable (graceful degradation, per the handoff §6).

### 2. Classify — `api/services/worker.py` `_run_phase`

Add an `except BackendUnavailableError` arm *before* the generic `except Exception`.
It returns a distinct result and logs `job_deferred` (not `phase_failed`):

```python
return {"success": False, "deferred": True, "detail": e.detail,
        "retry_after_s": e.retry_after_s, "cost": 0, "tokens": 0}
```

### 3. Requeue — `api/services/worker.py` `_process_job` main loop

At the existing `if not phase_result["success"]` branch (`worker.py:865`), check
`deferred` first:

```python
if phase_result.get("deferred"):
    await defer_job(job_id, retry_after_s=phase_result.get("retry_after_s"),
                    detail=phase_result.get("detail"))
    return  # stop processing this job this cycle; do NOT raise/fail
if not phase_result["success"]:
    raise Exception(...)  # unchanged
```

### 4. Schedule + gate — `api/services/database.py`

`defer_job(job_id, retry_after_s, detail)`:
- Read `defer_count`, `first_deferred_at`.
- **Ceiling check:** if `first_deferred_at` set and `now - first_deferred_at >=
  ceiling` (default 6 h) → status `paused`, `error_message` = "Waited {ceiling}h for
  local capacity; Studio still busy. Retry, or switch this job to cloud.", emit
  `system_pause`. Done.
- **Else defer:** status `pending`; `retry_after = now + backoff`; `defer_count += 1`;
  set `first_deferred_at` if null; emit `job_deferred` with `detail`/`defer_count`.
- Backoff = `retry_after_s` from dougie if provided, else the schedule
  `[2, 5, 10, 15]` min indexed by `defer_count` (plateau at 15). Config-tunable.

`claim_next_job`: add `AND (retry_after IS NULL OR retry_after <= :now)` to the
pending-selection `WHERE`, so deferred jobs aren't re-claimed until their backoff
elapses. Order by `created_at` unchanged.

**Counter separation:** `defer_count` is independent of `retry_count`/`max_retries`.
Successful job completion clears the defer state (`defer_count = 0`, `retry_after =
NULL`, `first_deferred_at = NULL`) via `clear_defer_state`, so a completed job carries
no stale bookkeeping and any future reprocess starts a fresh ceiling. The ceiling is
therefore **per-job wall-clock** (time since the job's first defer), not per-phase —
a simpler, defensible semantic for "this job has been waiting too long."

### Data model — alembic migration

Add nullable columns to `jobs` (all default-safe, no backfill):
- `retry_after` TIMESTAMP NULL
- `defer_count` INTEGER NOT NULL DEFAULT 0
- `first_deferred_at` TIMESTAMP NULL

### Config — `config/llm-config.json`

- `local-dougie`: add `"defer_when_unavailable": true`.
- New `routing.deferral`: `{ "backoff_minutes": [2, 5, 10, 15], "ceiling_hours": 6 }`.

### Events — `api/models/events.py`

Add `EventType.job_deferred`. Payload (`EventData.extra`):
`{ "detail": str, "defer_count": int, "retry_after": iso8601, "backend": str }`.

## Error handling / edge cases

- **Clear defer state on success** so ceilings don't leak across unrelated busy spells.
- **Ceiling uses `first_deferred_at`**, not `defer_count`, so wall-clock governs the
  6 h, independent of backoff tuning.
- **Mid-pipeline defer** is safe: completed phases are cached and skipped on resume.
- **Worker crash while deferred** is covered by the existing `reset_stuck_jobs` path
  (the job is `pending`, not `in_progress`, so it's not even "stuck").
- **dougie down vs busy:** both are `BackendUnavailableError` → both defer. If dougie
  is permanently down, the 6 h ceiling pauses the job with the clear message.

## Testing (TDD)

Unit (`tests/api/test_llm.py`):
- `_call_openai` raises `BackendUnavailableError` on 503 for a `defer_when_unavailable`
  backend; carries `detail`; reads `retry_after_s` from `Retry-After` when present.
- Same on `ConnectError` / `ReadTimeout` for that backend.
- Does **not** raise it for a non-opt-in backend (generic failure preserved).
- Honors a future `{"error": {"retryable": false}}` → does NOT raise it.

Worker (`tests/api/test_worker.py` or equivalent):
- `_run_phase` returns `deferred: True` on `BackendUnavailableError`.
- `_process_job` calls `defer_job` and returns without raising on a deferred phase.

DB (`tests/api/test_database.py`):
- `defer_job` sets `pending` + `retry_after` + increments `defer_count`; backoff grows.
- Past ceiling → `paused` with the message.
- `claim_next_job` skips a job whose `retry_after` is in the future; claims it once elapsed.
- `defer_job` does not change `retry_count`.
- `clear_defer_state` resets all three columns (called on job completion).

## Rollout

- Own PR, stacked on #210. Safe by default: nothing routes to `local-dougie` yet,
  and the deferral path only activates for `defer_when_unavailable` backends.
- Upgrades automatically when dougie ships the richer envelope (handoff) — Cardigan
  starts honoring `retryable`/`retry_after_s` without code changes here.
