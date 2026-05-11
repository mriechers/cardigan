"""
Langfuse Analytics API Router

Provides endpoints for querying observability data:
- GET /api/langfuse/status - Check Langfuse connection status
- GET /api/langfuse/model-stats - Get model usage statistics from Langfuse
- GET /api/langfuse/phase-stats - Get local phase analytics from session_stats
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

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
        # Query completions grouped by phase and model
        completions_query = text("""
            SELECT
                json_extract(data, '$.phase') as phase,
                json_extract(data, '$.model') as model,
                COUNT(*) as count,
                COALESCE(SUM(json_extract(data, '$.cost')), 0) as total_cost,
                COALESCE(SUM(json_extract(data, '$.tokens')), 0) as total_tokens
            FROM session_stats
            WHERE event_type = 'phase_completed'
              AND timestamp >= :period_start
            GROUP BY phase, model
            ORDER BY phase
        """)

        # Query failures grouped by phase
        failures_query = text("""
            SELECT
                json_extract(data, '$.phase') as phase,
                COUNT(*) as count
            FROM session_stats
            WHERE event_type = 'phase_failed'
              AND timestamp >= :period_start
            GROUP BY phase
        """)

        completions_result = await session.execute(completions_query, {"period_start": period_start.isoformat()})
        completions = completions_result.fetchall()

        failures_result = await session.execute(failures_query, {"period_start": period_start.isoformat()})
        failures = failures_result.fetchall()

    # Build failure lookup: phase -> count
    failure_map: Dict[str, int] = {}
    for row in failures:
        phase = row[0]
        count = row[1]
        failure_map[phase] = count

    # Aggregate by phase
    phase_data: Dict[str, PhaseStats] = {}

    for row in completions:
        phase_name, model, count, cost, tokens = row
        if not phase_name:
            continue

        if phase_name not in phase_data:
            phase_data[phase_name] = PhaseStats(phase=phase_name)

        ps = phase_data[phase_name]

        # Get failures for this phase
        phase_failures = failure_map.get(phase_name, 0)

        # Calculate success rate for this model
        total_attempts = count + phase_failures
        success_rate = (count / total_attempts * 100) if total_attempts > 0 else 100.0

        ps.models.append(
            PhaseModelStats(
                model=model or "unknown",
                completions=count,
                failures=phase_failures,
                total_cost=float(cost or 0),
                total_tokens=int(tokens or 0),
                success_rate=round(success_rate, 1),
            )
        )

        ps.total_completions += count
        ps.total_cost += float(cost or 0)
        ps.total_tokens += int(tokens or 0)

    # Add remaining failures not captured in completions
    for phase_name, fail_count in failure_map.items():
        if phase_name not in phase_data:
            phase_data[phase_name] = PhaseStats(phase=phase_name)
        ps = phase_data[phase_name]
        ps.total_failures = fail_count

    # Calculate overall stats
    total_cost = 0.0
    total_completions = 0
    total_failures = 0

    for phase_name, ps in phase_data.items():
        total_cost += ps.total_cost
        total_completions += ps.total_completions
        total_failures += ps.total_failures

        # Calculate overall success rate
        total_attempts = ps.total_completions + ps.total_failures
        ps.success_rate = round((ps.total_completions / total_attempts * 100) if total_attempts > 0 else 0, 1)

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
    )
