"""Upload router for Cardigan API.

Provides bulk transcript upload endpoint.
"""

import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from api.middleware.rate_limit import RATE_EXPENSIVE, limiter
from api.models.job import JobCreate
from api.services import database
from api.services.airtable import AirtableClient
from api.services.utils import extract_media_id

logger = logging.getLogger(__name__)

router = APIRouter()

# Configuration
TRANSCRIPTS_DIR = Path(os.getenv("TRANSCRIPTS_DIR", "transcripts"))
ALLOWED_EXTENSIONS = {".txt", ".srt"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_BATCH_SIZE = 20


class UploadStatus(BaseModel):
    """Status for a single file upload."""

    filename: str
    success: bool
    job_id: Optional[int] = None
    error: Optional[str] = None


class UploadResponse(BaseModel):
    """Response for bulk upload."""

    uploaded: int
    failed: int
    files: List[UploadStatus]


@router.post("/transcripts", response_model=UploadResponse)
@limiter.limit(RATE_EXPENSIVE)
async def upload_transcripts(
    request: Request,
    files: List[UploadFile] = File(..., description="Transcript files (.txt or .srt)"),
) -> UploadResponse:
    """Upload multiple transcript files and queue for processing.

    Accepts batch uploads of .txt or .srt files. Each file is:
    1. Validated (type, size)
    2. Saved to transcripts/ directory
    3. Queued for processing

    Constraints:
    - Maximum batch size: 20 files
    - Maximum file size: 50 MB per file
    - Allowed types: .txt, .srt

    Returns status for each file upload attempt.

    Args:
        files: List of transcript files to upload

    Returns:
        Upload response with status for each file

    Raises:
        HTTPException: 400 if batch size exceeded or no files provided
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    if len(files) > MAX_BATCH_SIZE:
        raise HTTPException(status_code=400, detail=f"Batch size exceeds maximum of {MAX_BATCH_SIZE} files")

    # Ensure transcripts directory exists
    TRANSCRIPTS_DIR.mkdir(exist_ok=True)

    results: List[UploadStatus] = []
    uploaded_count = 0
    failed_count = 0

    for file in files:
        try:
            # Validate file extension
            file_ext = Path(file.filename or "").suffix.lower()
            if file_ext not in ALLOWED_EXTENSIONS:
                results.append(
                    UploadStatus(
                        filename=file.filename or "unknown",
                        success=False,
                        error=f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
                    )
                )
                failed_count += 1
                continue

            # Read file content
            content = await file.read()

            # Validate file size
            if len(content) > MAX_FILE_SIZE:
                results.append(
                    UploadStatus(
                        filename=file.filename or "unknown",
                        success=False,
                        error=f"File too large. Maximum size: {MAX_FILE_SIZE / 1024 / 1024:.0f} MB",
                    )
                )
                failed_count += 1
                continue

            # Save file to transcripts directory
            file_path = TRANSCRIPTS_DIR / (file.filename or "")
            file_path.write_bytes(content)
            logger.info(f"Saved transcript: {file_path}")

            # Queue for processing
            project_name = file_path.stem
            # Clean up common suffixes
            for suffix in ["_ForClaude", "_forclaude", "_transcript"]:
                if project_name.endswith(suffix):
                    project_name = project_name[: -len(suffix)]

            job_create = JobCreate(
                project_name=project_name,
                transcript_file=file.filename or "",
            )

            # Check for duplicate by media ID
            media_id = extract_media_id(job_create.transcript_file)
            existing_jobs = await database.find_jobs_by_media_id(media_id) if media_id else []
            if existing_jobs:
                existing = existing_jobs[0]
                results.append(
                    UploadStatus(
                        filename=file.filename or "unknown",
                        success=False,
                        error=f"Already exists as job {existing.id} ({existing.status.value})",
                    )
                )
                failed_count += 1
                logger.warning(f"Skipping {file.filename}: duplicate media ID {media_id}")
                continue

            # Create job
            job = await database.create_job(job_create)

            # Attempt auto-link to Airtable SST record
            try:
                if not media_id:
                    raise ValueError("No valid Media ID extracted")
                airtable_client = AirtableClient()
                record = await airtable_client.search_sst_by_media_id(media_id)

                if record:
                    from api.models.job import JobUpdate

                    record_id = record["id"]
                    airtable_url = airtable_client.get_sst_url(record_id)
                    update = JobUpdate(
                        airtable_record_id=record_id,
                        airtable_url=airtable_url,
                        media_id=media_id,
                    )
                    job = await database.update_job(job.id, update)
                    logger.info(f"Job {job.id}: Linked to SST record {record_id}")
                else:
                    from api.models.job import JobUpdate

                    update = JobUpdate(media_id=media_id)
                    job = await database.update_job(job.id, update)
                    logger.warning(f"Job {job.id}: No SST record found for {media_id}")
            except Exception as e:
                logger.warning(f"Job {job.id}: Airtable lookup failed - {e}")

            results.append(UploadStatus(filename=file.filename or "unknown", success=True, job_id=job.id))
            uploaded_count += 1
            logger.info(f"Queued job {job.id} for {file.filename}")

        except Exception as e:
            logger.error(f"Failed to upload {file.filename}: {e}")
            results.append(UploadStatus(filename=file.filename or "unknown", success=False, error=str(e)))
            failed_count += 1

    return UploadResponse(uploaded=uploaded_count, failed=failed_count, files=results)
