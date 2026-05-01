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
