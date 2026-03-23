"""LLM service layer for Cardigan.

Provides unified interface for LLM API calls with cost tracking,
model selection, and event logging.
"""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from api.models.events import EventCreate, EventData, EventType
from api.services.database import log_event
from api.services.langfuse_client import get_langfuse_client

# Cost cap and safety configuration - can be overridden via environment
DEFAULT_RUN_COST_CAP = 1.0  # $1 per run max
DEFAULT_MAX_COST_PER_1K_TOKENS = 0.05  # $0.05 per 1K tokens max

# Model allowlist - if set, only these models are allowed
# Empty list means all models allowed
DEFAULT_MODEL_ALLOWLIST: List[str] = []


class CostCapExceededError(Exception):
    """Raised when a request would exceed the run cost cap."""

    pass


class ModelNotAllowedError(Exception):
    """Raised when a model is not in the allowlist."""

    pass


class TokenCostTooHighError(Exception):
    """Raised when a model's per-token cost exceeds the safety limit."""

    pass


# Pricing per 1M tokens (input/output) - updated Dec 2024
# These are fallback values; OpenRouter returns actual costs
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # OpenRouter free tier models (cheapskate preset)
    "xiaomi/mimo-v2-flash:free": {"input": 0.0, "output": 0.0},
    "mistralai/devstral-2-2512:free": {"input": 0.0, "output": 0.0},
    "deepseek/deepseek-r1-0528:free": {"input": 0.0, "output": 0.0},
    # OpenRouter models
    "google/gemini-2.0-flash-exp": {"input": 0.0, "output": 0.0},  # Free during preview
    "google/gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "google/gemini-3-flash-preview": {"input": 0.15, "output": 0.60},
    "google/gemini-3-pro-preview": {"input": 1.25, "output": 5.00},
    "google/gemini-pro-1.5": {"input": 1.25, "output": 5.00},
    "anthropic/claude-3.5-sonnet": {"input": 3.00, "output": 15.00},
    "anthropic/claude-sonnet-4.5": {"input": 3.00, "output": 15.00},
    "openai/gpt-4o": {"input": 2.50, "output": 10.00},
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "xai/grok-4.1-fast": {"input": 2.00, "output": 8.00},
    "moonshotai/kimi-k2-0711:free": {"input": 0.0, "output": 0.0},
    # Direct API models
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-3-5-sonnet-latest": {"input": 3.00, "output": 15.00},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-1.5-flash-8b": {"input": 0.0375, "output": 0.15},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
}


@dataclass
class LLMResponse:
    """Response from an LLM API call."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: float
    duration_ms: int
    backend: str
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class RunCostTracker:
    """Tracks cumulative costs for a processing run."""

    job_id: Optional[int] = None
    total_cost: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    call_count: int = 0
    calls: List[Dict[str, Any]] = field(default_factory=list)
    start_time: Optional[datetime] = None

    def add_call(self, response: LLMResponse) -> None:
        """Add an LLM call to the running totals."""
        self.total_cost += response.cost
        self.total_input_tokens += response.input_tokens
        self.total_output_tokens += response.output_tokens
        self.total_tokens += response.total_tokens
        self.call_count += 1
        self.calls.append(
            {
                "model": response.model,
                "backend": response.backend,
                "tokens": response.total_tokens,
                "cost": response.cost,
                "duration_ms": response.duration_ms,
            }
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return summary dict for logging."""
        return {
            "job_id": self.job_id,
            "total_cost": round(self.total_cost, 6),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "call_count": self.call_count,
        }


# Global cost tracker for current run
_current_run_tracker: Optional[RunCostTracker] = None


def start_run_tracking(job_id: Optional[int] = None) -> RunCostTracker:
    """Start tracking costs for a new processing run."""
    global _current_run_tracker
    _current_run_tracker = RunCostTracker(
        job_id=job_id,
        start_time=datetime.now(timezone.utc),
    )
    return _current_run_tracker


def get_run_tracker() -> Optional[RunCostTracker]:
    """Get the current run's cost tracker."""
    return _current_run_tracker


async def end_run_tracking() -> Optional[Dict[str, Any]]:
    """End run tracking and emit worker:completed event.

    Returns summary dict with total_cost and total_tokens.
    """
    global _current_run_tracker

    if _current_run_tracker is None:
        return None

    tracker = _current_run_tracker
    summary = tracker.to_dict()

    # Log worker:completed event
    await log_event(
        EventCreate(
            job_id=tracker.job_id,
            event_type=EventType.job_completed,
            data=EventData(
                cost=tracker.total_cost,
                tokens=tracker.total_tokens,
                extra={
                    "input_tokens": tracker.total_input_tokens,
                    "output_tokens": tracker.total_output_tokens,
                    "call_count": tracker.call_count,
                },
            ),
        )
    )

    _current_run_tracker = None
    return summary


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    openrouter_cost: Optional[float] = None,
) -> float:
    """Calculate cost for an API call.

    Uses OpenRouter-reported cost if available, otherwise estimates
    from MODEL_PRICING table.

    Args:
        model: Model identifier
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        openrouter_cost: Cost reported by OpenRouter (if available)

    Returns:
        Cost in USD
    """
    # Prefer OpenRouter's reported cost
    if openrouter_cost is not None:
        return openrouter_cost

    # Look up pricing
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        # Unknown model - estimate conservatively
        pricing = {"input": 1.0, "output": 3.0}  # $1/M input, $3/M output

    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]

    return input_cost + output_cost


class LLMClient:
    """Unified client for LLM API calls with cost tracking."""

    def __init__(self, config_path: Optional[str] = None):
        """Initialize client with config.

        Args:
            config_path: Path to llm-config.json (default: config/llm-config.json)
        """
        if config_path is None:
            config_path = "config/llm-config.json"

        self.config_path = Path(config_path)
        self.config = self._load_config()
        self._http_client: Optional[httpx.AsyncClient] = None

        # Track active model/preset for health endpoint
        self.active_backend: Optional[str] = None
        self.active_model: Optional[str] = None
        self.active_preset: Optional[str] = None

        # Load safety guards from env/config
        self._load_safety_config()

    def _load_safety_config(self) -> None:
        """Load cost cap and allowlist configuration from environment/config."""
        # Run cost cap (per-run maximum)
        self.run_cost_cap = float(
            os.getenv("LLM_RUN_COST_CAP", self.config.get("safety", {}).get("run_cost_cap", DEFAULT_RUN_COST_CAP))
        )

        # Max cost per 1K tokens (safety against expensive models)
        self.max_cost_per_1k_tokens = float(
            os.getenv(
                "LLM_MAX_COST_PER_1K_TOKENS",
                self.config.get("safety", {}).get("max_cost_per_1k_tokens", DEFAULT_MAX_COST_PER_1K_TOKENS),
            )
        )

        # Model allowlist
        allowlist_env = os.getenv("LLM_MODEL_ALLOWLIST", "")
        if allowlist_env:
            self.model_allowlist = [m.strip() for m in allowlist_env.split(",") if m.strip()]
        else:
            self.model_allowlist = self.config.get("safety", {}).get("model_allowlist", DEFAULT_MODEL_ALLOWLIST)

        # Whether to enforce guards (can disable for testing)
        self.enforce_guards = os.getenv("LLM_ENFORCE_GUARDS", "true").lower() == "true"

    def check_model_allowed(self, model: str) -> None:
        """Check if model is in the allowlist.

        Raises:
            ModelNotAllowedError: If model is not allowed
        """
        if not self.enforce_guards:
            return

        if not self.model_allowlist:
            return  # Empty allowlist = all models allowed

        # Check exact match or prefix match (for versioned models)
        for allowed in self.model_allowlist:
            if model == allowed or model.startswith(allowed + ":"):
                return

        raise ModelNotAllowedError(
            f"Model '{model}' is not in allowlist. " f"Allowed: {', '.join(self.model_allowlist)}"
        )

    def check_token_cost(self, model: str) -> None:
        """Check if model's per-token cost is within safety limits.

        Raises:
            TokenCostTooHighError: If model is too expensive
        """
        if not self.enforce_guards:
            return

        pricing = MODEL_PRICING.get(model)
        if pricing is None:
            # Unknown model - be conservative and allow (but log warning)
            return

        # Calculate average cost per 1K tokens (weighted toward output)
        avg_cost_per_1k = (pricing["input"] + pricing["output"] * 2) / 3 / 1000

        if avg_cost_per_1k > self.max_cost_per_1k_tokens:
            raise TokenCostTooHighError(
                f"Model '{model}' costs ~${avg_cost_per_1k:.4f}/1K tokens, "
                f"exceeds limit of ${self.max_cost_per_1k_tokens:.4f}/1K"
            )

    def check_run_cost_cap(self) -> None:
        """Check if current run is approaching cost cap.

        Raises:
            CostCapExceededError: If cap would be exceeded
        """
        if not self.enforce_guards:
            return

        tracker = get_run_tracker()
        if tracker is None:
            return

        if tracker.total_cost >= self.run_cost_cap:
            raise CostCapExceededError(
                f"Run cost ${tracker.total_cost:.4f} has reached cap of ${self.run_cost_cap:.2f}. "
                f"Increase LLM_RUN_COST_CAP or use a cheaper model."
            )

    def _load_config(self) -> Dict[str, Any]:
        """Load LLM configuration from file."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"LLM config not found: {self.config_path}")

        with open(self.config_path) as f:
            return json.load(f)

    def reload_config(self) -> None:
        """Reload configuration from file."""
        self.config = self._load_config()

    async def get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            # Ensure old client is properly closed before creating new one
            if self._http_client is not None:
                try:
                    await self._http_client.aclose()
                except Exception:
                    pass  # Already broken, ignore
            self._http_client = httpx.AsyncClient(timeout=180.0)
        return self._http_client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    def get_backend_config(self, backend_name: Optional[str] = None) -> Dict[str, Any]:
        """Get configuration for a specific backend.

        Args:
            backend_name: Backend name, or None for primary backend

        Returns:
            Backend configuration dict
        """
        if backend_name is None:
            backend_name = self.config.get("primary_backend", "openrouter")

        backends = self.config.get("backends", {})
        if backend_name not in backends:
            raise ValueError(f"Unknown backend: {backend_name}")

        return backends[backend_name]

    def get_backend_for_phase(
        self, phase: str, context: Optional[Dict[str, Any]] = None, tier_override: Optional[int] = None
    ) -> str:
        """Get the configured backend for a specific agent phase.

        Supports tiered routing based on transcript duration and explicit tier override.

        Args:
            phase: Phase name (e.g., 'analyst', 'formatter', 'seo', 'copy_editor')
            context: Optional context dict with transcript_metrics
            tier_override: Optional tier index to use instead of calculated tier

        Returns:
            Backend name to use for this phase
        """
        routing_config = self.config.get("routing", {})
        tiers = routing_config.get("tiers", ["openrouter-cheapskate", "openrouter", "openrouter-big-brain"])

        # Get base tier for this phase (default to tier 0 = cheapskate)
        phase_base_tiers = routing_config.get("phase_base_tiers", {})
        base_tier = phase_base_tiers.get(phase, 0)

        # If tier override provided, use it directly
        if tier_override is not None:
            selected_tier = min(tier_override, len(tiers) - 1)
        else:
            # Calculate tier based on transcript duration
            selected_tier = base_tier

            if context:
                transcript_metrics = context.get("transcript_metrics", {})
                estimated_duration = transcript_metrics.get("estimated_duration_minutes", 0)

                # Find appropriate tier based on duration thresholds
                duration_thresholds = routing_config.get("duration_thresholds", [])
                for threshold in duration_thresholds:
                    max_minutes = threshold.get("max_minutes")
                    tier = threshold.get("tier", 0)

                    if max_minutes is None or estimated_duration <= max_minutes:
                        # Use the higher of base tier or duration-based tier
                        selected_tier = max(base_tier, tier)
                        break
                else:
                    # No threshold matched, use max tier
                    selected_tier = max(base_tier, len(tiers) - 1)

        # Get backend for selected tier
        if selected_tier < len(tiers):
            backend = tiers[selected_tier]
            # Validate backend exists
            if backend in self.config.get("backends", {}):
                return backend

        # Fall back to phase_backends config or primary backend
        phase_backends = self.config.get("phase_backends", {})
        return phase_backends.get(phase, self.config.get("primary_backend", "openrouter"))

    def get_tier_for_phase(self, phase: str, context: Optional[Dict[str, Any]] = None) -> int:
        """Get the calculated tier index for a phase based on context.

        Args:
            phase: Phase name
            context: Optional context dict with transcript_metrics

        Returns:
            Tier index (0 = cheapskate, 1 = default, 2 = big-brain)
        """
        tier, _ = self.get_tier_for_phase_with_reason(phase, context)
        return tier

    def get_tier_for_phase_with_reason(self, phase: str, context: Optional[Dict[str, Any]] = None) -> tuple:
        """Get the calculated tier index and reason for a phase.

        Args:
            phase: Phase name
            context: Optional context dict with transcript_metrics

        Returns:
            Tuple of (tier index, reason string)
        """
        routing_config = self.config.get("routing", {})
        tiers = routing_config.get("tiers", ["openrouter-cheapskate", "openrouter", "openrouter-big-brain"])

        # Get base tier for this phase
        phase_base_tiers = routing_config.get("phase_base_tiers", {})
        base_tier = phase_base_tiers.get(phase, 0)

        if not context:
            return base_tier, f"phase default (base tier {base_tier})"

        transcript_metrics = context.get("transcript_metrics", {})
        estimated_duration = transcript_metrics.get("estimated_duration_minutes", 0)

        # Find appropriate tier based on duration thresholds
        duration_thresholds = routing_config.get("duration_thresholds", [])
        for threshold in duration_thresholds:
            max_minutes = threshold.get("max_minutes")
            tier = threshold.get("tier", 0)

            if max_minutes is None or estimated_duration <= max_minutes:
                selected_tier = max(base_tier, tier)
                if selected_tier > base_tier:
                    reason = f"duration {estimated_duration:.0f}min (threshold: ≤{max_minutes}min → tier {tier})"
                else:
                    reason = f"phase default (base tier {base_tier})"
                return selected_tier, reason

        # No threshold matched, use max tier
        max_tier = len(tiers) - 1
        return max(base_tier, max_tier), f"duration {estimated_duration:.0f}min exceeds all thresholds"

    def get_next_tier(self, current_tier: int) -> Optional[int]:
        """Get the next escalation tier, or None if at max.

        Args:
            current_tier: Current tier index

        Returns:
            Next tier index, or None if already at max
        """
        routing_config = self.config.get("routing", {})
        tiers = routing_config.get("tiers", ["openrouter-cheapskate", "openrouter", "openrouter-big-brain"])

        if current_tier < len(tiers) - 1:
            return current_tier + 1
        return None

    def get_escalation_config(self) -> Dict[str, Any]:
        """Get escalation configuration.

        Returns:
            Dict with escalation settings (enabled, on_failure, on_timeout, etc.)
        """
        routing_config = self.config.get("routing", {})
        return routing_config.get(
            "escalation",
            {
                "enabled": True,
                "on_failure": True,
                "on_timeout": True,
                "timeout_seconds": 120,
                "max_retries_per_tier": 1,
            },
        )

    def get_api_key(self, backend_config: Dict[str, Any]) -> Optional[str]:
        """Get API key for a backend from environment."""
        key_env = backend_config.get("api_key_env")
        if key_env:
            return os.getenv(key_env)
        return None

    async def chat(
        self,
        messages: List[Dict[str, str]],
        backend: Optional[str] = None,
        model: Optional[str] = None,
        preset: Optional[str] = None,
        job_id: Optional[int] = None,
        phase: Optional[str] = None,
        tier: Optional[int] = None,
        tier_label: Optional[str] = None,
        **kwargs,
    ) -> LLMResponse:
        """Make a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            backend: Backend to use (default: primary)
            model: Model override (default: backend's configured model)
            preset: OpenRouter preset override (default: backend's configured preset)
            job_id: Job ID for event logging
            phase: Agent phase name for observability (analyst, formatter, etc.)
            tier: Tier index for observability (0=cheapskate, 1=default, 2=big-brain)
            tier_label: Human-readable tier name
            **kwargs: Additional parameters passed to the API

        Returns:
            LLMResponse with content, tokens, and cost
        """
        backend_name = backend or self.config.get("primary_backend", "openrouter")
        backend_config = self.get_backend_config(backend_name)

        # Determine model - for OpenRouter with preset, use @preset/name syntax
        preset_name = preset or backend_config.get("preset")
        if preset_name and backend_config.get("type") == "openrouter":
            model_id = f"@preset/{preset_name}"
            self.active_preset = preset_name
        else:
            model_id = model or backend_config.get("model") or backend_config.get("fallback_model")
            self.active_preset = None

        self.active_backend = backend_name
        self.active_model = model_id

        # Safety guards - check before making request
        self.check_run_cost_cap()
        self.check_model_allowed(model_id)
        self.check_token_cost(model_id)

        # Get API key
        api_key = self.get_api_key(backend_config)

        # Build request based on backend type
        backend_type = backend_config.get("type", "openai")

        start_time = time.time()

        if backend_type == "openrouter":
            response = await self._call_openrouter(backend_config, model_id, messages, api_key, **kwargs)
        elif backend_type == "openai":
            response = await self._call_openai(backend_config, model_id, messages, api_key, **kwargs)
        elif backend_type == "anthropic":
            response = await self._call_anthropic(backend_config, model_id, messages, api_key, **kwargs)
        elif backend_type == "gemini":
            response = await self._call_gemini(backend_config, model_id, messages, api_key, **kwargs)
        else:
            raise ValueError(f"Unsupported backend type: {backend_type}")

        duration_ms = int((time.time() - start_time) * 1000)
        response.duration_ms = duration_ms
        response.backend = backend_name

        # Track costs
        if _current_run_tracker is not None:
            _current_run_tracker.add_call(response)

        # Log cost_update event
        await log_event(
            EventCreate(
                job_id=job_id,
                event_type=EventType.cost_update,
                data=EventData(
                    cost=response.cost,
                    tokens=response.total_tokens,
                    model=response.model,
                    backend=backend_name,
                    duration_ms=duration_ms,
                ),
            )
        )

        # Send trace to Langfuse for observability
        langfuse = get_langfuse_client()
        if langfuse.is_available():
            await langfuse.trace_generation(
                name=f"{phase}-generation" if phase else "llm-generation",
                model=response.model,
                input_messages=messages,
                output=response.content,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                total_tokens=response.total_tokens,
                cost=response.cost,
                duration_ms=duration_ms,
                job_id=job_id,
                phase=phase,
                tier=tier,
                tier_label=tier_label,
                backend=backend_name,
            )

        return response

    async def _call_openrouter(
        self,
        config: Dict[str, Any],
        model: str,
        messages: List[Dict[str, str]],
        api_key: Optional[str],
        **kwargs,
    ) -> LLMResponse:
        """Make OpenRouter API call."""
        client = await self.get_client()

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://pbswisconsin.org",
            "X-Title": "Cardigan",
        }

        payload = {
            "model": model,
            "messages": messages,
            **kwargs,
        }

        response = await client.post(
            config["endpoint"],
            headers=headers,
            json=payload,
        )

        # Log error details before raising
        if response.status_code >= 400:
            try:
                error_body = response.json()
                print(f"[LLM] OpenRouter API error status={response.status_code} model={model} error={error_body}")
            except Exception:
                print(
                    f"[LLM] OpenRouter API error (non-JSON) status={response.status_code} "
                    f"model={model} response={response.text[:500]}"
                )

        response.raise_for_status()

        data = response.json()

        # Extract usage
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", input_tokens + output_tokens)

        # OpenRouter may report cost directly
        openrouter_cost = None
        if "usage" in data and "total_cost" in data["usage"]:
            openrouter_cost = data["usage"]["total_cost"]

        # Calculate cost (force $0 for free tier models)
        actual_model = data.get("model", model)
        if actual_model.endswith(":free"):
            cost = 0.0
        else:
            cost = calculate_cost(actual_model, input_tokens, output_tokens, openrouter_cost)

        # Extract content
        content = data["choices"][0]["message"]["content"]
        actual_model = data.get("model", model)

        return LLMResponse(
            content=content,
            model=actual_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost=cost,
            duration_ms=0,  # Set by caller
            backend="openrouter",
            raw_response=data,
        )

    async def _call_openai(
        self,
        config: Dict[str, Any],
        model: str,
        messages: List[Dict[str, str]],
        api_key: Optional[str],
        **kwargs,
    ) -> LLMResponse:
        """Make OpenAI API call."""
        client = await self.get_client()

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": messages,
            **kwargs,
        }

        response = await client.post(
            config["endpoint"],
            headers=headers,
            json=payload,
        )
        response.raise_for_status()

        data = response.json()

        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", input_tokens + output_tokens)

        cost = calculate_cost(model, input_tokens, output_tokens)
        content = data["choices"][0]["message"]["content"]

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost=cost,
            duration_ms=0,
            backend="openai",
            raw_response=data,
        )

    async def _call_anthropic(
        self,
        config: Dict[str, Any],
        model: str,
        messages: List[Dict[str, str]],
        api_key: Optional[str],
        **kwargs,
    ) -> LLMResponse:
        """Make Anthropic API call."""
        client = await self.get_client()

        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        # Convert messages format for Anthropic
        system_msg = None
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                anthropic_messages.append(msg)

        payload = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens", 4096),
        }
        if system_msg:
            payload["system"] = system_msg

        response = await client.post(
            config["endpoint"],
            headers=headers,
            json=payload,
        )
        response.raise_for_status()

        data = response.json()

        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total_tokens = input_tokens + output_tokens

        cost = calculate_cost(model, input_tokens, output_tokens)
        content = data["content"][0]["text"]

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost=cost,
            duration_ms=0,
            backend="anthropic",
            raw_response=data,
        )

    async def _call_gemini(
        self,
        config: Dict[str, Any],
        model: str,
        messages: List[Dict[str, str]],
        api_key: Optional[str],
        **kwargs,
    ) -> LLMResponse:
        """Make Google Gemini API call."""
        client = await self.get_client()

        # Build endpoint with API key
        endpoint = f"{config['endpoint']}?key={api_key}"

        # Convert messages to Gemini format
        contents = []
        for msg in messages:
            role = "user" if msg["role"] in ("user", "system") else "model"
            contents.append(
                {
                    "role": role,
                    "parts": [{"text": msg["content"]}],
                }
            )

        payload = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": kwargs.get("max_tokens", 8192),
            },
        }

        response = await client.post(
            endpoint,
            json=payload,
        )
        response.raise_for_status()

        data = response.json()

        # Extract usage metadata
        usage = data.get("usageMetadata", {})
        input_tokens = usage.get("promptTokenCount", 0)
        output_tokens = usage.get("candidatesTokenCount", 0)
        total_tokens = usage.get("totalTokenCount", input_tokens + output_tokens)

        cost = calculate_cost(model, input_tokens, output_tokens)
        content = data["candidates"][0]["content"]["parts"][0]["text"]

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost=cost,
            duration_ms=0,
            backend="gemini",
            raw_response=data,
        )

    def get_status(self) -> Dict[str, Any]:
        """Get current LLM client status for health endpoint.

        Returns:
            Dict with active/configured backend, model, preset, and last_run_totals
        """
        tracker = get_run_tracker()
        last_run = tracker.to_dict() if tracker else None

        # Get configured settings from primary backend
        primary_backend = self.config.get("primary_backend")
        configured_preset = None
        fallback_model = None
        if primary_backend:
            backend_config = self.config.get("backends", {}).get(primary_backend, {})
            configured_preset = backend_config.get("preset")
            fallback_model = backend_config.get("fallback_model") or backend_config.get("model")

        # Get phase-to-backend mapping
        phase_backends = self.config.get("phase_backends", {})

        # Get OpenRouter preset details (manually maintained)
        openrouter_presets = self.config.get("openrouter_presets", {})

        return {
            "active_backend": self.active_backend,
            "active_model": self.active_model,
            "active_preset": self.active_preset,
            "primary_backend": primary_backend,
            "configured_preset": configured_preset,
            "fallback_model": fallback_model,
            "phase_backends": phase_backends,
            "openrouter_presets": openrouter_presets,
            "last_run_totals": last_run,
        }


# Global LLM client instance
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Get or create global LLM client instance."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


async def close_llm_client() -> None:
    """Close global LLM client."""
    global _llm_client
    if _llm_client is not None:
        await _llm_client.close()
        _llm_client = None
