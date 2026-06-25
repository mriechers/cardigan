"""Integration tests for the QA-fail auto-escalation gate in the worker (#243)."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services import worker as worker_mod
from api.services.worker import JobWorker


def _make_worker():
    """Bare JobWorker with a stubbed LLM config carrying qa_escalation."""
    w = JobWorker.__new__(JobWorker)
    w.llm = MagicMock()
    w.llm.config = {
        "qa_escalation": {
            "on_validation_fail": True,
            "max_auto_escalations": 1,
            "exclude_variants": ["fast", "fable"],
        },
        "agent_phases": ["analyst", "formatter", "seo", "validator"],
    }
    return w


@pytest.mark.asyncio
async def test_qa_fail_then_pass_completes(monkeypatch):
    """Validator fail -> escalate once -> re-validate pass -> 'completed'."""
    w = _make_worker()

    monkeypatch.setattr(worker_mod, "get_job", AsyncMock(return_value=None))
    monkeypatch.setattr(
        worker_mod,
        "resolve_escalated_model",
        AsyncMock(return_value="anthropic/claude-sonnet-4-6"),
    )
    pause = AsyncMock()
    monkeypatch.setattr(worker_mod, "pause_and_suggest", pause)
    run_phase = AsyncMock(
        return_value={"success": True, "output": "{}", "model": "anthropic/claude-4.5-haiku-20251001"}
    )
    monkeypatch.setattr(w, "_run_phase", run_phase)
    # Re-validation parses to a PASS verdict (the initial fail is passed in directly).
    monkeypatch.setattr(w, "_parse_validation_result", lambda out: {"overall": "pass"})

    validation_result = {
        "overall": "fail",
        "phase_results": {"seo": {"status": "fail", "flags": ["x"]}},
    }

    outcome = await w._finalize_with_qa_gate(
        job_id=1,
        context={"validator_output": "{}"},
        project_path="/tmp/proj",
        validation_result=validation_result,
        phase_order=["seo", "validator"],
    )

    assert outcome == "completed"
    pause.assert_not_called()
    # Escalation re-ran the flagged phase with the stronger model override.
    override_calls = [c for c in run_phase.await_args_list if c.kwargs.get("model_override")]
    assert override_calls, "expected at least one escalated _run_phase call"
    assert override_calls[0].kwargs["model_override"] == "anthropic/claude-sonnet-4-6"
    # _run_phase always receives job_id + project_path positionally.
    assert override_calls[0].args[0] == 1
    assert override_calls[0].args[3] == "/tmp/proj"


@pytest.mark.asyncio
async def test_qa_persistent_fail_pauses(monkeypatch):
    """Validator fail -> escalate -> re-validate STILL fail -> 'paused' + mark_escalated."""
    w = _make_worker()

    monkeypatch.setattr(worker_mod, "get_job", AsyncMock(return_value=None))
    monkeypatch.setattr(
        worker_mod,
        "resolve_escalated_model",
        AsyncMock(return_value="anthropic/claude-sonnet-4-6"),
    )
    pause = AsyncMock()
    monkeypatch.setattr(worker_mod, "pause_and_suggest", pause)
    monkeypatch.setattr(
        w,
        "_run_phase",
        AsyncMock(return_value={"success": True, "output": "{}", "model": "x"}),
    )
    monkeypatch.setattr(w, "_parse_validation_result", lambda out: {"overall": "fail"})

    validation_result = {
        "overall": "fail",
        "phase_results": {"seo": {"status": "fail", "flags": ["x"]}},
    }

    outcome = await w._finalize_with_qa_gate(
        job_id=2,
        context={},
        project_path="/tmp/proj",
        validation_result=validation_result,
        phase_order=["seo", "validator"],
    )

    assert outcome == "paused"
    pause.assert_awaited_once()
    assert pause.await_args.kwargs.get("mark_escalated") is True
    assert pause.await_args.kwargs.get("trigger") == "qa_fail"


@pytest.mark.asyncio
async def test_qa_fail_already_escalated_pauses(monkeypatch):
    """Job already carries auto_escalated_at -> pause immediately, no re-run."""
    w = _make_worker()

    already = SimpleNamespace(auto_escalated_at=datetime.now(timezone.utc), phases=[])
    monkeypatch.setattr(worker_mod, "get_job", AsyncMock(return_value=already))
    pause = AsyncMock()
    monkeypatch.setattr(worker_mod, "pause_and_suggest", pause)
    run_phase = AsyncMock(return_value={"success": True, "output": "{}"})
    monkeypatch.setattr(w, "_run_phase", run_phase)

    validation_result = {
        "overall": "fail",
        "phase_results": {"seo": {"status": "fail", "flags": ["x"]}},
    }

    outcome = await w._finalize_with_qa_gate(
        job_id=3,
        context={},
        project_path="/tmp/proj",
        validation_result=validation_result,
        phase_order=["seo", "validator"],
    )

    assert outcome == "paused"
    pause.assert_awaited_once()
    # Re-escalation is suppressed when already escalated once.
    run_phase.assert_not_called()


@pytest.mark.asyncio
async def test_qa_pass_completes_without_escalation(monkeypatch):
    """Validator pass -> straight to 'completed', no escalation machinery touched."""
    w = _make_worker()

    get_job = AsyncMock(return_value=None)
    monkeypatch.setattr(worker_mod, "get_job", get_job)
    run_phase = AsyncMock()
    monkeypatch.setattr(w, "_run_phase", run_phase)
    pause = AsyncMock()
    monkeypatch.setattr(worker_mod, "pause_and_suggest", pause)

    outcome = await w._finalize_with_qa_gate(
        job_id=4,
        context={},
        project_path="/tmp/proj",
        validation_result={"overall": "pass"},
        phase_order=["seo", "validator"],
    )

    assert outcome == "completed"
    get_job.assert_not_called()
    run_phase.assert_not_called()
    pause.assert_not_called()
