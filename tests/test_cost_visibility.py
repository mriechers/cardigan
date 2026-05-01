"""Tests for cost visibility: token granularity and pricing."""


def test_job_phase_has_token_breakdown():
    """JobPhase should support input_tokens and output_tokens fields."""
    from api.models.job import JobPhase

    phase = JobPhase(
        name="analyst",
        input_tokens=12000,
        output_tokens=3000,
        tokens=15000,
        cost=0.02,
    )
    assert phase.input_tokens == 12000
    assert phase.output_tokens == 3000
    assert phase.tokens == 15000


def test_job_phase_token_breakdown_defaults_to_zero():
    """Token breakdown fields should default to 0."""
    from api.models.job import JobPhase

    phase = JobPhase(name="analyst")
    assert phase.input_tokens == 0
    assert phase.output_tokens == 0
    assert phase.tokens == 0


def test_run_phase_return_includes_token_breakdown():
    """_run_phase return dict should include input_tokens and output_tokens."""
    from api.models.job import JobPhase

    phase_result = {
        "success": True,
        "output": "test content",
        "cost": 0.02,
        "tokens": 15000,
        "input_tokens": 12000,
        "output_tokens": 3000,
        "model": "anthropic/claude-haiku-4.5",
    }

    phase = JobPhase(
        name="analyst",
        status="completed",
        cost=phase_result["cost"],
        tokens=phase_result["tokens"],
        input_tokens=phase_result["input_tokens"],
        output_tokens=phase_result["output_tokens"],
        model=phase_result["model"],
    )

    assert phase.input_tokens == 12000
    assert phase.output_tokens == 3000
