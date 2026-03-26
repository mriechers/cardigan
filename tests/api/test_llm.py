"""Tests for LLMClient in api/services/llm.py.

Tests backend selection, tier calculation, cost tracking, safety guards,
and error handling for LLM API interactions.
"""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from api.services.llm import (
    CostCapExceededError,
    LLMClient,
    LLMResponse,
    ModelNotAllowedError,
    RunCostTracker,
    TokenCostTooHighError,
    calculate_cost,
    end_run_tracking,
    get_run_tracker,
    start_run_tracking,
)


@pytest.fixture
def mock_config(tmp_path):
    """Create a mock config file for testing."""
    config = {
        "primary_backend": "openrouter",
        "backends": {
            "openrouter": {
                "type": "openrouter",
                "endpoint": "https://openrouter.ai/api/v1/chat/completions",
                "api_key_env": "OPENROUTER_API_KEY",
                "model": "google/gemini-2.0-flash-exp",
                "preset": "cheapskate",
                "fallback_model": "google/gemini-2.5-flash",
            },
            "openrouter-cheapskate": {
                "type": "openrouter",
                "endpoint": "https://openrouter.ai/api/v1/chat/completions",
                "api_key_env": "OPENROUTER_API_KEY",
                "preset": "cheapskate",
            },
            "openrouter-big-brain": {
                "type": "openrouter",
                "endpoint": "https://openrouter.ai/api/v1/chat/completions",
                "api_key_env": "OPENROUTER_API_KEY",
                "preset": "big-brain",
            },
        },
        "routing": {
            "tiers": ["openrouter-cheapskate", "openrouter", "openrouter-big-brain"],
            "tier_labels": ["cheapskate", "default", "big-brain"],
            "phase_base_tiers": {"analyst": 0, "formatter": 0, "seo": 0, "manager": 2},
            "duration_thresholds": [
                {"max_minutes": 15, "tier": 0},
                {"max_minutes": 30, "tier": 1},
                {"max_minutes": None, "tier": 2},
            ],
            "escalation": {
                "enabled": True,
                "on_failure": True,
                "on_timeout": True,
                "timeout_seconds": 120,
                "max_retries_per_tier": 1,
            },
        },
        "safety": {"run_cost_cap": 1.0, "max_cost_per_1k_tokens": 0.05, "model_allowlist": []},
    }

    config_path = tmp_path / "test_llm_config.json"
    with open(config_path, "w") as f:
        json.dump(config, f)

    return config_path


@pytest.fixture
def llm_client(mock_config):
    """Create an LLMClient instance with test config."""
    return LLMClient(config_path=str(mock_config))


class TestLLMClientInitialization:
    """Tests for LLMClient initialization."""

    def test_client_loads_config(self, llm_client):
        """Test that client loads configuration correctly."""
        assert llm_client.config is not None
        assert "backends" in llm_client.config
        assert "routing" in llm_client.config
        assert llm_client.run_cost_cap == 1.0
        assert llm_client.max_cost_per_1k_tokens == 0.05

    def test_client_missing_config_raises_error(self):
        """Test that missing config file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            LLMClient(config_path="nonexistent.json")

    def test_client_env_overrides_config(self, mock_config, monkeypatch):
        """Test that environment variables override config."""
        monkeypatch.setenv("LLM_RUN_COST_CAP", "5.0")
        monkeypatch.setenv("LLM_MAX_COST_PER_1K_TOKENS", "0.10")

        client = LLMClient(config_path=str(mock_config))

        assert client.run_cost_cap == 5.0
        assert client.max_cost_per_1k_tokens == 0.10

    def test_model_allowlist_from_env(self, mock_config, monkeypatch):
        """Test model allowlist can be set via environment."""
        monkeypatch.setenv("LLM_MODEL_ALLOWLIST", "gpt-4o,claude-3-5-sonnet")

        client = LLMClient(config_path=str(mock_config))

        assert "gpt-4o" in client.model_allowlist
        assert "claude-3-5-sonnet" in client.model_allowlist


class TestBackendSelection:
    """Tests for backend and model selection."""

    def test_get_backend_config(self, llm_client):
        """Test retrieving backend configuration."""
        config = llm_client.get_backend_config("openrouter")

        assert config["type"] == "openrouter"
        assert "endpoint" in config
        assert "api_key_env" in config

    def test_get_backend_config_invalid_backend(self, llm_client):
        """Test that invalid backend raises ValueError."""
        with pytest.raises(ValueError):
            llm_client.get_backend_config("nonexistent-backend")

    def test_get_backend_for_phase_analyst(self, llm_client):
        """Test backend selection for analyst phase."""
        backend = llm_client.get_backend_for_phase("analyst")

        # Should use cheapskate tier for short transcripts
        assert backend == "openrouter-cheapskate"

    def test_get_backend_for_phase_manager(self, llm_client):
        """Test backend selection for manager phase."""
        backend = llm_client.get_backend_for_phase("manager")

        # Manager has base tier 2 (big-brain)
        assert backend == "openrouter-big-brain"

    def test_get_backend_for_phase_with_long_transcript(self, llm_client):
        """Test backend escalates for long transcripts."""
        context = {"transcript_metrics": {"estimated_duration_minutes": 45}}

        backend = llm_client.get_backend_for_phase("analyst", context=context)

        # Should escalate to higher tier for long transcript
        assert backend in ["openrouter", "openrouter-big-brain"]

    def test_get_backend_with_tier_override(self, llm_client):
        """Test explicit tier override."""
        backend = llm_client.get_backend_for_phase("analyst", tier_override=2)

        # Should use tier 2 (big-brain)
        assert backend == "openrouter-big-brain"


class TestTierCalculation:
    """Tests for tier calculation logic."""

    def test_get_tier_for_phase_base_tier(self, llm_client):
        """Test tier calculation uses base tier."""
        tier = llm_client.get_tier_for_phase("analyst")

        assert tier == 0  # Base tier for analyst

    def test_get_tier_for_phase_with_reason(self, llm_client):
        """Test tier calculation returns reason."""
        tier, reason = llm_client.get_tier_for_phase_with_reason("analyst")

        assert tier == 0
        assert "base tier" in reason.lower()

    def test_get_tier_for_phase_escalates_with_duration(self, llm_client):
        """Test tier escalates based on duration."""
        context = {"transcript_metrics": {"estimated_duration_minutes": 25}}

        tier, reason = llm_client.get_tier_for_phase_with_reason("analyst", context=context)

        # Should escalate to tier 1 for 15-30 minute transcript
        assert tier >= 1
        assert "duration" in reason.lower()

    def test_get_next_tier(self, llm_client):
        """Test getting next escalation tier."""
        next_tier = llm_client.get_next_tier(0)
        assert next_tier == 1

        next_tier = llm_client.get_next_tier(1)
        assert next_tier == 2

        # No tier beyond max
        next_tier = llm_client.get_next_tier(2)
        assert next_tier is None

    def test_get_escalation_config(self, llm_client):
        """Test retrieving escalation configuration."""
        config = llm_client.get_escalation_config()

        assert config["enabled"] is True
        assert config["on_failure"] is True
        assert config["on_timeout"] is True
        assert "timeout_seconds" in config


class TestCostCalculation:
    """Tests for cost calculation."""

    def test_calculate_cost_with_openrouter_cost(self):
        """Test that OpenRouter-reported cost is preferred."""
        cost = calculate_cost(model="gpt-4o", input_tokens=1000, output_tokens=500, openrouter_cost=0.025)

        assert cost == 0.025

    def test_calculate_cost_from_pricing_table(self):
        """Test cost calculation from pricing table."""
        cost = calculate_cost(model="gpt-4o", input_tokens=1000, output_tokens=500, openrouter_cost=None)

        # gpt-4o: $2.50/M input, $10/M output
        # (1000 / 1M * 2.50) + (500 / 1M * 10) = 0.0025 + 0.005 = 0.0075
        assert cost == pytest.approx(0.0075, rel=0.01)

    def test_calculate_cost_unknown_model(self):
        """Test cost calculation for unknown model uses conservative estimate."""
        cost = calculate_cost(model="unknown-model", input_tokens=1000, output_tokens=500, openrouter_cost=None)

        # Conservative estimate: $1/M input, $3/M output
        # (1000 / 1M * 1) + (500 / 1M * 3) = 0.001 + 0.0015 = 0.0025
        assert cost == pytest.approx(0.0025, rel=0.01)

    def test_calculate_cost_free_tier_model(self):
        """Test free tier models return zero cost."""
        cost = calculate_cost(
            model="xiaomi/mimo-v2-flash:free", input_tokens=1000, output_tokens=500, openrouter_cost=None
        )

        assert cost == 0.0


class TestCostTracking:
    """Tests for run cost tracking."""

    def test_start_run_tracking(self):
        """Test starting cost tracking."""
        tracker = start_run_tracking(job_id=123)

        assert tracker.job_id == 123
        assert tracker.total_cost == 0.0
        assert tracker.call_count == 0
        assert tracker.start_time is not None

    def test_run_tracker_add_call(self):
        """Test adding calls to tracker."""
        tracker = RunCostTracker(job_id=1)

        response = LLMResponse(
            content="test",
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cost=0.001,
            duration_ms=1000,
            backend="openai",
        )

        tracker.add_call(response)

        assert tracker.total_cost == 0.001
        assert tracker.total_tokens == 150
        assert tracker.call_count == 1

    def test_get_run_tracker(self):
        """Test retrieving current run tracker."""
        start_run_tracking(job_id=456)
        tracker = get_run_tracker()

        assert tracker is not None
        assert tracker.job_id == 456

    @pytest.mark.asyncio
    async def test_end_run_tracking(self):
        """Test ending run tracking emits event."""
        start_run_tracking(job_id=789)
        tracker = get_run_tracker()

        # Add a call
        response = LLMResponse(
            content="test",
            model="gpt-4o",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cost=0.001,
            duration_ms=1000,
            backend="openai",
        )
        tracker.add_call(response)

        with patch("api.services.llm.log_event") as mock_log:
            mock_log.return_value = None
            summary = await end_run_tracking()

        assert summary is not None
        assert summary["total_cost"] == 0.001
        assert summary["total_tokens"] == 150

        # Tracker should be cleared
        assert get_run_tracker() is None


class TestSafetyGuards:
    """Tests for cost cap and safety guard enforcement."""

    def test_check_model_allowed_empty_allowlist(self, llm_client):
        """Test that empty allowlist allows all models."""
        # Should not raise
        llm_client.check_model_allowed("any-model")

    def test_check_model_allowed_with_allowlist(self, llm_client):
        """Test model allowlist enforcement."""
        llm_client.model_allowlist = ["gpt-4o", "claude-3-5-sonnet"]

        # Allowed model should pass
        llm_client.check_model_allowed("gpt-4o")

        # Disallowed model should raise
        with pytest.raises(ModelNotAllowedError):
            llm_client.check_model_allowed("random-model")

    def test_check_model_allowed_prefix_match(self, llm_client):
        """Test model allowlist supports prefix matching."""
        llm_client.model_allowlist = ["gpt-4o"]

        # Versioned model should match
        llm_client.check_model_allowed("gpt-4o:extended")

    def test_check_token_cost_within_limit(self, llm_client):
        """Test token cost check passes for cheap models."""
        # Should not raise for cheap model
        llm_client.check_token_cost("gpt-4o-mini")

    def test_check_token_cost_exceeds_limit(self, llm_client):
        """Test token cost check fails for expensive models."""
        llm_client.max_cost_per_1k_tokens = 0.001  # Very low limit

        with pytest.raises(TokenCostTooHighError):
            llm_client.check_token_cost("anthropic/claude-3.5-sonnet")

    def test_check_run_cost_cap_within_limit(self, llm_client):
        """Test run cost cap check passes when under limit."""
        start_run_tracking(job_id=1)
        tracker = get_run_tracker()
        tracker.total_cost = 0.5

        # Should not raise (under $1 cap)
        llm_client.check_run_cost_cap()

    def test_check_run_cost_cap_exceeds_limit(self, llm_client):
        """Test run cost cap check fails when over limit."""
        start_run_tracking(job_id=1)
        tracker = get_run_tracker()
        tracker.total_cost = 1.5

        with pytest.raises(CostCapExceededError):
            llm_client.check_run_cost_cap()

    def test_safety_guards_can_be_disabled(self, llm_client, monkeypatch):
        """Test that safety guards can be disabled for testing."""
        monkeypatch.setenv("LLM_ENFORCE_GUARDS", "false")
        llm_client._load_safety_config()

        # Should not raise even with violations
        llm_client.model_allowlist = ["only-this-model"]
        llm_client.check_model_allowed("different-model")


class TestAPIInteractions:
    """Tests for API call handling."""

    @pytest.mark.asyncio
    async def test_chat_with_openrouter(self, llm_client, monkeypatch):
        """Test chat call to OpenRouter backend."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        # Reset global tracker to avoid cost cap issues
        start_run_tracking(job_id=1)

        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Test response"}}],
            "model": "google/gemini-2.0-flash-exp",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }

        with patch.object(httpx.AsyncClient, "post", return_value=mock_response):
            with patch("api.services.llm.log_event"):
                response = await llm_client.chat(messages=[{"role": "user", "content": "Hello"}], backend="openrouter")

        assert response.content == "Test response"
        assert response.model == "google/gemini-2.0-flash-exp"
        assert response.total_tokens == 150

    @pytest.mark.xfail(reason="Mock targets httpx.AsyncClient.post but chat() delegates to _call_openrouter — payload not captured correctly")
    @pytest.mark.asyncio
    async def test_chat_with_preset(self, llm_client, monkeypatch):
        """Test chat with OpenRouter preset."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        # Reset global tracker
        start_run_tracking(job_id=2)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Test"}}],
            "model": "google/gemini-2.0-flash-exp",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        with patch.object(httpx.AsyncClient, "post", return_value=mock_response) as mock_post:
            with patch("api.services.llm.log_event"):
                await llm_client.chat(messages=[{"role": "user", "content": "Hello"}], backend="openrouter-cheapskate")

        # Verify preset was used in request
        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert payload["model"] == "@preset/cheapskate"

    @pytest.mark.asyncio
    async def test_chat_enforces_safety_guards(self, llm_client):
        """Test that chat enforces safety guards before making request."""
        # Reset global tracker
        start_run_tracking(job_id=3)

        llm_client.model_allowlist = ["allowed-model"]

        with pytest.raises(ModelNotAllowedError):
            await llm_client.chat(messages=[{"role": "user", "content": "Hello"}], model="disallowed-model")

    @pytest.mark.asyncio
    async def test_chat_http_error(self, llm_client, monkeypatch):
        """Test chat handles HTTP errors."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        # Reset global tracker
        start_run_tracking(job_id=4)

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server error", request=MagicMock(), response=mock_response
        )

        with patch.object(httpx.AsyncClient, "post", return_value=mock_response):
            with pytest.raises(httpx.HTTPStatusError):
                await llm_client.chat(messages=[{"role": "user", "content": "Hello"}])


class TestClientManagement:
    """Tests for client lifecycle management."""

    @pytest.mark.asyncio
    async def test_get_client_creates_instance(self, llm_client):
        """Test that get_client creates HTTP client."""
        client = await llm_client.get_client()

        assert client is not None
        assert isinstance(client, httpx.AsyncClient)

    @pytest.mark.asyncio
    async def test_get_client_reuses_instance(self, llm_client):
        """Test that get_client reuses existing instance."""
        client1 = await llm_client.get_client()
        client2 = await llm_client.get_client()

        assert client1 is client2

    @pytest.mark.asyncio
    async def test_close_client(self, llm_client):
        """Test closing HTTP client."""
        await llm_client.get_client()
        await llm_client.close()

        assert llm_client._http_client is None

    def test_reload_config(self, llm_client, mock_config):
        """Test reloading configuration."""
        # Modify config file
        new_config = llm_client.config.copy()
        new_config["primary_backend"] = "openrouter-big-brain"

        with open(mock_config, "w") as f:
            json.dump(new_config, f)

        llm_client.reload_config()

        assert llm_client.config["primary_backend"] == "openrouter-big-brain"

    def test_get_status(self, llm_client):
        """Test getting client status."""
        status = llm_client.get_status()

        assert "active_backend" in status
        assert "active_model" in status
        assert "primary_backend" in status
        assert "configured_preset" in status
        assert status["primary_backend"] == "openrouter"
