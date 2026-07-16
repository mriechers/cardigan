"""Upload router for Cardigan API.

Provides bulk transcript upload and single media (audio/video) upload
endpoints.
"""

import json
import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, ValidationError

from api.middleware.rate_limit import RATE_EXPENSIVE, limiter
from api.models.job import JobCreate
from api.services import database, glossary
from api.services import media as media_service
from api.services.airtable import AirtableClient
from api.services.utils import extract_media_id

logger = logging.getLogger(__name__)

router = APIRouter()

# Configuration
TRANSCRIPTS_DIR = Path(os.getenv("TRANSCRIPTS_DIR", "transcripts"))
ALLOWED_EXTENSIONS = {".txt", ".srt"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_BATCH_SIZE = 20

# Media upload (audio upload mode). LAN/Tailscale deployment — plain
# multipart with a generous cap, streamed to disk. The Cloudflare tunnel
# profile caps request bodies around 100MB; media upload is LAN-only.
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "media"))
MEDIA_MAX_UPLOAD_BYTES = int(os.getenv("MEDIA_MAX_UPLOAD_BYTES", str(2 * 1024**3)))  # 2 GB
MEDIA_KEEP_ORIGINAL = os.getenv("MEDIA_KEEP_ORIGINAL", "false").lower() in ("1", "true", "yes")
UPLOAD_CHUNK_BYTES = 1024 * 1024


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
    try:
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        raise HTTPException(
            status_code=500,
            detail="Server cannot write to transcripts directory. Check file permissions.",
        )

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


# ============================================================================
# Media upload (audio upload mode)
# ============================================================================


class IntakeForm(BaseModel):
    """Intake metadata submitted alongside a media upload."""

    project_name: str = Field(..., min_length=1, max_length=200)
    speakers: List[str] = Field(
        default_factory=list, max_length=20, description="Expected speakers, most prominent first"
    )
    context_terms: List[str] = Field(
        default_factory=list, max_length=50, description="Topic terms / proper nouns for this recording"
    )
    add_to_glossary: bool = Field(default=False, description="Append speakers + context terms to the running glossary")
    language: str = Field(default="en", max_length=10, description="ISO language hint; empty lets Whisper detect")


class MediaUploadResponse(BaseModel):
    """Response for a media upload."""

    job_id: int
    media_file: str
    original_filename: str
    audio_extracted: bool
    duration_seconds: Optional[float] = None
    glossary_terms_added: int = 0


def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    """First non-existing path for stem+suffix, adding -2, -3, ... as needed."""
    candidate = directory / f"{stem}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


async def _stream_to_disk(file: UploadFile, dest: Path, max_bytes: int) -> int:
    """Stream an UploadFile to disk in chunks; raise 413 past max_bytes."""
    total = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds maximum size of {max_bytes / 1024**3:.1f} GB",
                    )
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return total


@router.post("/media", response_model=MediaUploadResponse)
@limiter.limit(RATE_EXPENSIVE)
async def upload_media(
    request: Request,
    file: UploadFile = File(..., description="Audio or video file"),
    intake: str = Form(..., description="JSON IntakeForm: project_name, speakers, context_terms, ..."),
) -> MediaUploadResponse:
    """Upload an audio/video file and queue a transcription (media) job.

    Video files have their audio track extracted server-side (stream copy
    when possible, 16 kHz mono AAC transcode otherwise) and the original
    video is deleted unless MEDIA_KEEP_ORIGINAL is set. The job runs a
    WhisperX transcription stage, then pauses in awaiting_review for the
    editor to correct the transcript before the LLM pipeline runs.
    """
    try:
        intake_form = IntakeForm.model_validate(json.loads(intake))
    except (json.JSONDecodeError, ValidationError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid intake form: {e}")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in media_service.MEDIA_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported media type: {suffix or '(none)'}. "
            f"Allowed: {', '.join(sorted(media_service.MEDIA_EXTENSIONS))}",
        )

    try:
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        raise HTTPException(
            status_code=500,
            detail="Server cannot write to media directory. Check file permissions.",
        )

    stem = database.sanitize_path_component(Path(file.filename or "upload").stem)
    upload_path = _unique_path(MEDIA_DIR, stem, suffix)
    await _stream_to_disk(file, upload_path, MEDIA_MAX_UPLOAD_BYTES)
    logger.info(f"Saved media upload: {upload_path}")

    # Video → extract audio track, drop the video
    audio_extracted = False
    audio_path = upload_path
    if suffix in media_service.VIDEO_EXTENSIONS:
        target = _unique_path(MEDIA_DIR, stem, ".m4a")
        try:
            audio_path = await media_service.extract_audio(upload_path, target)
        except media_service.AudioExtractionError as e:
            upload_path.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=f"Audio extraction failed: {e}")
        audio_extracted = True
        if not MEDIA_KEEP_ORIGINAL:
            upload_path.unlink(missing_ok=True)
            logger.info(f"Deleted original video after extraction: {upload_path.name}")

    duration_seconds = await media_service.get_duration_seconds(audio_path)

    # Glossary opt-in: speaker names + context terms become whisper prompt terms
    glossary_terms_added = 0
    if intake_form.add_to_glossary:
        try:
            glossary_terms_added = glossary.add_whisper_terms(intake_form.speakers + intake_form.context_terms)
        except Exception as e:
            logger.warning(f"Glossary opt-in failed (continuing): {e}")

    # Duplicate check by media ID when the filename carries one
    media_id = extract_media_id(audio_path.name)
    if media_id:
        existing_jobs = await database.find_jobs_by_media_id(media_id)
        if existing_jobs:
            audio_path.unlink(missing_ok=True)
            existing = existing_jobs[0]
            raise HTTPException(
                status_code=409,
                detail=f"Already exists as job {existing.id} ({existing.status.value})",
            )

    intake_payload = {
        "speakers": intake_form.speakers,
        "context_terms": intake_form.context_terms,
        "glossary_terms_added": glossary_terms_added,
        "language": intake_form.language,
        "original_filename": file.filename or "",
    }
    job = await database.create_job(
        JobCreate(
            project_name=intake_form.project_name,
            transcript_file="",
            job_type="media",
            media_file=audio_path.name,
            intake=intake_payload,
        )
    )

    # Record duration for queue display; SST auto-link when a media ID matched
    from api.models.job import JobUpdate

    update = JobUpdate(duration_minutes=round(duration_seconds / 60, 1) if duration_seconds else None)
    if media_id:
        update.media_id = media_id
        try:
            airtable_client = AirtableClient()
            record = await airtable_client.search_sst_by_media_id(media_id)
            if record:
                update.airtable_record_id = record["id"]
                update.airtable_url = airtable_client.get_sst_url(record["id"])
        except Exception as e:
            logger.warning(f"Job {job.id}: Airtable lookup failed - {e}")
    if update.model_dump(exclude_none=True):
        job = await database.update_job(job.id, update) or job

    logger.info(f"Queued media job {job.id} for {audio_path.name}")
    return MediaUploadResponse(
        job_id=job.id,
        media_file=audio_path.name,
        original_filename=file.filename or "",
        audio_extracted=audio_extracted,
        duration_seconds=duration_seconds,
        glossary_terms_added=glossary_terms_added,
    )
