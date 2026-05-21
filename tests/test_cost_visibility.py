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


def test_available_model_includes_pricing():
    """AvailableModel should include input/output pricing fields."""
    from api.routers.config import AvailableModel

    model = AvailableModel(
        id="anthropic/claude-haiku-4.5",
        name="Claude Haiku 4.5",
        provider="Anthropic",
        tier=0,
        pricing_input=0.80,
        pricing_output=4.00,
    )
    assert model.pricing_input == 0.80
    assert model.pricing_output == 4.00


def test_available_model_pricing_defaults_to_none():
    """Pricing fields should default to None when not available."""
    from api.routers.config import AvailableModel

    model = AvailableModel(
        id="test/model",
        name="Test",
        provider="Test",
        tier=0,
    )
    assert model.pricing_input is None
    assert model.pricing_output is None


def test_estimate_cost_endpoint():
    """The /config/estimate-cost endpoint should return a cost estimate."""
    from starlette.testclient import TestClient

    from api.main import app

    client = TestClient(app)
    resp = client.post("/api/config/estimate-cost", json={"word_count": 5000})
    assert resp.status_code == 200
    data = resp.json()
    assert "total_estimated_cost" in data
    assert data["total_estimated_cost"] > 0
    assert "phase_estimates" in data
