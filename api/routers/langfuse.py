"""
Langfuse Analytics API Router

Provides endpoints for querying observability data:
- GET /api/langfuse/status - Check Langfuse connection status
- GET /api/langfuse/model-stats - Get model usage statistics from Langfuse
- GET /api/langfuse/phase-stats - Get local phase analytics from session_stats
- GET /api/langfuse/model-timeline - Get model drift timeline from session_stats
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


class EscalationReasonBreakdown(BaseModel):
    """Breakdown of escalation reasons for a phase."""

    timeout: int = 0
    api_error: int = 0
    truncation: int = 0
    other: int = 0


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
    escalation_reasons: Optional[EscalationReasonBreakdown] = None


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

        # Query escalation reasons grouped by phase
        escalation_reasons_query = text("""
            SELECT
                json_extract(data, '$.phase') as phase,
                json_extract(data, '$.extra.reason') as reason,
                COUNT(*) as count
            FROM session_stats
            WHERE event_type = 'phase_started'
              AND json_extract(data, '$.extra.escalation') = 1
              AND timestamp >= :period_start
            GROUP BY phase, reason
        """)

        completions_result = await session.execute(completions_query, {"period_start": period_start.isoformat()})
        completions = completions_result.fetchall()

        failures_result = await session.execute(failures_query, {"period_start": period_start.isoformat()})
        failures = failures_result.fetchall()

        escalation_reasons_result = await session.execute(
            escalation_reasons_query, {"period_start": period_start.isoformat()}
        )
        escalation_reason_rows = escalation_reasons_result.fetchall()

    # Build failure lookup: phase -> tier -> count
    failure_map: Dict[str, Dict[Optional[int], int]] = {}
    for row in failures:
        phase = row[0]
        tier = int(row[1]) if row[1] is not None else None
        count = row[3]
        if phase not in failure_map:
            failure_map[phase] = {}
        failure_map[phase][tier] = count

    def _classify_reason(reason: Optional[str]) -> str:
        if not reason:
            return "other"
        if "timeout" in reason.lower():
            return "timeout"
        if any(k in reason for k in ("error", "Error", "Exception")):
            return "api_error"
        if "truncat" in reason.lower():
            return "truncation"
        return "other"

    # Build escalation reason lookup: phase -> EscalationReasonBreakdown
    escalation_reason_map: Dict[str, EscalationReasonBreakdown] = {}
    for row in escalation_reason_rows:
        phase, reason, count = row[0], row[1], row[2]
        if not phase:
            continue
        if phase not in escalation_reason_map:
            escalation_reason_map[phase] = EscalationReasonBreakdown()
        bucket = _classify_reason(reason)
        breakdown = escalation_reason_map[phase]
        if bucket == "timeout":
            breakdown.timeout += count
        elif bucket == "api_error":
            breakdown.api_error += count
        elif bucket == "truncation":
            breakdown.truncation += count
        else:
            breakdown.other += count

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

        # Attach escalation reasons breakdown if any exist for this phase
        if phase_name in escalation_reason_map:
            ps.escalation_reasons = escalation_reason_map[phase_name]

        escalation_summary["by_phase"][phase_name] = {
            "base_tier": base_tier_completions,
            "escalated": escalated_completions,
            "rate": ps.escalation_rate,
            "escalation_reasons": escalation_reason_map[phase_name].model_dump()
            if phase_name in escalation_reason_map
            else None,
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


# ============================================================================
# Model Drift Timeline (from session_stats table)
# ============================================================================


class ModelTimelineEntry(BaseModel):
    """Usage data for a single model on a single day."""

    model: str = Field(..., description="Model identifier")
    count: int = Field(..., description="Number of completions using this model")
    cost: float = Field(..., description="Total cost in USD for this model on this day")


class ModelTimelineDay(BaseModel):
    """Aggregated model usage for a single calendar day."""

    date: str = Field(..., description="Date in YYYY-MM-DD format")
    models: List[ModelTimelineEntry] = Field(
        default_factory=list, description="Per-model breakdown for this day"
    )
    primary_model: Optional[str] = Field(
        None, description="Model with the highest completion count on this day"
    )
    primary_changed: bool = Field(
        False,
        description="True if the primary model differs from the previous day's primary",
    )


class ModelTimelineResponse(BaseModel):
    """Response containing daily model usage timeline."""

    available: bool = Field(..., description="Whether timeline data is available")
    days: List[ModelTimelineDay] = Field(
        default_factory=list, description="Daily model usage, ordered by date ascending"
    )
    period_days: int = Field(..., description="Number of days requested")
    all_models: List[str] = Field(
        default_factory=list, description="All unique model names seen in this period"
    )


@router.get("/model-timeline", response_model=ModelTimelineResponse)
async def get_model_timeline(
    days: int = Query(default=30, ge=1, le=90, description="Number of days to look back"),
):
    """
    Get a daily timeline of model usage to track model drift over time.

    Returns per-day counts and costs for each model used, with a flag
    indicating when the primary (most-used) model changed day-over-day.

    Data comes from local SQLite session_stats, specifically phase_completed events.
    """
    period_start = datetime.now(timezone.utc) - timedelta(days=days)

    async with get_session() as session:
        timeline_query = text("""
            SELECT
                date(timestamp) as day,
                json_extract(data, '$.model') as model,
                COUNT(*) as count,
                COALESCE(SUM(json_extract(data, '$.cost')), 0) as total_cost
            FROM session_stats
            WHERE event_type IN ('phase_completed', 'cost_update')
              AND json_extract(data, '$.model') IS NOT NULL
              AND timestamp >= :period_start
            GROUP BY day, model
            ORDER BY day ASC, count DESC
        """)

        result = await session.execute(timeline_query, {"period_start": period_start.isoformat()})
        rows = result.fetchall()

    # Build day -> list of entries
    day_map: Dict[str, List[ModelTimelineEntry]] = {}
    for row in rows:
        day, model, count, cost = row
        if not day or not model:
            continue
        if day not in day_map:
            day_map[day] = []
        day_map[day].append(
            ModelTimelineEntry(
                model=model,
                count=int(count),
                cost=round(float(cost or 0), 6),
            )
        )

    # Collect all unique model names across the period
    all_models_set: set = set()
    for entries in day_map.values():
        for entry in entries:
            all_models_set.add(entry.model)

    # Build timeline with primary_changed calculated day-over-day
    prev_primary: Optional[str] = None
    timeline: List[ModelTimelineDay] = []

    for day in sorted(day_map.keys()):
        entries = day_map[day]
        # Primary model = highest count; already ordered DESC by count from SQL
        primary = entries[0].model if entries else None
        changed = prev_primary is not None and primary != prev_primary

        timeline.append(
            ModelTimelineDay(
                date=day,
                models=entries,
                primary_model=primary,
                primary_changed=changed,
            )
        )

        prev_primary = primary

    all_models_sorted = sorted(all_models_set)

    return ModelTimelineResponse(
        available=True,
        days=timeline,
        period_days=days,
        all_models=all_models_sorted,
    )


# ============================================================================
# Cost Efficiency Analysis (from session_stats table)
# ============================================================================


class PhaseCostEfficiency(BaseModel):
    """Cost efficiency analysis for a single phase."""

    phase: str = Field(..., description="Phase name")
    base_tier: int = Field(..., description="Configured base tier for this phase")
    base_tier_label: str = Field("", description="Human-readable base tier name")
    total_runs: int = Field(0, description="Total completed runs")
    runs_at_base: int = Field(0, description="Runs that completed at configured base tier")
    runs_escalated: int = Field(0, description="Runs that required escalation")
    avg_cost_at_base: float = Field(0.0, description="Average cost when completing at base tier")
    avg_cost_when_escalated: float = Field(0.0, description="Average total cost including failed attempts + escalation")
    escalation_waste: float = Field(0.0, description="Total cost of failed attempts that were discarded")
    total_cost: float = Field(0.0, description="Total cost across all runs")
    recommendation: Optional[str] = Field(None, description="Actionable recommendation if applicable")
    suggested_tier: Optional[int] = Field(None, description="Suggested base tier if upgrade recommended")


class CostEfficiencyResponse(BaseModel):
    """Response containing cost efficiency analysis per phase."""

    phases: List[PhaseCostEfficiency] = Field(default_factory=list)
    period_days: int
    total_escalation_waste: float = Field(0.0, description="Total wasted cost from failed attempts across all phases")


@router.get("/cost-efficiency", response_model=CostEfficiencyResponse)
async def get_cost_efficiency(
    days: int = Query(default=30, ge=1, le=90, description="Number of days to look back"),
):
    """
    Analyze cost efficiency of model tier assignments per phase.

    Computes escalation waste (cost of failed attempts before tier escalation)
    and generates recommendations for phases where upgrading the base tier
    would reduce total cost.
    """
    period_start = datetime.now(timezone.utc) - timedelta(days=days)

    # Load current config for base tier info
    from api.routers.config import _load_config

    config = _load_config()
    routing = config.get("routing", {})
    phase_base_tiers = routing.get("phase_base_tiers", {})
    tier_labels = routing.get("tier_labels", ["cheapskate", "default", "big-brain"])

    async with get_session() as session:
        # Get completed runs with tier and cost info
        completions_query = text("""
            SELECT
                json_extract(data, '$.phase') as phase,
                json_extract(data, '$.extra.tier') as tier,
                COALESCE(json_extract(data, '$.cost'), 0) as cost
            FROM session_stats
            WHERE event_type = 'phase_completed'
              AND timestamp >= :period_start
        """)

        # Get failed attempts with cost info (these represent wasted spend)
        failures_query = text("""
            SELECT
                json_extract(data, '$.phase') as phase,
                json_extract(data, '$.extra.tier') as tier,
                COALESCE(json_extract(data, '$.cost'), 0) as cost
            FROM session_stats
            WHERE event_type = 'phase_failed'
              AND timestamp >= :period_start
        """)

        completions_result = await session.execute(completions_query, {"period_start": period_start.isoformat()})
        completions = completions_result.fetchall()

        failures_result = await session.execute(failures_query, {"period_start": period_start.isoformat()})
        failures = failures_result.fetchall()

    # Aggregate per phase
    phase_stats: Dict[str, Dict[str, Any]] = {}

    for phase, tier_raw, cost in completions:
        if not phase:
            continue
        tier = int(tier_raw) if tier_raw is not None else 0
        cost = float(cost or 0)

        if phase not in phase_stats:
            base = phase_base_tiers.get(phase, 0)
            phase_stats[phase] = {
                "base_tier": base,
                "base_costs": [],
                "escalated_costs": [],
                "total_cost": 0.0,
            }

        ps = phase_stats[phase]
        ps["total_cost"] += cost

        if tier <= ps["base_tier"]:
            ps["base_costs"].append(cost)
        else:
            ps["escalated_costs"].append(cost)

    # Sum up failure costs per phase (escalation waste)
    failure_costs: Dict[str, float] = {}
    for phase, tier_raw, cost in failures:
        if not phase:
            continue
        cost = float(cost or 0)
        failure_costs[phase] = failure_costs.get(phase, 0) + cost

    # Build response
    results: List[PhaseCostEfficiency] = []
    total_waste = 0.0

    for phase, ps in sorted(phase_stats.items()):
        base_tier = ps["base_tier"]
        base_costs = ps["base_costs"]
        escalated_costs = ps["escalated_costs"]
        waste = failure_costs.get(phase, 0)
        total_waste += waste

        total_runs = len(base_costs) + len(escalated_costs)
        avg_base = (sum(base_costs) / len(base_costs)) if base_costs else 0
        avg_escalated = (
            (sum(escalated_costs) + waste) / len(escalated_costs)
            if escalated_costs
            else 0
        )

        label = tier_labels[base_tier] if base_tier < len(tier_labels) else f"tier-{base_tier}"

        # Generate recommendation
        recommendation = None
        suggested_tier = None
        escalation_rate = (len(escalated_costs) / total_runs * 100) if total_runs > 0 else 0

        if total_runs >= 3 and escalation_rate > 30 and base_tier < len(tier_labels) - 1:
            next_tier = base_tier + 1
            next_label = tier_labels[next_tier] if next_tier < len(tier_labels) else f"tier-{next_tier}"
            recommendation = (
                f"Escalated {escalation_rate:.0f}% of runs. "
                f"Wasted {format_cost(waste)} on failed attempts over {days} days. "
                f"Consider upgrading base tier to {next_label}."
            )
            suggested_tier = next_tier

        results.append(
            PhaseCostEfficiency(
                phase=phase,
                base_tier=base_tier,
                base_tier_label=label,
                total_runs=total_runs,
                runs_at_base=len(base_costs),
                runs_escalated=len(escalated_costs),
                avg_cost_at_base=round(avg_base, 6),
                avg_cost_when_escalated=round(avg_escalated, 6),
                escalation_waste=round(waste, 6),
                total_cost=round(ps["total_cost"], 4),
                recommendation=recommendation,
                suggested_tier=suggested_tier,
            )
        )

    return CostEfficiencyResponse(
        phases=results,
        period_days=days,
        total_escalation_waste=round(total_waste, 4),
    )


def format_cost(cost: float) -> str:
    """Format a cost value for display in recommendations."""
    if cost < 0.01:
        return f"${cost:.4f}"
    if cost < 1:
        return f"${cost:.3f}"
    return f"${cost:.2f}"
