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
    monkeypatch.setattr(worker_mod, "update_job", AsyncMock())
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
    monkeypatch.setattr(worker_mod, "update_job", AsyncMock())
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
async def test_escalated_output_threaded_into_revalidation(monkeypatch):
    """Re-validation must judge the ESCALATED output, not the stale pre-escalation one.

    Regression guard for the Critical-1 bug: the escalation loop ran a phase on a
    stronger model but never wrote the new output back into `context`, so the
    re-validation built its prompt from the ORIGINAL failing output. This test does
    NOT stub away that context side-effect — it asserts that by the time the
    validator re-runs, `context["seo_output"]` holds the escalated output.
    """
    w = _make_worker()

    monkeypatch.setattr(worker_mod, "get_job", AsyncMock(return_value=None))
    monkeypatch.setattr(
        worker_mod,
        "resolve_escalated_model",
        AsyncMock(return_value="anthropic/claude-sonnet-4-6"),
    )
    pause = AsyncMock()
    monkeypatch.setattr(worker_mod, "pause_and_suggest", pause)
    monkeypatch.setattr(worker_mod, "update_job", AsyncMock())

    captured: dict = {}

    async def fake_run_phase(job_id, phase_name, context, project_path, model_override=None):
        if phase_name == "validator":
            # Re-validation: snapshot what context holds for the escalated phase
            # at the moment the validator is (re-)built.
            captured["seo_output_at_revalidation"] = context.get("seo_output")
            return {"success": True, "output": "{}", "model": "validator-model"}
        # Escalated phase run produces NEW output. Like the real _run_phase, this
        # stub does NOT mutate context itself — the gate is responsible for threading.
        return {"success": True, "output": "ESCALATED-OUTPUT", "model": "strong-model"}

    monkeypatch.setattr(w, "_run_phase", fake_run_phase)
    monkeypatch.setattr(w, "_parse_validation_result", lambda out: {"overall": "pass"})

    validation_result = {
        "overall": "fail",
        "phase_results": {"seo": {"status": "fail", "flags": ["x"]}},
    }

    outcome = await w._finalize_with_qa_gate(
        job_id=5,
        # Seed context with the STALE pre-escalation output the validator first failed on.
        context={"seo_output": "STALE-OUTPUT"},
        project_path="/tmp/proj",
        validation_result=validation_result,
        phase_order=["seo", "validator"],
    )

    assert outcome == "completed"
    pause.assert_not_called()
    # The re-validation must have seen the ESCALATED output, not the stale one.
    assert captured["seo_output_at_revalidation"] == "ESCALATED-OUTPUT"


@pytest.mark.asyncio
async def test_validator_excluded_from_escalation_loop(monkeypatch):
    """The validator is not re-run as a downstream escalation phase (#243 Minor-3).

    `phase_order` includes `validator`, and `select_escalation_phases` would return
    it as a downstream phase. The gate must exclude it from the escalation loop so
    only the dedicated re-validation runs the validator (exactly once here).
    """
    w = _make_worker()

    monkeypatch.setattr(worker_mod, "get_job", AsyncMock(return_value=None))
    monkeypatch.setattr(
        worker_mod,
        "resolve_escalated_model",
        AsyncMock(return_value="anthropic/claude-sonnet-4-6"),
    )
    pause = AsyncMock()
    monkeypatch.setattr(worker_mod, "pause_and_suggest", pause)
    monkeypatch.setattr(worker_mod, "update_job", AsyncMock())

    escalated_phases: list = []

    async def fake_run_phase(job_id, phase_name, context, project_path, model_override=None):
        if model_override is not None:
            escalated_phases.append(phase_name)
        return {"success": True, "output": "{}", "model": "m"}

    monkeypatch.setattr(w, "_run_phase", fake_run_phase)
    monkeypatch.setattr(w, "_parse_validation_result", lambda out: {"overall": "pass"})

    # validator itself is flagged AND in phase_order -> would be selected without the filter.
    validation_result = {
        "overall": "fail",
        "phase_results": {
            "seo": {"status": "fail", "flags": ["x"]},
            "validator": {"status": "fail", "flags": ["y"]},
        },
    }

    outcome = await w._finalize_with_qa_gate(
        job_id=6,
        context={},
        project_path="/tmp/proj",
        validation_result=validation_result,
        phase_order=["seo", "validator"],
    )

    assert outcome == "completed"
    # The validator was never escalated with a model_override.
    assert "validator" not in escalated_phases
    assert escalated_phases == ["seo"]


@pytest.mark.asyncio
async def test_passing_revalidation_verdict_is_persisted(monkeypatch):
    """After fail→escalate→pass, update_job must persist validation_result.overall='pass'.

    Regression guard for MUST-FIX 1 (#243): if the persist line
    ``await update_job(job_id, JobUpdate(validation_result=verdict, …))`` is removed
    from _finalize_with_qa_gate, this assertion fails while all other gate tests
    remain green — confirming the guard uniquely locks in that behaviour.
    """
    w = _make_worker()

    mock_update_job = AsyncMock()
    monkeypatch.setattr(worker_mod, "get_job", AsyncMock(return_value=None))
    monkeypatch.setattr(
        worker_mod,
        "resolve_escalated_model",
        AsyncMock(return_value="anthropic/claude-sonnet-4-6"),
    )
    monkeypatch.setattr(worker_mod, "pause_and_suggest", AsyncMock())
    monkeypatch.setattr(worker_mod, "update_job", mock_update_job)
    monkeypatch.setattr(
        w,
        "_run_phase",
        AsyncMock(return_value={"success": True, "output": "{}", "model": "strong-model"}),
    )
    monkeypatch.setattr(w, "_parse_validation_result", lambda out: {"overall": "pass"})

    validation_result = {
        "overall": "fail",
        "phase_results": {"seo": {"status": "fail", "flags": ["x"]}},
    }

    outcome = await w._finalize_with_qa_gate(
        job_id=7,
        context={"seo_output": "stale-output"},
        project_path="/tmp/proj",
        validation_result=validation_result,
        phase_order=["seo", "validator"],
    )

    assert outcome == "completed"

    # Scan every update_job call for one carrying validation_result.overall == 'pass'.
    # If the persist line is deleted, mock_update_job is never called with a matching
    # JobUpdate and this assertion fails.
    persist_calls = [
        c
        for c in mock_update_job.await_args_list
        if len(c.args) >= 2
        and hasattr(c.args[1], "validation_result")
        and isinstance(c.args[1].validation_result, dict)
        and c.args[1].validation_result.get("overall") == "pass"
    ]
    assert persist_calls, (
        "update_job was never called with a JobUpdate carrying "
        "validation_result.overall='pass'; the MUST-FIX 1 persist line may have been removed"
    )


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
