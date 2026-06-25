"""Configuration API endpoints for Cardigan.

Provides endpoints for viewing and updating LLM routing configuration.
"""

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.services.config_path import resolve_config_path
from api.services.cost_estimator import estimate_job_cost
from api.services.llm import get_llm_client
from api.services.model_roster import get_available_models, invalidate_cache

router = APIRouter(prefix="/config", tags=["config"])

CONFIG_PATH = resolve_config_path()


class PhaseBackendsUpdate(BaseModel):
    """Request body for updating phase-to-backend mappings."""

    phase_backends: Dict[str, str] = Field(
        ...,
        description="Mapping of phase names to backend names",
        examples=[
            {
                "analyst": "openrouter",
                "formatter": "openrouter-cheapskate",
                "seo": "openrouter-cheapskate",
                "copy_editor": "openrouter",
            }
        ],
    )


class PhaseBackendsResponse(BaseModel):
    """Response with current phase-to-backend mappings."""

    phase_backends: Dict[str, str]
    available_backends: List[str]
    available_phases: List[str]


def _load_config() -> dict:
    """Load current config from JSON file."""
    if not CONFIG_PATH.exists():
        raise HTTPException(status_code=500, detail="Config file not found")
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _save_config(config: dict) -> None:
    """Save config to JSON file with error handling."""
    try:
        # Ensure directory exists
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
    except (IOError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")

    # Reload the LLM client's config
    llm = get_llm_client()
    llm.reload_config()


@router.get("/phase-backends", response_model=PhaseBackendsResponse)
async def get_phase_backends():
    """Get current phase-to-backend mappings.

    Returns the current configuration for which backend handles each agent phase,
    along with lists of available backends and phases.
    """
    config = _load_config()

    phase_backends = config.get("phase_backends", {})
    available_backends = list(config.get("backends", {}).keys())
    available_phases = ["analyst", "formatter", "seo", "validator", "timestamp", "copy_editor", "chat"]

    return PhaseBackendsResponse(
        phase_backends=phase_backends,
        available_backends=available_backends,
        available_phases=available_phases,
    )


@router.patch("/phase-backends", response_model=PhaseBackendsResponse)
async def update_phase_backends(update: PhaseBackendsUpdate):
    """Update phase-to-backend mappings.

    Allows reconfiguring which backend (preset tier) handles each agent phase.
    Changes are persisted to the config file and take effect immediately.
    """
    config = _load_config()
    available_backends = list(config.get("backends", {}).keys())
    valid_phases = {"analyst", "formatter", "seo", "validator", "timestamp", "copy_editor", "chat"}

    # Validate the update
    for phase, backend in update.phase_backends.items():
        if phase not in valid_phases:
            raise HTTPException(
                status_code=400, detail=f"Invalid phase: {phase}. Valid phases: {', '.join(valid_phases)}"
            )
        if backend not in available_backends:
            raise HTTPException(
                status_code=400, detail=f"Invalid backend: {backend}. Available: {', '.join(available_backends)}"
            )

    # Update config
    config["phase_backends"] = update.phase_backends
    _save_config(config)

    # Reload the live LLM client so changes take effect immediately
    from api.services.llm import get_llm_client

    get_llm_client().reload_config()

    return PhaseBackendsResponse(
        phase_backends=config["phase_backends"],
        available_backends=available_backends,
        available_phases=list(valid_phases),
    )


class AvailableModel(BaseModel):
    """A model available for phase assignment."""

    id: str = Field(..., description="OpenRouter model ID (e.g., 'anthropic/claude-sonnet-4-5-20250514')")
    name: str = Field(..., description="Human-readable model name")
    provider: str = Field(..., description="Model provider (e.g., 'Anthropic', 'Google')")
    tier: int = Field(..., ge=0, le=2, description="Cost tier (0=economy, 1=standard, 2=premium)")
    pricing_input: Optional[float] = Field(None, description="Cost per 1M input tokens (USD)")
    pricing_output: Optional[float] = Field(None, description="Cost per 1M output tokens (USD)")


class PhaseModelsResponse(BaseModel):
    """Response with current phase-to-model assignments and available models."""

    phase_models: Dict[str, str] = Field(..., description="Current model ID assigned to each phase")
    available_models: List[AvailableModel] = Field(..., description="All models available for selection")
    available_phases: List[str] = Field(..., description="All configurable phases")


class PhaseModelsUpdate(BaseModel):
    """Request body for updating phase-to-model assignments."""

    phase_models: Dict[str, str] = Field(
        ...,
        description="Mapping of phase names to model IDs",
        examples=[
            {"analyst": "anthropic/claude-haiku-4-5-20251001", "formatter": "anthropic/claude-sonnet-4-5-20250514"}
        ],
    )


DEFAULT_PHASE_MODELS = {
    "analyst": "anthropic/claude-haiku-4.5",
    "formatter": "anthropic/claude-sonnet-4.6",
    "seo": "anthropic/claude-haiku-4.5",
    "validator": "anthropic/claude-haiku-4.5",
    "timestamp": "anthropic/claude-sonnet-4.6",
    "copy_editor": "anthropic/claude-opus-4.6",
    "chat": "anthropic/claude-sonnet-4.6",
}


@router.get("/models", response_model=PhaseModelsResponse)
async def get_phase_models():
    """Get current phase-to-model assignments and available models.

    Returns which specific model is assigned to each agent phase,
    plus the full list of available models for the Settings UI.
    Models are fetched dynamically from OpenRouter when model_families
    is configured, falling back to the static available_models list.
    """
    config = _load_config()
    models_data = await get_available_models()
    available_models = [AvailableModel(**m) for m in models_data]
    phase_models = config.get("phase_models", DEFAULT_PHASE_MODELS)
    available_phases = ["analyst", "formatter", "seo", "validator", "timestamp", "copy_editor", "chat"]

    return PhaseModelsResponse(
        phase_models=phase_models,
        available_models=available_models,
        available_phases=available_phases,
    )


@router.patch("/models", response_model=PhaseModelsResponse)
async def update_phase_models(update: PhaseModelsUpdate):
    """Update phase-to-model assignments.

    Allows reconfiguring which specific model handles each agent phase.
    Changes are persisted to the config file and take effect immediately.
    """
    config = _load_config()
    valid_phases = {"analyst", "formatter", "seo", "validator", "timestamp", "copy_editor", "chat"}
    models_data = await get_available_models()
    available_model_ids = {m["id"] for m in models_data}

    for phase, model_id in update.phase_models.items():
        if phase not in valid_phases:
            raise HTTPException(
                status_code=400, detail=f"Invalid phase: {phase}. Valid phases: {', '.join(sorted(valid_phases))}"
            )
        if available_model_ids and model_id not in available_model_ids:
            raise HTTPException(status_code=400, detail=f"Unknown model: {model_id}. Check available_models in config.")

    # Merge with existing (partial update)
    phase_models = config.get("phase_models", DEFAULT_PHASE_MODELS)
    phase_models.update(update.phase_models)
    config["phase_models"] = phase_models
    _save_config(config)

    # Reload the live LLM client so changes take effect immediately
    from api.services.llm import get_llm_client

    get_llm_client().reload_config()

    return await get_phase_models()


@router.post("/models/refresh", response_model=PhaseModelsResponse)
async def refresh_model_roster():
    """Force-refresh the dynamic model roster from OpenRouter.

    Clears the cache and fetches a fresh model list. Useful when new
    models have been released and you want them immediately.
    """
    invalidate_cache()
    return await get_phase_models()


class WorkerConfigResponse(BaseModel):
    """Response with current worker configuration."""

    max_concurrent_jobs: int = Field(..., ge=1, le=5)
    poll_interval_seconds: int = Field(..., ge=1)
    heartbeat_interval_seconds: int = Field(..., ge=10)


class WorkerConfigUpdate(BaseModel):
    """Request body for updating worker configuration."""

    max_concurrent_jobs: Optional[int] = Field(None, ge=1, le=5, description="Max jobs to process concurrently (1-5)")
    poll_interval_seconds: Optional[int] = Field(None, ge=1, le=60, description="Seconds between queue polls")
    heartbeat_interval_seconds: Optional[int] = Field(None, ge=10, le=300, description="Seconds between heartbeats")


@router.get("/worker", response_model=WorkerConfigResponse)
async def get_worker_config():
    """Get current worker configuration.

    Returns settings for job processing concurrency and timing.
    """
    config = _load_config()
    worker = config.get("worker", {})

    return WorkerConfigResponse(
        max_concurrent_jobs=worker.get("max_concurrent_jobs", 3),
        poll_interval_seconds=worker.get("poll_interval_seconds", 5),
        heartbeat_interval_seconds=worker.get("heartbeat_interval_seconds", 60),
    )


@router.patch("/worker", response_model=WorkerConfigResponse)
async def update_worker_config(update: WorkerConfigUpdate):
    """Update worker configuration.

    Changes are persisted to the config file. Note: running workers will need
    to be restarted to pick up changes.
    """
    config = _load_config()
    worker = config.get(
        "worker", {"max_concurrent_jobs": 3, "poll_interval_seconds": 5, "heartbeat_interval_seconds": 60}
    )

    # Apply updates (only non-None values)
    if update.max_concurrent_jobs is not None:
        worker["max_concurrent_jobs"] = update.max_concurrent_jobs
    if update.poll_interval_seconds is not None:
        worker["poll_interval_seconds"] = update.poll_interval_seconds
    if update.heartbeat_interval_seconds is not None:
        worker["heartbeat_interval_seconds"] = update.heartbeat_interval_seconds

    # Save config
    config["worker"] = worker
    _save_config(config)

    return await get_worker_config()


class CostEstimateRequest(BaseModel):
    """Request body for cost estimation."""

    word_count: int = Field(..., ge=0, description="Transcript word count")
    phase_models: Optional[Dict[str, str]] = Field(
        None, description="Override phase-model assignments (defaults to current config)"
    )


class CostEstimateResponse(BaseModel):
    """Cost estimate for a job."""

    total_estimated_cost: float
    estimated_input_tokens: int
    phase_estimates: List[Dict[str, Any]]


@router.post("/estimate-cost", response_model=CostEstimateResponse)
async def get_cost_estimate(body: CostEstimateRequest):
    """Estimate the cost of processing a transcript.

    Uses current phase-model assignments (or overrides) and model pricing
    to estimate total cost. Useful for showing users expected costs
    before submitting a job.
    """
    config = _load_config()

    # Use provided phase_models or fall back to current config
    phase_models = body.phase_models or config.get("phase_models", DEFAULT_PHASE_MODELS)

    # Build pricing overrides from the model roster (has live OpenRouter pricing)
    pricing_overrides = {}
    try:
        models_data = await get_available_models()
        for m in models_data:
            if m.get("pricing_input") is not None and m.get("pricing_output") is not None:
                pricing_overrides[m["id"]] = {
                    "input": m["pricing_input"],
                    "output": m["pricing_output"],
                }
    except Exception:
        pass  # Fall through to MODEL_PRICING static fallback

    result = estimate_job_cost(
        word_count=body.word_count,
        phase_models=phase_models,
        pricing_overrides=pricing_overrides,
    )

    return CostEstimateResponse(**result)
