"""QA-failure escalation + shared pause-and-suggest terminal handling (Spec B)."""

from __future__ import annotations

from datetime import datetime, timezone

from api.models.job import JobStatus, JobUpdate
from api.services import model_roster
from api.services.database import update_job

FAMILY_ORDER = ["haiku", "sonnet", "opus"]


def parse_model_family(model_slug: str | None) -> str | None:
    """Return 'haiku' | 'sonnet' | 'opus' parsed from a model slug, else None.

    Robust to OpenRouter's mixed word order (claude-4.6-sonnet vs claude-sonnet-4.6).
    """
    if not model_slug:
        return None
    s = model_slug.lower()
    for family in FAMILY_ORDER:
        if family in s:
            return family
    return None


def bump_family(family: str | None) -> str | None:
    """Return the next-stronger family, or None if already opus / unknown."""
    if family not in FAMILY_ORDER:
        return None
    idx = FAMILY_ORDER.index(family)
    return FAMILY_ORDER[idx + 1] if idx + 1 < len(FAMILY_ORDER) else None


async def pause_and_suggest(job_id: int, *, trigger: str, message: str, mark_escalated: bool = False) -> None:
    """Terminal handler shared by all failure triggers (QA-fail, credit, truncation).

    Leaves the job visibly NOT completed: status=paused with a structured,
    actionable error_message. Optionally stamps the escalate-once marker.
    """
    update = JobUpdate(
        status=JobStatus.paused,
        error_message=f"[{trigger}] {message}",
    )
    if mark_escalated:
        update.auto_escalated_at = datetime.now(timezone.utc)
    await update_job(job_id, update)


def select_escalation_phases(validation_result: dict, phase_order: list) -> list:
    """Earliest validator-flagged phase + every downstream phase in run order."""
    results = (validation_result or {}).get("phase_results", {})
    flagged = {name for name, r in results.items() if r.get("status") == "fail" or r.get("flags")}
    for i, name in enumerate(phase_order):
        if name in flagged:
            return phase_order[i:]
    return []


async def resolve_escalated_model(current_model: str | None, exclude_variants: list) -> str | None:
    """Bump current_model's family one step and resolve the newest catalog model
    in that family. None if already opus / unknown family / catalog unavailable.
    """
    target_family = bump_family(parse_model_family(current_model))
    if target_family is None:
        return None
    return await model_roster.newest_in_family(target_family, exclude_variants)
