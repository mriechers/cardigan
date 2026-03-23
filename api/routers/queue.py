"""Queue management router for Cardigan API.

Provides CRUD operations for the job queue.
"""

import logging
import os
from typing import List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from api.middleware.rate_limit import RATE_EXPENSIVE, limiter
from api.models.job import Job, JobCreate, JobStatus, JobUpdate
from api.services import database
from api.services.airtable import AirtableClient
from api.services.utils import extract_media_id, get_srt_duration, parse_srt

logger = logging.getLogger(__name__)

# Base path for transcript files
TRANSCRIPTS_DIR = os.getenv("TRANSCRIPTS_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "transcripts"))


def calculate_transcript_metrics_from_file(transcript_file: str) -> Tuple[Optional[float], Optional[int]]:
    """Calculate duration and word count from a transcript file.

    For SRT files: parses timecodes to get actual duration, counts words in captions.
    For TXT files: counts words and estimates duration at 150 wpm.

    Args:
        transcript_file: Relative path to transcript file (from transcripts/)

    Returns:
        Tuple of (duration_minutes, word_count), either may be None on error
    """
    file_path = os.path.join(TRANSCRIPTS_DIR, transcript_file)

    if not os.path.exists(file_path):
        logger.warning(f"Transcript file not found for metrics: {file_path}")
        return None, None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        if not content.strip():
            return None, None

        # Check if it's an SRT file
        if transcript_file.lower().endswith(".srt"):
            captions = parse_srt(content)
            if captions:
                # Get duration from last caption's end time
                duration_ms = get_srt_duration(captions)
                duration_minutes = round(duration_ms / 60000, 2)

                # Count words in all captions
                all_text = " ".join(c.text for c in captions)
                word_count = len(all_text.split())

                return duration_minutes, word_count

        # For TXT files or SRT parsing failure, estimate from word count
        word_count = len(content.split())
        # Estimate duration at 150 words per minute (typical speaking rate)
        duration_minutes = round(word_count / 150, 2)

        return duration_minutes, word_count

    except Exception as e:
        logger.warning(f"Failed to calculate transcript metrics: {e}")
        return None, None


router = APIRouter()


class DuplicateJobResponse(BaseModel):
    """Response when a duplicate job is detected."""

    message: str
    existing_job: Job
    action_required: str


class PaginatedJobsResponse(BaseModel):
    """Paginated response with jobs and metadata."""

    jobs: List[Job]
    total: int
    page: int
    page_size: int
    total_pages: int


@router.get("/", response_model=PaginatedJobsResponse)
async def list_queue(
    status: Optional[JobStatus] = Query(default=None, description="Filter by job status (null = all statuses)"),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=50, ge=1, le=100, description="Jobs per page"),
    search: Optional[str] = Query(default=None, description="Search by filename or project path"),
    sort: str = Query(default="newest", description="Sort order: 'newest' or 'oldest'"),
) -> PaginatedJobsResponse:
    """List jobs in the queue with filtering, search, and pagination.

    Returns jobs in reverse chronological order by default (newest first).
    Supports search by filename and pagination.

    Args:
        status: Filter by job status (null = all statuses)
        page: Page number, 1-indexed (default: 1)
        page_size: Jobs per page (1-100, default: 50)
        search: Filter by transcript filename or project path
        sort: Sort order - 'newest' (default) or 'oldest'

    Returns:
        Paginated response with jobs and metadata
    """
    offset = (page - 1) * page_size

    # Get jobs and total count
    jobs = await database.list_jobs(
        status=status,
        limit=page_size,
        offset=offset,
        search=search,
        sort_order=sort,
    )
    total = await database.count_jobs(status=status, search=search)
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    return PaginatedJobsResponse(
        jobs=jobs,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post(
    "/",
    response_model=Job,
    status_code=201,
    responses={409: {"model": DuplicateJobResponse, "description": "Transcript already processed or in queue"}},
)
@limiter.limit(RATE_EXPENSIVE)
async def add_to_queue(
    request: Request,
    job_create: JobCreate,
    force: bool = Query(default=False, description="Force re-queue even if transcript was already processed"),
) -> Job:
    """Add a new job to the queue.

    Creates a job record with status=pending and default values.
    Returns the complete job record with generated ID.

    Duplicate Detection:
    - If the transcript file has already been processed (completed) or is
      currently in queue (pending/in_progress), returns 409 with details
      about the existing job.
    - Use `force=true` to bypass duplicate detection and re-queue anyway.

    Args:
        job_create: Job creation schema with required fields
        force: Bypass duplicate detection (default: false)

    Returns:
        Complete Job record including generated ID and timestamps

    Raises:
        HTTPException: 409 if transcript already processed (unless force=true)
    """
    # Extract media ID for duplicate detection and Airtable lookup
    media_id = extract_media_id(job_create.transcript_file)

    # Check for existing jobs with this transcript or media ID (unless force=true)
    if not force:
        # First check by exact transcript filename
        existing_jobs = await database.find_jobs_by_transcript(job_create.transcript_file)

        # Also check by media ID (catches .srt vs .txt variants)
        if not existing_jobs and media_id:
            existing_jobs = await database.find_jobs_by_media_id(media_id)

        if existing_jobs:
            # Find most relevant existing job
            existing = existing_jobs[0]  # Most recent non-cancelled job

            # Determine status message
            if existing.status == JobStatus.completed:
                message = f"Transcript already processed successfully as job {existing.id}"
                action = "Use force=true to re-queue, or view existing job"
            elif existing.status in [JobStatus.pending, JobStatus.in_progress]:
                message = f"Transcript already in queue as job {existing.id} (status: {existing.status.value})"
                action = "Wait for existing job to complete, or cancel it first"
            elif existing.status == JobStatus.failed:
                message = f"Previous processing failed as job {existing.id}"
                action = "Use POST /api/jobs/{id}/retry to retry, or force=true to create new job"
            elif existing.status == JobStatus.paused:
                message = f"Transcript paused as job {existing.id}"
                action = "Use POST /api/jobs/{id}/resume to resume, or force=true to create new job"
            else:
                message = f"Transcript exists as job {existing.id} (status: {existing.status.value})"
                action = "Use force=true to create new job anyway"

            raise HTTPException(
                status_code=409,
                detail={
                    "message": message,
                    "existing_job_id": existing.id,
                    "existing_status": existing.status.value,
                    "action_required": action,
                    "hint": "Add ?force=true to bypass this check",
                },
            )

    # Create the job first
    job = await database.create_job(job_create)

    # Calculate transcript metrics for cost analysis
    duration_minutes, word_count = calculate_transcript_metrics_from_file(job_create.transcript_file)
    if duration_minutes or word_count:
        logger.info(f"Job {job.id}: Transcript metrics - {duration_minutes}min, {word_count} words")

    # Attempt to auto-link SST record from Airtable and store all metadata
    try:
        if not media_id:
            raise ValueError("No valid Media ID extracted from filename")
        # Try to lookup SST record if Airtable is configured
        airtable_client = AirtableClient()
        record = await airtable_client.search_sst_by_media_id(media_id)

        if record:
            # Found matching SST record - update job with link and metrics
            record_id = record["id"]
            airtable_url = airtable_client.get_sst_url(record_id)

            update = JobUpdate(
                airtable_record_id=record_id,
                airtable_url=airtable_url,
                media_id=media_id,
                duration_minutes=duration_minutes,
                word_count=word_count,
            )
            job = await database.update_job(job.id, update)
            logger.info(f"Job {job.id}: Linked to SST record {record_id} (Media ID: {media_id})")
        else:
            # No matching SST record found - store media_id and metrics only
            update = JobUpdate(
                media_id=media_id,
                duration_minutes=duration_minutes,
                word_count=word_count,
            )
            job = await database.update_job(job.id, update)
            logger.warning(f"Job {job.id}: No SST record found for Media ID: {media_id}")

    except ValueError as e:
        # Airtable API key not configured - still store media_id and metrics
        update = JobUpdate(
            media_id=media_id,
            duration_minutes=duration_minutes,
            word_count=word_count,
        )
        job = await database.update_job(job.id, update)
        logger.warning(f"Job {job.id}: Airtable lookup skipped - {e}")
    except Exception as e:
        # Any other error during Airtable lookup - still store metrics
        update = JobUpdate(
            media_id=media_id,
            duration_minutes=duration_minutes,
            word_count=word_count,
        )
        job = await database.update_job(job.id, update)
        logger.warning(f"Job {job.id}: Airtable lookup failed - {e}")

    return job


class BulkDeleteResponse(BaseModel):
    """Response for bulk delete operations."""

    deleted_count: int
    message: str


@router.delete("/bulk", response_model=BulkDeleteResponse)
async def bulk_delete_jobs(
    statuses: List[JobStatus] = Query(
        ..., description="List of statuses to delete (e.g., completed, failed, cancelled)"
    ),
) -> BulkDeleteResponse:
    """Bulk delete jobs by status.

    Permanently deletes all jobs matching the specified statuses.
    Useful for clearing out completed, failed, or cancelled jobs.

    Safety: Will not delete pending or in_progress jobs even if requested.

    Args:
        statuses: List of job statuses to delete

    Returns:
        Count of deleted jobs and confirmation message
    """
    # Safety: Never bulk delete pending or in_progress jobs
    safe_statuses = [s for s in statuses if s not in [JobStatus.pending, JobStatus.in_progress]]

    if not safe_statuses:
        return BulkDeleteResponse(
            deleted_count=0, message="No safe statuses to delete. Cannot bulk delete pending or in_progress jobs."
        )

    deleted_count = await database.bulk_delete_jobs_by_status(safe_statuses)

    status_names = ", ".join(s.value for s in safe_statuses)
    return BulkDeleteResponse(
        deleted_count=deleted_count, message=f"Deleted {deleted_count} jobs with status: {status_names}"
    )


@router.delete("/{job_id}", status_code=204)
async def remove_from_queue(job_id: int) -> None:
    """Remove a job from the queue.

    Permanently deletes the job record from the database.
    Returns 404 if job not found.

    Args:
        job_id: ID of job to delete

    Raises:
        HTTPException: 404 if job not found
    """
    deleted = await database.delete_job(job_id)

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return None


@router.get("/next", response_model=Job)
async def get_next_job() -> Job:
    """Get the next pending job to process.

    Returns the highest priority pending job. If multiple jobs have
    the same priority, returns the earliest queued job.

    Returns 404 if no pending jobs exist.

    Returns:
        Next Job to process

    Raises:
        HTTPException: 404 if no pending jobs in queue
    """
    job = await database.get_next_pending_job()

    if job is None:
        raise HTTPException(status_code=404, detail="No pending jobs in queue")

    return job


@router.get("/stats")
async def get_queue_stats() -> dict:
    """Get queue statistics.

    Returns counts of jobs by status and other queue metrics.

    Returns:
        Dictionary with queue statistics:
        - pending: Number of jobs waiting to be processed
        - in_progress: Number of jobs currently processing
        - completed: Number of successfully completed jobs
        - failed: Number of failed jobs
        - cancelled: Number of cancelled jobs
        - paused: Number of paused jobs
        - total: Total number of jobs in database
    """
    # Use count_jobs for efficiency (doesn't fetch full records)
    pending_count = await database.count_jobs(status=JobStatus.pending)
    in_progress_count = await database.count_jobs(status=JobStatus.in_progress)
    completed_count = await database.count_jobs(status=JobStatus.completed)
    failed_count = await database.count_jobs(status=JobStatus.failed)
    cancelled_count = await database.count_jobs(status=JobStatus.cancelled)
    paused_count = await database.count_jobs(status=JobStatus.paused)

    total = pending_count + in_progress_count + completed_count + failed_count + cancelled_count + paused_count

    return {
        "pending": pending_count,
        "in_progress": in_progress_count,
        "completed": completed_count,
        "failed": failed_count,
        "cancelled": cancelled_count,
        "paused": paused_count,
        "total": total,
    }
