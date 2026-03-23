"""Configuration API endpoints for Cardigan.

Provides endpoints for viewing and updating LLM routing configuration.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.services.llm import get_llm_client

router = APIRouter(prefix="/config", tags=["config"])

# Config file path
CONFIG_PATH = Path("config/llm-config.json")


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


class DurationThreshold(BaseModel):
    """A single duration threshold for tier selection."""

    max_minutes: Optional[int] = Field(None, description="Max minutes for this tier (null = unlimited)")
    tier: int = Field(..., ge=0, le=2, description="Tier index (0=cheapskate, 1=default, 2=big-brain)")


class EscalationConfig(BaseModel):
    """Configuration for failure-based escalation."""

    enabled: bool = Field(True, description="Whether escalation is enabled")
    on_failure: bool = Field(True, description="Escalate on LLM failure")
    on_timeout: bool = Field(True, description="Escalate on timeout")
    timeout_seconds: int = Field(120, ge=30, le=600, description="Timeout before escalation")
    max_retries_per_tier: int = Field(1, ge=1, le=3, description="Max retries before escalating")


class RoutingConfigUpdate(BaseModel):
    """Request body for updating routing configuration."""

    duration_thresholds: Optional[List[DurationThreshold]] = Field(
        None, description="Duration thresholds for tier selection"
    )
    phase_base_tiers: Optional[Dict[str, int]] = Field(
        None, description="Base tier for each phase (0=cheapskate, 1=default, 2=big-brain)"
    )
    escalation: Optional[EscalationConfig] = Field(None, description="Escalation settings")


class PhaseBackendsResponse(BaseModel):
    """Response with current phase-to-backend mappings."""

    phase_backends: Dict[str, str]
    available_backends: List[str]
    available_phases: List[str]


class RoutingConfigResponse(BaseModel):
    """Response with current routing configuration."""

    tiers: List[str]
    tier_labels: List[str]
    duration_thresholds: List[DurationThreshold]
    phase_base_tiers: Dict[str, int]
    escalation: EscalationConfig


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
    available_phases = ["analyst", "formatter", "seo", "manager", "copy_editor", "chat"]

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
    valid_phases = {"analyst", "formatter", "seo", "manager", "copy_editor", "chat"}

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

    return PhaseBackendsResponse(
        phase_backends=config["phase_backends"],
        available_backends=available_backends,
        available_phases=list(valid_phases),
    )


@router.get("/routing", response_model=RoutingConfigResponse)
async def get_routing_config():
    """Get current tiered routing configuration.

    Returns settings for duration-based tier selection and failure escalation.
    """
    config = _load_config()
    routing = config.get("routing", {})

    # Default values
    default_tiers = ["openrouter-cheapskate", "openrouter", "openrouter-big-brain"]
    default_labels = ["cheapskate", "default", "big-brain"]
    default_thresholds = [
        {"max_minutes": 15, "tier": 0},
        {"max_minutes": 30, "tier": 1},
        {"max_minutes": None, "tier": 2},
    ]
    default_phase_tiers = {"analyst": 1, "formatter": 0, "seo": 0, "manager": 2, "copy_editor": 1, "chat": 1}
    default_escalation = {
        "enabled": True,
        "on_failure": True,
        "on_timeout": True,
        "timeout_seconds": 120,
        "max_retries_per_tier": 1,
    }

    return RoutingConfigResponse(
        tiers=routing.get("tiers", default_tiers),
        tier_labels=routing.get("tier_labels", default_labels),
        duration_thresholds=[DurationThreshold(**t) for t in routing.get("duration_thresholds", default_thresholds)],
        phase_base_tiers=routing.get("phase_base_tiers", default_phase_tiers),
        escalation=EscalationConfig(**routing.get("escalation", default_escalation)),
    )


@router.patch("/routing", response_model=RoutingConfigResponse)
async def update_routing_config(update: RoutingConfigUpdate):
    """Update tiered routing configuration.

    Allows reconfiguring duration thresholds, phase base tiers, and escalation settings.
    Changes are persisted to the config file and take effect immediately.
    """
    config = _load_config()
    routing = config.get("routing", {})
    valid_phases = {"analyst", "formatter", "seo", "manager", "copy_editor", "chat"}

    # Apply updates (only non-None values)
    if update.duration_thresholds is not None:
        routing["duration_thresholds"] = [
            {"max_minutes": t.max_minutes, "tier": t.tier} for t in update.duration_thresholds
        ]

    if update.phase_base_tiers is not None:
        # Validate phases
        for phase, tier in update.phase_base_tiers.items():
            if phase not in valid_phases:
                raise HTTPException(
                    status_code=400, detail=f"Invalid phase: {phase}. Valid phases: {', '.join(valid_phases)}"
                )
            if tier < 0 or tier > 2:
                raise HTTPException(status_code=400, detail=f"Invalid tier {tier} for {phase}. Must be 0, 1, or 2.")
        routing["phase_base_tiers"] = update.phase_base_tiers

    if update.escalation is not None:
        routing["escalation"] = update.escalation.model_dump()

    # Save config
    config["routing"] = routing
    _save_config(config)

    # Return updated config
    return await get_routing_config()


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
