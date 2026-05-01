"""Tests for cost estimation service."""


def test_estimate_job_cost_basic():
    """Should estimate cost from word count and model pricing."""
    from api.services.cost_estimator import estimate_job_cost

    result = estimate_job_cost(
        word_count=1000,
        phase_models={
            "analyst": "anthropic/claude-haiku-4.5",
            "formatter": "anthropic/claude-haiku-4.5",
            "seo": "anthropic/claude-haiku-4.5",
            "validator": "anthropic/claude-haiku-4.5",
        },
    )
    assert "total_estimated_cost" in result
    assert result["total_estimated_cost"] > 0
    assert "phase_estimates" in result
    assert len(result["phase_estimates"]) == 4


def test_estimate_job_cost_returns_per_phase_breakdown():
    """Each phase should have its own cost estimate."""
    from api.services.cost_estimator import estimate_job_cost

    result = estimate_job_cost(
        word_count=5000,
        phase_models={
            "analyst": "anthropic/claude-haiku-4.5",
            "formatter": "anthropic/claude-sonnet-4.6",
        },
    )
    for phase_est in result["phase_estimates"]:
        assert "phase" in phase_est
        assert "model" in phase_est
        assert "estimated_input_tokens" in phase_est
        assert "estimated_output_tokens" in phase_est
        assert "estimated_cost" in phase_est


def test_estimate_job_cost_zero_words():
    """Zero word count should return zero cost."""
    from api.services.cost_estimator import estimate_job_cost

    result = estimate_job_cost(
        word_count=0,
        phase_models={"analyst": "anthropic/claude-haiku-4.5"},
    )
    assert result["total_estimated_cost"] == 0


def test_estimate_job_cost_unknown_model_uses_conservative_estimate():
    """Unknown models should fall back to conservative pricing."""
    from api.services.cost_estimator import estimate_job_cost

    result = estimate_job_cost(
        word_count=1000,
        phase_models={"analyst": "unknown/model-xyz"},
    )
    assert result["total_estimated_cost"] > 0
