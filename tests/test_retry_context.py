"""Tests for user-driven retry with context feed-forward."""


from unittest.mock import MagicMock


def test_retry_request_accepts_model():
    """PhaseRetryRequest should accept a model field."""
    from api.routers.jobs import PhaseRetryRequest

    req = PhaseRetryRequest(feedback="fix speaker names", model="anthropic/claude-sonnet-4.6")
    assert req.model == "anthropic/claude-sonnet-4.6"
    assert req.feedback == "fix speaker names"


def test_retry_request_model_optional():
    """Model field should be optional."""
    from api.routers.jobs import PhaseRetryRequest

    req = PhaseRetryRequest()
    assert req.model is None
    assert req.feedback is None


def test_build_phase_prompt_includes_validation_flags():
    """Retry prompt should include validation flags when present."""
    from api.services.worker import JobWorker

    worker = JobWorker.__new__(JobWorker)
    worker.llm = MagicMock()
    worker.llm.config = {}

    context = {
        "transcript": "Hello world",
        "_validation_flags": ["review notes in body", "speaker labels inconsistent"],
    }
    prompt = worker._build_phase_prompt("analyst", context)
    assert "Validation Issues from Previous Attempt" in prompt
    assert "review notes in body" in prompt
    assert "speaker labels inconsistent" in prompt


def test_build_phase_prompt_includes_previous_output():
    """Retry prompt should include previous output when present."""
    from api.services.worker import JobWorker

    worker = JobWorker.__new__(JobWorker)
    worker.llm = MagicMock()
    worker.llm.config = {}

    context = {
        "transcript": "Hello world",
        "_previous_output": "Previous analysis content here",
    }
    prompt = worker._build_phase_prompt("analyst", context)
    assert "Previous Output" in prompt
    assert "Previous analysis content here" in prompt


def test_build_phase_prompt_no_retry_context_when_absent():
    """Normal (non-retry) prompts should not include retry context."""
    from api.services.worker import JobWorker

    worker = JobWorker.__new__(JobWorker)
    worker.llm = MagicMock()
    worker.llm.config = {}

    context = {"transcript": "Hello world"}
    prompt = worker._build_phase_prompt("analyst", context)
    assert "Validation Issues" not in prompt
    assert "Previous Output" not in prompt


def test_build_phase_prompt_retry_context_with_editorial_feedback():
    """Retry prompt should include both validation flags and editorial feedback."""
    from api.services.worker import JobWorker

    worker = JobWorker.__new__(JobWorker)
    worker.llm = MagicMock()
    worker.llm.config = {}

    context = {
        "transcript": "Hello world",
        "_validation_flags": ["missing chapter markers"],
        "_previous_output": "Previous output text",
        "_editorial_feedback": "Add a chapter for the budget segment",
    }
    prompt = worker._build_phase_prompt("analyst", context)
    assert "Validation Issues from Previous Attempt" in prompt
    assert "missing chapter markers" in prompt
    assert "Previous Output" in prompt
    assert "Editorial Feedback" in prompt
    assert "Add a chapter for the budget segment" in prompt


def test_run_phase_accepts_model_override():
    """_run_phase should accept model_override parameter."""
    import inspect

    from api.services.worker import JobWorker

    sig = inspect.signature(JobWorker._run_phase)
    assert "model_override" in sig.parameters
    param = sig.parameters["model_override"]
    assert param.default is None
