"""Jobs router for Cardigan API.

Provides endpoints for job detail retrieval, updates, and control operations.
"""

import logging
import os
import re
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from api.models.events import EventCreate, EventData, EventType, SessionEvent
from api.models.job import Job, JobStatus, JobUpdate, PhaseStatus
from api.services.airtable import AirtableClient
from api.services.database import (
    get_events_for_job,
    get_job,
    log_event,
    update_job,
)

logger = logging.getLogger(__name__)


class SSTMetadata(BaseModel):
    """SST (Single Source of Truth) metadata from Airtable."""

    media_id: Optional[str] = None
    release_title: Optional[str] = None
    short_description: Optional[str] = None
    media_manager_url: Optional[str] = None
    youtube_url: Optional[str] = None
    airtable_url: Optional[str] = None


router = APIRouter()


# Valid state transitions for job control operations
PAUSEABLE_STATES = {JobStatus.pending, JobStatus.in_progress}
RESUMABLE_STATES = {JobStatus.paused}
RETRYABLE_STATES = {JobStatus.failed, JobStatus.paused}
CANCELLABLE_STATES = {
    JobStatus.pending,
    JobStatus.in_progress,
    JobStatus.paused,
}


@router.get("/{job_id}", response_model=Job)
async def get_job_detail(job_id: int):
    """Retrieve full details for a specific job.

    Args:
        job_id: Job ID to retrieve

    Returns:
        Complete job record with all fields

    Raises:
        HTTPException: 404 if job not found
    """
    job = await get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return job


@router.patch("/{job_id}", response_model=Job)
async def update_job_fields(job_id: int, job_update: JobUpdate):
    """Update job fields with partial data.

    Accepts any subset of updateable fields and applies them to the job.

    Args:
        job_id: Job ID to update
        job_update: Partial update schema with optional fields

    Returns:
        Updated job record

    Raises:
        HTTPException: 404 if job not found
    """
    updated_job = await update_job(job_id, job_update)

    if updated_job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return updated_job


@router.post("/{job_id}/pause", response_model=Job)
async def pause_job(job_id: int):
    """Pause a running or pending job.

    Sets job status to 'paused'. Only valid for pending or in_progress jobs.

    Args:
        job_id: Job ID to pause

    Returns:
        Updated job record

    Raises:
        HTTPException: 404 if job not found, 400 if invalid state transition
    """
    job = await get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if job.status not in PAUSEABLE_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot pause job in status '{job.status}'. "
            f"Only {', '.join(s.value for s in PAUSEABLE_STATES)} jobs can be paused.",
        )

    job_update = JobUpdate(status=JobStatus.paused)
    updated_job = await update_job(job_id, job_update)

    return updated_job


@router.post("/{job_id}/resume", response_model=Job)
async def resume_job(job_id: int):
    """Resume a paused job.

    Sets job status back to 'pending' so it can be picked up by the worker.
    Only valid for paused jobs.

    Args:
        job_id: Job ID to resume

    Returns:
        Updated job record

    Raises:
        HTTPException: 404 if job not found, 400 if invalid state transition
    """
    job = await get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if job.status not in RESUMABLE_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resume job in status '{job.status}'. "
            f"Only {', '.join(s.value for s in RESUMABLE_STATES)} jobs can be resumed.",
        )

    job_update = JobUpdate(status=JobStatus.pending)
    updated_job = await update_job(job_id, job_update)

    return updated_job


@router.post("/{job_id}/retry", response_model=Job)
async def retry_job(job_id: int):
    """Retry a failed or paused job with automatic tier escalation.

    Resets job status to 'pending', clears error message, and escalates
    to the next model tier. For truncation-paused jobs, resets the formatter
    and downstream phases while preserving analyst output.

    Args:
        job_id: Job ID to retry

    Returns:
        Updated job record

    Raises:
        HTTPException: 404 if job not found, 400 if invalid state transition
    """
    job = await get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if job.status not in RETRYABLE_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot retry job in status '{job.status}'. "
            f"Only {', '.join(s.value for s in RETRYABLE_STATES)} jobs can be retried.",
        )

    # Determine escalation tier from previous max tier used
    max_previous_tier = 0
    phases = job.phases or []
    for phase in phases:
        if phase.tier is not None and phase.tier > max_previous_tier:
            max_previous_tier = phase.tier
    escalated_tier = min(max_previous_tier + 1, 2)  # Cap at big-brain (tier 2)

    tier_labels = {0: "cheapskate", 1: "default", 2: "big-brain"}
    logger.info(
        "Retry with escalation",
        extra={
            "job_id": job_id,
            "previous_max_tier": max_previous_tier,
            "escalated_tier": escalated_tier,
            "tier_label": tier_labels.get(escalated_tier),
        },
    )

    # Determine which phases to reset based on failure type
    is_truncation = job.error_message and "TRUNCATION" in job.error_message
    if is_truncation:
        phases_to_reset = {"formatter", "seo", "manager", "timestamp"}
    else:
        # Find the first non-completed phase and reset from there forward
        # This preserves completed phases (e.g., analyst) when a later phase fails
        phase_names = [p.name for p in phases]
        first_incomplete_idx = None
        for i, phase in enumerate(phases):
            if phase.status != PhaseStatus.completed:
                first_incomplete_idx = i
                break
        if first_incomplete_idx is not None:
            phases_to_reset = set(phase_names[first_incomplete_idx:])
        else:
            phases_to_reset = None  # All completed — reset everything as fallback

    # Reset phase statuses and set forced tier, archiving previous run data
    updated_phases = []
    for phase in phases:
        phase_dict = phase.model_dump()
        if phases_to_reset is None or phase.name in phases_to_reset:
            # Archive current run before resetting (if it had results)
            if phase_dict.get("model") or phase_dict.get("tier") is not None:
                prev_run = {
                    "tier": phase_dict.get("tier"),
                    "tier_label": phase_dict.get("tier_label"),
                    "model": phase_dict.get("model"),
                    "cost": phase_dict.get("cost", 0),
                    "tokens": phase_dict.get("tokens", 0),
                    "completed_at": phase_dict.get("completed_at"),
                }
                previous_runs = phase_dict.get("previous_runs") or []
                previous_runs.append(prev_run)
                phase_dict["previous_runs"] = previous_runs
                phase_dict["retry_count"] = (phase_dict.get("retry_count") or 0) + 1

            # Reset this phase to pending with forced escalation tier
            phase_dict["status"] = "pending"
            phase_dict["completed_at"] = None
            phase_dict["error_message"] = None
            phase_dict["cost"] = 0
            phase_dict["tokens"] = 0
            phase_dict["model"] = None
            phase_dict["tier"] = None
            phase_dict["tier_label"] = None
            phase_dict["tier_reason"] = None
            phase_dict["attempts"] = None
            phase_dict["metadata"] = {"forced_tier": escalated_tier}
        updated_phases.append(phase_dict)

    from api.models.job import JobPhase

    job_update = JobUpdate(
        status=JobStatus.pending,
        error_message="",
        current_phase=None,
        phases=[JobPhase(**p) for p in updated_phases],
    )
    updated_job = await update_job(job_id, job_update)

    # Log user retry event
    await log_event(
        EventCreate(
            job_id=job_id,
            event_type=EventType.user_action,
            data=EventData(
                extra={
                    "action": "job_retry",
                    "escalated_tier": escalated_tier,
                    "tier_label": tier_labels.get(escalated_tier),
                    "is_truncation_retry": is_truncation,
                    "phases_reset": list(phases_to_reset) if phases_to_reset else "all",
                }
            ),
        )
    )

    return updated_job


@router.post("/{job_id}/cancel", response_model=Job)
async def cancel_job(job_id: int):
    """Cancel a job.

    Sets job status to 'cancelled'. Only valid for pending, in_progress, or paused jobs.
    Cannot cancel completed or failed jobs.

    Args:
        job_id: Job ID to cancel

    Returns:
        Updated job record

    Raises:
        HTTPException: 404 if job not found, 400 if invalid state transition
    """
    job = await get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if job.status not in CANCELLABLE_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel job in status '{job.status}'. "
            f"Only {', '.join(s.value for s in CANCELLABLE_STATES)} jobs can be cancelled.",
        )

    job_update = JobUpdate(status=JobStatus.cancelled)
    updated_job = await update_job(job_id, job_update)

    return updated_job


@router.get("/{job_id}/events", response_model=List[SessionEvent])
async def get_job_events(job_id: int):
    """Retrieve all events for a specific job.

    Returns chronologically ordered list of events logged during job execution.
    Useful for debugging, monitoring, and audit trails.

    Args:
        job_id: Job ID to get events for

    Returns:
        List of SessionEvent records ordered by timestamp

    Raises:
        HTTPException: 404 if job not found
    """
    # Verify job exists
    job = await get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    events = await get_events_for_job(job_id)

    return events


@router.get("/{job_id}/outputs/{filename}")
async def get_job_output(job_id: int, filename: str, download: bool = Query(default=False)):
    """Retrieve an output file for a specific job.

    Returns the contents of a generated output file (markdown, json, etc.).
    File must exist in the job's output directory.

    Args:
        job_id: Job ID to get output for
        filename: Name of the output file (e.g., analyst_output.md)
        download: If True, set Content-Disposition header to trigger browser download

    Returns:
        File contents as plain text

    Raises:
        HTTPException: 404 if job or file not found, 400 if invalid filename
    """
    # Verify job exists
    job = await get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Security: only allow specific safe filenames
    allowed_files = {
        "analyst_output.md",
        "formatter_output.md",
        "seo_output.md",
        "manager_output.md",
        "timestamp_output.md",
        "copy_editor_output.md",
        "recovery_analysis.md",
        "manifest.json",
    }

    # Also allow versioned revision and keyword report files
    is_revision_file = bool(re.match(r"^copy_revision_v\d+\.md$", filename))
    is_keyword_report = bool(re.match(r"^keyword_report_v\d+\.md$", filename))

    if filename not in allowed_files and not is_revision_file and not is_keyword_report:
        raise HTTPException(
            status_code=400, detail=f"Invalid filename. Allowed files: {', '.join(sorted(allowed_files))}"
        )

    # Build path to output file
    if not job.project_path:
        raise HTTPException(status_code=404, detail="Job has no output directory configured")

    # Security: Resolve paths and validate within OUTPUT directory
    output_dir = Path(os.getenv("OUTPUT_DIR", "OUTPUT")).resolve()
    file_path = (Path(job.project_path) / filename).resolve()

    # Prevent path traversal attacks
    if not file_path.is_relative_to(output_dir):
        raise HTTPException(status_code=400, detail="Invalid project path - outside output directory")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Output file '{filename}' not found for job {job_id}")

    # Read and return file contents
    content = file_path.read_text(encoding="utf-8")

    # Determine content type
    media_type = "application/json" if filename.endswith(".json") else "text/markdown"
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return PlainTextResponse(content, media_type=media_type, headers=headers)


@router.get("/{job_id}/sst-metadata", response_model=SSTMetadata)
async def get_sst_metadata(job_id: int):
    """Retrieve SST (Single Source of Truth) metadata from Airtable for a job.

    Returns contextual metadata from PBS Wisconsin's Airtable SST table,
    including release title, descriptions, and external links.

    This is a READ-ONLY operation against Airtable.

    Args:
        job_id: Job ID to get SST metadata for

    Returns:
        SSTMetadata with available fields from Airtable

    Raises:
        HTTPException: 404 if job not found or no Airtable record linked
    """
    job = await get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if not job.airtable_record_id:
        raise HTTPException(status_code=404, detail=f"Job {job_id} has no linked Airtable record")

    try:
        client = AirtableClient()
        record = await client.get_sst_record(job.airtable_record_id)

        if record is None:
            raise HTTPException(status_code=404, detail=f"Airtable record {job.airtable_record_id} not found")

        fields = record.get("fields", {})

        return SSTMetadata(
            media_id=fields.get("Media ID"),
            release_title=fields.get("Release Title"),
            short_description=fields.get("Short Description"),
            media_manager_url=fields.get("Final Website Link"),  # PBS Wisconsin website URL
            youtube_url=fields.get("YouTube Link"),
            airtable_url=client.get_sst_url(job.airtable_record_id),
        )

    except ValueError as e:
        # Airtable API key not configured
        logger.warning(f"Airtable not configured: {e}")
        raise HTTPException(status_code=503, detail="Airtable integration not configured")
    except Exception as e:
        logger.error(f"Failed to fetch SST metadata: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch metadata from Airtable")


class PhaseRetryRequest(BaseModel):
    """Request body for phase retry with optional tier override and editorial feedback."""

    tier: Optional[int] = Field(
        None,
        ge=0,
        le=2,
        description="Force specific tier: 0=cheapskate, 1=default, 2=big-brain. "
        "If not specified, auto-escalates from the tier previously used.",
    )
    feedback: Optional[str] = Field(
        None,
        description="Editorial feedback to guide the retry (e.g., 'add a chapter for topic X', "
        "'merge the first two chapters'). Injected into the agent prompt.",
    )


class PhaseRetryResponse(BaseModel):
    """Response for phase retry request."""

    success: bool
    phase: Optional[str] = None
    message: str
    cost: Optional[float] = None
    tokens: Optional[int] = None


# Map output keys to phase names
OUTPUT_TO_PHASE = {
    "analysis": "analyst",
    "formatted_transcript": "formatter",
    "seo_metadata": "seo",
    "qa_review": "manager",
    "timestamp_report": "timestamp",
}


@router.post("/{job_id}/phases/{phase_name}/retry", response_model=PhaseRetryResponse)
async def retry_phase(
    job_id: int,
    phase_name: str,
    background_tasks: BackgroundTasks,
    body: PhaseRetryRequest = None,
):
    """Retry a single phase for a job with optional tier override and editorial feedback.

    Re-runs one output (e.g., timestamp) without re-running the entire
    pipeline. If no tier is specified in the request body, automatically
    escalates to the next tier above what was previously used for this phase.

    Accepts an optional JSON body with:
      - tier: 0=cheapskate, 1=default, 2=big-brain (omit to auto-escalate)
      - feedback: editorial guidance injected into the agent prompt

    Args:
        job_id: Job ID to retry a phase for
        phase_name: Phase name (analyst, formatter, seo, manager, timestamp,
                    copy_editor) OR output key (analysis, seo_metadata,
                    timestamp_report, etc.)
        body: Optional JSON body with tier and/or feedback fields

    Returns:
        PhaseRetryResponse with status

    Raises:
        HTTPException: 404 if job not found, 400 if invalid phase
    """
    if body is None:
        body = PhaseRetryRequest()
    tier = body.tier
    feedback = body.feedback
    # Map output key to phase name if needed
    if phase_name in OUTPUT_TO_PHASE:
        phase_name = OUTPUT_TO_PHASE[phase_name]

    # Validate phase name
    valid_phases = {"analyst", "formatter", "seo", "manager", "timestamp"}
    if phase_name not in valid_phases:
        raise HTTPException(
            status_code=400, detail=f"Invalid phase: {phase_name}. Valid phases: {', '.join(sorted(valid_phases))}"
        )

    # Verify job exists
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Auto-escalate if no explicit tier provided
    effective_tier = tier
    if effective_tier is None:
        previous_tier = 0
        for phase in job.phases or []:
            if phase.name == phase_name and phase.tier is not None:
                previous_tier = phase.tier
                break
        effective_tier = min(previous_tier + 1, 2)
        logger.info(
            "Auto-escalating phase retry",
            extra={
                "job_id": job_id,
                "phase": phase_name,
                "previous_tier": previous_tier,
                "escalated_tier": effective_tier,
            },
        )

    # Capture original model for event logging
    original_model = None
    for phase in job.phases or []:
        if phase.name == phase_name and phase.model:
            original_model = phase.model
            break

    # Log user retry event
    await log_event(
        EventCreate(
            job_id=job_id,
            event_type=EventType.user_action,
            data=EventData(
                phase=phase_name,
                model=original_model,
                extra={
                    "action": "phase_retry",
                    "tier": effective_tier,
                    "auto_escalated": tier is None,
                    "original_model": original_model,
                    "has_feedback": feedback is not None,
                },
            ),
        )
    )

    # Set current_phase so UI shows retry in progress
    await update_job(job_id, JobUpdate(current_phase=phase_name))

    # Run the phase retry in the background
    async def run_retry():
        from api.services.worker import JobWorker

        worker = JobWorker()
        result = await worker.retry_single_phase(job_id, phase_name, force_tier=effective_tier, feedback=feedback)
        if not result.get("success"):
            logger.error(
                "Phase retry failed",
                extra={"job_id": job_id, "phase": phase_name, "tier": effective_tier, "error": result.get("error")},
            )
        # Clear current_phase after retry completes
        await update_job(job_id, JobUpdate(current_phase=None))

    background_tasks.add_task(run_retry)

    tier_labels = {0: "cheapskate", 1: "default", 2: "big-brain"}
    escalation_note = " (auto-escalated)" if tier is None else ""
    tier_msg = f" at tier {effective_tier} ({tier_labels.get(effective_tier, '?')}){escalation_note}"
    return PhaseRetryResponse(
        success=True,
        phase=phase_name,
        message=f"Phase '{phase_name}' retry started for job {job_id}{tier_msg}. Refresh to see results.",
    )
