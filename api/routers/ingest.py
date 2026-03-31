"""Ingest router for Cardigan API.

Provides endpoints for remote ingest server monitoring, transcript queueing,
and screengrab attachment.

Endpoints:
- GET /config - Get scanner configuration
- PUT /config - Update scanner configuration
- POST /scan - Trigger scan of remote server
- GET /status - Get scanner status and file counts
- GET /available - List files available for queueing (with SST enrichment)

Transcript endpoints:
- POST /transcripts/{file_id}/queue - Download and queue single transcript
- POST /transcripts/queue - Bulk queue multiple transcripts
- POST /transcripts/{file_id}/ignore - Mark transcript as ignored
- POST /transcripts/{file_id}/unignore - Restore ignored transcript

Screengrab endpoints:
- GET /screengrabs - List pending screengrabs
- POST /screengrabs/{file_id}/attach - Attach single screengrab to SST
- POST /screengrabs/attach-all - Attach all pending screengrabs
- POST /screengrabs/{file_id}/ignore - Mark screengrab as ignored
- POST /screengrabs/{file_id}/unignore - Restore ignored screengrab
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text

from api.middleware.rate_limit import RATE_EXPENSIVE, limiter
from api.models.ingest import IngestConfigResponse, IngestConfigUpdate
from api.services.database import get_session
from api.services.ingest_config import (
    get_ingest_config,
    get_next_scan_time,
    record_scan_result,
    update_ingest_config,
)
from api.services.ingest_scanner import IngestScanner
from api.services.ingest_scheduler import configure_scheduler
from api.services.screengrab_attacher import (
    get_screengrab_attacher,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# Response models


class SSTRecordInfo(BaseModel):
    """Minimal SST record info for enriching available files."""

    id: str
    title: Optional[str] = None
    project: Optional[str] = None


class AvailableFile(BaseModel):
    """A file available for queueing."""

    id: int
    filename: str
    media_id: Optional[str]
    file_type: str
    remote_url: str
    first_seen_at: datetime
    remote_modified_at: Optional[datetime] = None  # Server modification time
    status: str
    sst_record: Optional[SSTRecordInfo] = None


class AvailableFilesResponse(BaseModel):
    """Response listing available files."""

    files: List[AvailableFile]
    total_new: int
    last_scan_at: Optional[datetime] = None


class ScanResponse(BaseModel):
    """Response from scan endpoint."""

    success: bool
    qc_passed_checked: int
    new_files_found: int
    total_files_on_server: int
    scan_duration_ms: int
    new_transcripts: int
    new_screengrabs: int
    error_message: Optional[str] = None


class ScreengrabFile(BaseModel):
    """A screengrab file discovered on remote server."""

    id: int
    filename: str
    remote_url: str
    media_id: Optional[str]
    status: str
    first_seen_at: datetime
    sst_record_id: Optional[str] = None
    attached_at: Optional[datetime] = None


class ScreengrabListResponse(BaseModel):
    """Response listing screengrabs."""

    screengrabs: List[ScreengrabFile]
    total_new: int
    total_attached: int
    total_no_match: int
    # Airtable attachment info (only populated for /screengrabs/for-media-id endpoint)
    sst_existing_attachments: Optional[int] = None
    sst_record_id: Optional[str] = None


class AttachResponse(BaseModel):
    """Response from single attach operation."""

    success: bool
    media_id: str
    filename: str
    sst_record_id: Optional[str] = None
    attachments_before: int = 0
    attachments_after: int = 0
    error_message: Optional[str] = None
    skipped_duplicate: bool = False


class BatchAttachResponse(BaseModel):
    """Response from batch attach operation."""

    total_processed: int
    attached: int
    skipped_no_match: int
    skipped_duplicate: int
    errors: List[str]


class IngestStatusResponse(BaseModel):
    """Scanner status and configuration."""

    enabled: bool
    server_url: str
    files_by_status: dict
    files_by_type: dict


# Endpoints


@router.get("/available", response_model=AvailableFilesResponse)
async def list_available_files(
    status: Optional[str] = Query(default="new", description="Filter by status (new, queued, ignored)"),
    file_type: Optional[str] = Query(
        default="transcript", description="Filter by file type (transcript or screengrab)"
    ),
    limit: int = Query(default=50, le=200),
    search: Optional[str] = Query(default=None, description="Search by filename or Media ID (case-insensitive)"),
    days: Optional[int] = Query(
        default=30, ge=1, le=365, description="Filter by first_seen_at within N days (default: 30)"
    ),
    exclude_with_jobs: bool = Query(default=True, description="Hide files that already have linked jobs"),
) -> AvailableFilesResponse:
    """
    List files available for queueing.

    Returns transcript files discovered on the ingest server.
    SST validation is deferred to queue time for performance.

    Args:
        status: Filter by status (default: new)
        file_type: Filter by type (default: transcript)
        limit: Maximum results to return
        search: Search term for filename or Media ID
        days: Filter to files seen within N days (default: 30)
        exclude_with_jobs: Hide files already linked to jobs (default: true)

    Returns:
        List of available files
    """
    async with get_session() as session:
        # Query available_files table
        query = """
            SELECT id, remote_url, filename, media_id, file_type,
                   first_seen_at, remote_modified_at, status
            FROM available_files
            WHERE 1=1
        """
        params = {"limit": limit}

        if status:
            query += " AND status = :status"
            params["status"] = status

        if file_type:
            query += " AND file_type = :file_type"
            params["file_type"] = file_type

        # Search filter: match filename OR media_id
        if search:
            query += " AND (filename LIKE :search OR media_id LIKE :search)"
            params["search"] = f"%{search}%"

        # Date filter: only files seen within N days
        if days:
            query += " AND first_seen_at >= datetime('now', :days_offset)"
            params["days_offset"] = f"-{days} days"

        # Exclude files that already have jobs
        if exclude_with_jobs:
            query += " AND job_id IS NULL"

        # Sort by server modification time when available, otherwise first_seen
        query += " ORDER BY COALESCE(remote_modified_at, first_seen_at) DESC LIMIT :limit"

        result = await session.execute(text(query), params)
        rows = result.fetchall()

        # Build file list without SST enrichment for performance
        # SST validation happens when files are actually queued
        files = [
            AvailableFile(
                id=row.id,
                filename=row.filename,
                media_id=row.media_id,
                file_type=row.file_type,
                remote_url=row.remote_url,
                first_seen_at=row.first_seen_at,
                remote_modified_at=row.remote_modified_at,
                status=row.status,
                sst_record=None,  # Deferred to queue time
            )
            for row in rows
        ]

        # Get total count of new files
        count_query = text("""
            SELECT COUNT(*) as count
            FROM available_files
            WHERE status = 'new' AND file_type = :file_type
        """)
        result = await session.execute(count_query, {"file_type": file_type or "transcript"})
        total_new = result.fetchone().count

    # Get last scan timestamp from config
    config = await get_ingest_config()

    return AvailableFilesResponse(
        files=files,
        total_new=total_new,
        last_scan_at=config.last_scan_at,
    )


@router.post("/scan", response_model=ScanResponse)
@limiter.limit(RATE_EXPENSIVE)
async def trigger_scan(
    request: Request,
    base_url: Optional[str] = Query(
        default=None, description="Base URL of ingest server (uses config default if not provided)"
    ),
    directories: Optional[str] = Query(
        default=None, description="Comma-separated list of directories to scan (uses config default if not provided)"
    ),
) -> ScanResponse:
    """
    Trigger a scan of the remote ingest server.

    Discovers new SRT transcripts and JPG screengrabs, tracking them
    in the database for further action.

    Args:
        base_url: Base URL of the ingest server (optional, uses config if not provided)
        directories: Comma-separated directory paths (optional, uses config if not provided)

    Returns:
        Scan results including counts of new files discovered
    """
    try:
        # Get config for defaults
        config = await get_ingest_config()

        # Use provided values or fall back to config
        scan_base_url = base_url or config.server_url
        scan_dirs = directories.split(",") if directories else config.directories

        scanner = IngestScanner(
            base_url=scan_base_url,
            directories=scan_dirs,
            ignore_directories=config.ignore_directories,
        )
        result = await scanner.scan()

        # Record scan result in config
        await record_scan_result(success=result.success)

        return ScanResponse(
            success=result.success,
            qc_passed_checked=result.qc_passed_checked,
            new_files_found=result.new_files_found,
            total_files_on_server=result.total_files_on_server,
            scan_duration_ms=result.scan_duration_ms,
            new_transcripts=result.new_transcripts,
            new_screengrabs=result.new_screengrabs,
            error_message=result.error_message,
        )
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        # Record failed scan
        await record_scan_result(success=False)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status", response_model=IngestStatusResponse)
async def get_ingest_status() -> IngestStatusResponse:
    """
    Get current ingest scanner status and file counts.

    Returns counts of files by status and type.
    """
    # Get config for enabled status and server URL
    config = await get_ingest_config()

    async with get_session() as session:
        # Count by status
        status_query = text("""
            SELECT status, COUNT(*) as count
            FROM available_files
            GROUP BY status
        """)
        result = await session.execute(status_query)
        status_counts = {row.status: row.count for row in result.fetchall()}

        # Count by type
        type_query = text("""
            SELECT file_type, COUNT(*) as count
            FROM available_files
            GROUP BY file_type
        """)
        result = await session.execute(type_query)
        type_counts = {row.file_type: row.count for row in result.fetchall()}

    return IngestStatusResponse(
        enabled=config.enabled,
        server_url=config.server_url,
        files_by_status=status_counts,
        files_by_type=type_counts,
    )


@router.get("/screengrabs", response_model=ScreengrabListResponse)
async def list_screengrabs(
    status: Optional[str] = Query(default=None, description="Filter by status: new, attached, no_match, ignored"),
    limit: int = Query(default=50, le=200),
) -> ScreengrabListResponse:
    """
    List screengrab files discovered on the ingest server.

    Args:
        status: Optional filter by status
        limit: Maximum results to return

    Returns:
        List of screengrab files with their status
    """
    async with get_session() as session:
        # Build query
        query = """
            SELECT id, remote_url, filename, media_id, status,
                   first_seen_at, airtable_record_id, attached_at
            FROM available_files
            WHERE file_type = 'screengrab'
        """
        params = {"limit": limit}

        if status:
            query += " AND status = :status"
            params["status"] = status

        query += " ORDER BY first_seen_at DESC LIMIT :limit"

        result = await session.execute(text(query), params)
        rows = result.fetchall()

        screengrabs = [
            ScreengrabFile(
                id=row.id,
                filename=row.filename,
                remote_url=row.remote_url,
                media_id=row.media_id,
                status=row.status,
                first_seen_at=row.first_seen_at,
                sst_record_id=row.airtable_record_id,
                attached_at=row.attached_at,
            )
            for row in rows
        ]

        # Get totals
        totals_query = text("""
            SELECT status, COUNT(*) as count
            FROM available_files
            WHERE file_type = 'screengrab'
            GROUP BY status
        """)
        result = await session.execute(totals_query)
        totals = {row.status: row.count for row in result.fetchall()}

    return ScreengrabListResponse(
        screengrabs=screengrabs,
        total_new=totals.get("new", 0),
        total_attached=totals.get("attached", 0),
        total_no_match=totals.get("no_match", 0),
    )


@router.get("/screengrabs/for-media-id/{media_id}", response_model=ScreengrabListResponse)
async def get_screengrabs_for_media_id(
    media_id: str,
    include_attached: bool = Query(default=False, description="Include already-attached screengrabs (default: false)"),
) -> ScreengrabListResponse:
    """
    Get screengrabs matching a specific Media ID.

    Used by JobDetail page to show contextual screengrab attachment prompts.
    Also checks Airtable SST record for existing attachments.

    Args:
        media_id: The Media ID to match
        include_attached: Whether to include already-attached screengrabs

    Returns:
        List of matching screengrabs with their status, plus existing Airtable attachments count
    """
    from api.services.airtable import AirtableClient

    # Look up existing attachments in Airtable SST record
    sst_existing_attachments: Optional[int] = None
    sst_record_id: Optional[str] = None
    try:
        airtable = AirtableClient()
        sst_record = await airtable.search_sst_by_media_id(media_id)
        if sst_record:
            sst_record_id = sst_record.get("id")
            attachments = sst_record.get("fields", {}).get("Screen Grab", []) or []
            sst_existing_attachments = len(attachments)
    except Exception as e:
        logger.warning(f"Failed to check Airtable attachments for {media_id}: {e}")

    async with get_session() as session:
        # Build query for matching media_id
        query = """
            SELECT id, remote_url, filename, media_id, status,
                   first_seen_at, airtable_record_id, attached_at
            FROM available_files
            WHERE file_type = 'screengrab'
              AND media_id = :media_id
        """
        params = {"media_id": media_id}

        if not include_attached:
            query += " AND status IN ('new', 'no_match')"

        query += " ORDER BY first_seen_at DESC"

        result = await session.execute(text(query), params)
        rows = result.fetchall()

        screengrabs = [
            ScreengrabFile(
                id=row.id,
                filename=row.filename,
                remote_url=row.remote_url,
                media_id=row.media_id,
                status=row.status,
                first_seen_at=row.first_seen_at,
                sst_record_id=row.airtable_record_id,
                attached_at=row.attached_at,
            )
            for row in rows
        ]

        # Get totals for this media_id
        totals_query = text("""
            SELECT status, COUNT(*) as count
            FROM available_files
            WHERE file_type = 'screengrab' AND media_id = :media_id
            GROUP BY status
        """)
        result = await session.execute(totals_query, {"media_id": media_id})
        totals = {row.status: row.count for row in result.fetchall()}

    return ScreengrabListResponse(
        screengrabs=screengrabs,
        total_new=totals.get("new", 0),
        total_attached=totals.get("attached", 0),
        total_no_match=totals.get("no_match", 0),
        sst_existing_attachments=sst_existing_attachments,
        sst_record_id=sst_record_id,
    )


@router.post("/screengrabs/{file_id}/attach", response_model=AttachResponse)
async def attach_screengrab(file_id: int) -> AttachResponse:
    """
    Attach a single screengrab to its matching SST record.

    SAFETY: This operation APPENDS to existing attachments, never replaces them.

    Args:
        file_id: ID from available_files table

    Returns:
        Attachment result including before/after counts
    """
    try:
        attacher = get_screengrab_attacher()
        result = await attacher.attach_from_available_file(file_id)

        return AttachResponse(
            success=result.success,
            media_id=result.media_id,
            filename=result.filename,
            sst_record_id=result.sst_record_id,
            attachments_before=result.attachments_before,
            attachments_after=result.attachments_after,
            error_message=result.error_message,
            skipped_duplicate=result.skipped_duplicate,
        )
    except Exception as e:
        logger.error(f"Attach failed for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/screengrabs/attach-all", response_model=BatchAttachResponse)
async def attach_all_screengrabs() -> BatchAttachResponse:
    """
    Attach all pending screengrabs that have matching SST records.

    SAFETY: This operation APPENDS to existing attachments, never replaces them.
    Each screengrab is processed individually with full audit logging.

    Returns:
        Batch results including counts of attached, skipped, and errors
    """
    try:
        attacher = get_screengrab_attacher()
        result = await attacher.attach_all_pending()

        return BatchAttachResponse(
            total_processed=result.total_processed,
            attached=result.attached,
            skipped_no_match=result.skipped_no_match,
            skipped_duplicate=result.skipped_duplicate,
            errors=result.errors,
        )
    except Exception as e:
        logger.error(f"Batch attach failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/screengrabs/{file_id}/ignore")
async def ignore_screengrab(file_id: int) -> dict:
    """
    Mark a screengrab as ignored (won't appear in pending list).

    Args:
        file_id: ID from available_files table

    Returns:
        Success message
    """
    async with get_session() as session:
        query = text("""
            UPDATE available_files
            SET status = 'ignored',
                status_changed_at = :now
            WHERE id = :file_id AND file_type = 'screengrab'
        """)
        result = await session.execute(
            query,
            {
                "file_id": file_id,
                "now": datetime.utcnow().isoformat(),
            },
        )

        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Screengrab not found")

    return {"success": True, "message": f"Screengrab {file_id} ignored"}


@router.post("/screengrabs/{file_id}/unignore")
async def unignore_screengrab(file_id: int) -> dict:
    """
    Restore an ignored screengrab to 'new' status.

    Args:
        file_id: ID from available_files table

    Returns:
        Success message
    """
    async with get_session() as session:
        query = text("""
            UPDATE available_files
            SET status = 'new',
                status_changed_at = :now
            WHERE id = :file_id AND file_type = 'screengrab' AND status = 'ignored'
        """)
        result = await session.execute(
            query,
            {
                "file_id": file_id,
                "now": datetime.utcnow().isoformat(),
            },
        )

        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Screengrab not found or not currently ignored")

    return {"success": True, "message": f"Screengrab {file_id} restored to new"}


# =============================================================================
# Transcript Action Endpoints (Sprint 11.1 Wave 3)
# =============================================================================


class QueueTranscriptResponse(BaseModel):
    """Response from queuing a transcript."""

    success: bool
    file_id: int
    media_id: Optional[str]
    local_path: Optional[str] = None
    job_id: Optional[int] = None
    error: Optional[str] = None


class BulkQueueRequest(BaseModel):
    """Request to queue multiple transcripts."""

    file_ids: List[int]


class BulkQueueResponse(BaseModel):
    """Response from bulk queue operation."""

    total_requested: int
    queued: int
    failed: int
    results: List[QueueTranscriptResponse]


@router.post("/transcripts/{file_id}/queue", response_model=QueueTranscriptResponse)
async def queue_transcript(file_id: int) -> QueueTranscriptResponse:
    """
    Queue a transcript for processing.

    Downloads the SRT file from the ingest server to the local transcripts/
    folder, then creates a job for processing.

    Args:
        file_id: ID from available_files table

    Returns:
        Queue result including local path and job ID
    """
    from api.models.job import JobCreate
    from api.services.database import create_job

    # Get scanner with config
    config = await get_ingest_config()
    scanner = IngestScanner(
        base_url=config.server_url,
        directories=config.directories,
        ignore_directories=config.ignore_directories,
    )

    # Download the file
    download_result = await scanner.download_file(file_id)

    if not download_result["success"]:
        return QueueTranscriptResponse(
            success=False,
            file_id=file_id,
            media_id=None,
            error=download_result.get("error", "Download failed"),
        )

    # Create a job for this transcript
    try:
        # Extract project name from Media ID or filename
        media_id = download_result.get("media_id")
        filename = download_result["filename"]
        project_name = media_id if media_id else filename.rsplit(".", 1)[0]

        # transcript_file should be relative to transcripts/ folder
        local_path = download_result["local_path"]
        if local_path.startswith("transcripts/"):
            transcript_file = local_path[len("transcripts/") :]
        else:
            transcript_file = filename

        job_create = JobCreate(
            project_name=project_name,
            transcript_file=transcript_file,
        )
        job = await create_job(job_create)

        # Update available_files with job_id
        async with get_session() as session:
            update_query = text("""
                UPDATE available_files
                SET job_id = :job_id
                WHERE id = :file_id
            """)
            await session.execute(
                update_query,
                {
                    "job_id": job.id,
                    "file_id": file_id,
                },
            )

        logger.info(f"Queued transcript {file_id}: job {job.id}")

        return QueueTranscriptResponse(
            success=True,
            file_id=file_id,
            media_id=download_result["media_id"],
            local_path=download_result["local_path"],
            job_id=job.id,
        )

    except Exception as e:
        logger.error(f"Failed to create job for transcript {file_id}: {e}")
        return QueueTranscriptResponse(
            success=False,
            file_id=file_id,
            media_id=download_result.get("media_id"),
            local_path=download_result.get("local_path"),
            error=f"Failed to create job: {e}",
        )


@router.post("/transcripts/queue", response_model=BulkQueueResponse)
async def queue_transcripts_bulk(request: BulkQueueRequest) -> BulkQueueResponse:
    """
    Queue multiple transcripts for processing.

    Downloads each SRT file and creates jobs for them.

    Args:
        request: BulkQueueRequest with list of file IDs

    Returns:
        Bulk queue results
    """
    results = []
    queued = 0
    failed = 0

    for file_id in request.file_ids:
        result = await queue_transcript(file_id)
        results.append(result)
        if result.success:
            queued += 1
        else:
            failed += 1

    return BulkQueueResponse(
        total_requested=len(request.file_ids),
        queued=queued,
        failed=failed,
        results=results,
    )


@router.post("/transcripts/{file_id}/ignore")
async def ignore_transcript(file_id: int) -> dict:
    """
    Mark a transcript as ignored (won't appear in pending list).

    Args:
        file_id: ID from available_files table

    Returns:
        Success message
    """
    async with get_session() as session:
        query = text("""
            UPDATE available_files
            SET status = 'ignored',
                status_changed_at = :now
            WHERE id = :file_id AND file_type = 'transcript'
        """)
        result = await session.execute(
            query,
            {
                "file_id": file_id,
                "now": datetime.utcnow().isoformat(),
            },
        )

        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Transcript not found")

    logger.info(f"Ignored transcript {file_id}")
    return {"success": True, "message": f"Transcript {file_id} ignored"}


@router.post("/transcripts/{file_id}/unignore")
async def unignore_transcript(file_id: int) -> dict:
    """
    Restore an ignored transcript to 'new' status.

    Args:
        file_id: ID from available_files table

    Returns:
        Success message
    """
    async with get_session() as session:
        query = text("""
            UPDATE available_files
            SET status = 'new',
                status_changed_at = :now
            WHERE id = :file_id AND file_type = 'transcript' AND status = 'ignored'
        """)
        result = await session.execute(
            query,
            {
                "file_id": file_id,
                "now": datetime.utcnow().isoformat(),
            },
        )

        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Transcript not found or not currently ignored")

    logger.info(f"Restored transcript {file_id} to new")
    return {"success": True, "message": f"Transcript {file_id} restored to new"}


# =============================================================================
# Configuration Endpoints (Sprint 11.1)
# =============================================================================


@router.get("/config", response_model=IngestConfigResponse)
async def get_config_endpoint() -> IngestConfigResponse:
    """
    Get current ingest scanner configuration.

    Returns settings for scheduled scanning including:
    - enabled: Whether scanning is active
    - scan_interval_hours: Hours between scans
    - scan_time: Time of day to run scan (HH:MM)
    - last_scan_at: When last scan completed
    - next_scan_at: When next scan is scheduled
    """
    config = await get_ingest_config()
    next_scan = await get_next_scan_time()

    return IngestConfigResponse(
        enabled=config.enabled,
        scan_interval_hours=config.scan_interval_hours,
        scan_time=config.scan_time,
        last_scan_at=config.last_scan_at,
        last_scan_success=config.last_scan_success,
        server_url=config.server_url,
        directories=config.directories,
        ignore_directories=config.ignore_directories,
        next_scan_at=next_scan,
    )


@router.put("/config", response_model=IngestConfigResponse)
async def update_config_endpoint(updates: IngestConfigUpdate) -> IngestConfigResponse:
    """
    Update ingest scanner configuration.

    Allows updating:
    - enabled: Turn scheduled scanning on/off
    - scan_interval_hours: Hours between scans (1-168)
    - scan_time: Time of day to run scan (HH:MM format)

    Note: Changes take effect on next scheduled scan.
    """
    # Validate scan_time format if provided
    if updates.scan_time:
        parts = updates.scan_time.split(":")
        if len(parts) != 2:
            raise HTTPException(status_code=400, detail="scan_time must be in HH:MM format")
        try:
            hour, minute = int(parts[0]), int(parts[1])
            if not (0 <= hour <= 23) or not (0 <= minute <= 59):
                raise ValueError()
        except ValueError:
            raise HTTPException(status_code=400, detail="scan_time must be a valid time (00:00 to 23:59)")

    config = await update_ingest_config(updates)

    # Reconfigure scheduler with new settings
    await configure_scheduler()

    next_scan = await get_next_scan_time()

    logger.info(
        f"Ingest config updated: enabled={config.enabled}, "
        f"interval={config.scan_interval_hours}h, time={config.scan_time}"
    )

    return IngestConfigResponse(
        enabled=config.enabled,
        scan_interval_hours=config.scan_interval_hours,
        scan_time=config.scan_time,
        last_scan_at=config.last_scan_at,
        last_scan_success=config.last_scan_success,
        server_url=config.server_url,
        directories=config.directories,
        ignore_directories=config.ignore_directories,
        next_scan_at=next_scan,
    )
