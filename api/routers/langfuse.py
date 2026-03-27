"""
Langfuse Analytics API Router

Provides endpoints for querying observability data:
- GET /api/langfuse/status - Check Langfuse connection status
- GET /api/langfuse/model-stats - Get model usage statistics from Langfuse
- GET /api/langfuse/phase-stats - Get local phase analytics from session_stats
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from api.services.database import get_session
from api.services.langfuse_client import get_langfuse_client

router = APIRouter()


class ModelStatsItem(BaseModel):
    """Statistics for a single model."""

    model_name: str = Field(..., description="Model identifier (e.g., 'anthropic/claude-3-5-sonnet')")
    request_count: int = Field(..., description="Number of requests to this model")
    total_cost: float = Field(..., description="Total cost in USD")
    total_tokens: int = Field(..., description="Total tokens used")
    avg_latency_ms: Optional[float] = Field(None, description="Average latency in milliseconds")
    cost_percentage: Optional[float] = Field(None, description="Percentage of total cost")


class ModelStatsResponse(BaseModel):
    """Response containing model usage statistics."""

    available: bool = Field(..., description="Whether Langfuse data is available")
    error: Optional[str] = Field(None, description="Error message if unavailable")
    models: List[ModelStatsItem] = Field(default_factory=list, description="Model usage statistics")
    period_start: Optional[datetime] = Field(None, description="Start of the reporting period")
    period_end: Optional[datetime] = Field(None, description="End of the reporting period")
    period_days: int = Field(7, description="Number of days in the reporting period")
    total_cost: float = Field(0.0, description="Total cost across all models")
    total_requests: int = Field(0, description="Total requests across all models")


class LangfuseStatusResponse(BaseModel):
    """Langfuse connection status."""

    available: bool = Field(..., description="Whether Langfuse is configured and reachable")
    error: Optional[str] = Field(None, description="Error message if unavailable")
    host: str = Field(..., description="Langfuse host URL")


@router.get("/status", response_model=LangfuseStatusResponse)
async def get_langfuse_status():
    """
    Check Langfuse connection status.

    Returns whether Langfuse is properly configured and the host URL.
    """
    client = get_langfuse_client()
    status = client.get_status()
    return LangfuseStatusResponse(**status)


@router.get("/model-stats", response_model=ModelStatsResponse)
async def get_model_stats(
    days: int = Query(default=7, ge=1, le=90, description="Number of days to look back"),
    limit: int = Query(default=10, ge=1, le=50, description="Maximum number of models to return"),
):
    """
    Get model usage statistics from Langfuse.

    Returns aggregated statistics for each model used, including:
    - Request count
    - Total cost
    - Total tokens
    - Average latency

    Data is grouped by the actual model OpenRouter selected (providedModelName),
    not the preset configuration.
    """
    client = get_langfuse_client()

    if not client.is_available():
        status = client.get_status()
        return ModelStatsResponse(
            available=False,
            error=status.get("error", "Langfuse not available"),
            period_days=days,
        )

    stats = await client.get_model_stats(days=days, limit=limit)

    if stats is None:
        return ModelStatsResponse(
            available=False,
            error="Failed to fetch model stats from Langfuse",
            period_days=days,
        )

    # Convert to response format and calculate percentages
    models = []
    for model in stats.models:
        cost_pct = (model.total_cost / stats.total_cost * 100) if stats.total_cost > 0 else 0
        models.append(
            ModelStatsItem(
                model_name=model.model_name,
                request_count=model.request_count,
                total_cost=model.total_cost,
                total_tokens=model.total_tokens,
                avg_latency_ms=model.avg_latency_ms,
                cost_percentage=round(cost_pct, 1),
            )
        )

    return ModelStatsResponse(
        available=True,
        models=models,
        period_start=stats.period_start,
        period_end=stats.period_end,
        period_days=days,
        total_cost=stats.total_cost,
        total_requests=stats.total_requests,
    )


# ============================================================================
# Local Phase Analytics (from session_stats table)
# ============================================================================


class PhaseModelStats(BaseModel):
    """Statistics for a model within a phase."""

    model: str = Field(..., description="Model identifier")
    tier: Optional[int] = Field(None, description="Tier index (0=cheapskate, 1=default, 2=big-brain)")
    tier_label: Optional[str] = Field(None, description="Human-readable tier name")
    completions: int = Field(0, description="Successful completions")
    failures: int = Field(0, description="Failed attempts")
    total_cost: float = Field(0.0, description="Total cost in USD")
    total_tokens: int = Field(0, description="Total tokens used")
    success_rate: float = Field(0.0, description="Success rate as percentage")


class PhaseStats(BaseModel):
    """Aggregated statistics for an agent phase."""

    phase: str = Field(..., description="Phase name (analyst, formatter, seo, etc.)")
    total_completions: int = Field(0, description="Total successful completions")
    total_failures: int = Field(0, description="Total failed attempts")
    total_cost: float = Field(0.0, description="Total cost across all models")
    total_tokens: int = Field(0, description="Total tokens across all models")
    success_rate: float = Field(0.0, description="Overall success rate")
    escalation_rate: float = Field(0.0, description="Percentage that escalated from base tier")
    models: List[PhaseModelStats] = Field(default_factory=list, description="Per-model breakdown")


class PhaseStatsResponse(BaseModel):
    """Response containing phase-level analytics."""

    phases: List[PhaseStats] = Field(default_factory=list)
    period_start: datetime
    period_end: datetime
    period_days: int
    total_cost: float = Field(0.0)
    total_completions: int = Field(0)
    total_failures: int = Field(0)
    escalation_summary: Dict[str, Any] = Field(default_factory=dict)


@router.get("/phase-stats", response_model=PhaseStatsResponse)
async def get_phase_stats(
    days: int = Query(default=7, ge=1, le=90, description="Number of days to look back"),
):
    """
    Get phase-level analytics from local session_stats.

    Returns per-phase breakdown showing:
    - Which models are used for each role
    - Success/failure rates per tier
    - Escalation patterns (started cheap, finished expensive)
    - Cost attribution by phase

    This data comes from local SQLite, not Langfuse.
    """
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=days)

    async with get_session() as session:
        # Query completions grouped by phase, model, tier
        completions_query = text("""
            SELECT
                json_extract(data, '$.phase') as phase,
                json_extract(data, '$.model') as model,
                json_extract(data, '$.extra.tier') as tier,
                json_extract(data, '$.extra.tier_label') as tier_label,
                COUNT(*) as count,
                COALESCE(SUM(json_extract(data, '$.cost')), 0) as total_cost,
                COALESCE(SUM(json_extract(data, '$.tokens')), 0) as total_tokens
            FROM session_stats
            WHERE event_type = 'phase_completed'
              AND timestamp >= :period_start
            GROUP BY phase, model, tier
            ORDER BY phase, tier
        """)

        # Query failures grouped by phase, tier
        failures_query = text("""
            SELECT
                json_extract(data, '$.phase') as phase,
                json_extract(data, '$.extra.tier') as tier,
                json_extract(data, '$.extra.tier_label') as tier_label,
                COUNT(*) as count
            FROM session_stats
            WHERE event_type = 'phase_failed'
              AND timestamp >= :period_start
            GROUP BY phase, tier
        """)

        completions_result = await session.execute(completions_query, {"period_start": period_start.isoformat()})
        completions = completions_result.fetchall()

        failures_result = await session.execute(failures_query, {"period_start": period_start.isoformat()})
        failures = failures_result.fetchall()

    # Build failure lookup: phase -> tier -> count
    failure_map: Dict[str, Dict[Optional[int], int]] = {}
    for row in failures:
        phase = row[0]
        tier = int(row[1]) if row[1] is not None else None
        count = row[3]
        if phase not in failure_map:
            failure_map[phase] = {}
        failure_map[phase][tier] = count

    # Aggregate by phase
    phase_data: Dict[str, PhaseStats] = {}

    for row in completions:
        phase_name, model, tier_raw, tier_label, count, cost, tokens = row
        if not phase_name:
            continue

        tier = int(tier_raw) if tier_raw is not None else None

        if phase_name not in phase_data:
            phase_data[phase_name] = PhaseStats(phase=phase_name)

        ps = phase_data[phase_name]

        # Get failures for this phase/tier combo
        phase_failures = failure_map.get(phase_name, {})
        tier_failures = phase_failures.get(tier, 0)

        # Calculate success rate for this model/tier
        total_attempts = count + tier_failures
        success_rate = (count / total_attempts * 100) if total_attempts > 0 else 100.0

        ps.models.append(
            PhaseModelStats(
                model=model or "unknown",
                tier=tier,
                tier_label=tier_label,
                completions=count,
                failures=tier_failures,
                total_cost=float(cost or 0),
                total_tokens=int(tokens or 0),
                success_rate=round(success_rate, 1),
            )
        )

        ps.total_completions += count
        ps.total_cost += float(cost or 0)
        ps.total_tokens += int(tokens or 0)

    # Add remaining failures not captured in completions
    for phase_name, tier_failures in failure_map.items():
        if phase_name not in phase_data:
            phase_data[phase_name] = PhaseStats(phase=phase_name)
        ps = phase_data[phase_name]
        ps.total_failures = sum(tier_failures.values())

    # Calculate overall stats and escalation rates
    total_cost = 0.0
    total_completions = 0
    total_failures = 0
    escalation_summary = {"by_phase": {}}

    for phase_name, ps in phase_data.items():
        total_cost += ps.total_cost
        total_completions += ps.total_completions
        total_failures += ps.total_failures

        # Calculate overall success rate
        total_attempts = ps.total_completions + ps.total_failures
        ps.success_rate = round((ps.total_completions / total_attempts * 100) if total_attempts > 0 else 0, 1)

        # Calculate escalation rate (how many finished at tier > 0)
        base_tier_completions = sum(m.completions for m in ps.models if m.tier == 0 or m.tier is None)
        escalated_completions = sum(m.completions for m in ps.models if m.tier is not None and m.tier > 0)
        if ps.total_completions > 0:
            ps.escalation_rate = round(escalated_completions / ps.total_completions * 100, 1)

        escalation_summary["by_phase"][phase_name] = {
            "base_tier": base_tier_completions,
            "escalated": escalated_completions,
            "rate": ps.escalation_rate,
        }

    # Sort phases by name
    phases = sorted(phase_data.values(), key=lambda p: p.phase)

    return PhaseStatsResponse(
        phases=phases,
        period_start=period_start,
        period_end=period_end,
        period_days=days,
        total_cost=round(total_cost, 4),
        total_completions=total_completions,
        total_failures=total_failures,
        escalation_summary=escalation_summary,
    )
