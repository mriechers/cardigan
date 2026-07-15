"""Transcript review endpoints for audio upload mode.

After the worker's transcription stage a media job sits in awaiting_review
with two artifacts in its project directory:

- ``transcription_raw.json``    — verbatim WhisperX result (provenance)
- ``transcription_edited.json`` — working copy the editor mutates here

The editor reads both (GET), autosaves segment/speaker edits (PUT), then
either approves (build a speaker-labeled SRT, mine glossary corrections
from the edit diff, reset the LLM phases, requeue) or requests a fresh
transcription with extra prompt terms (retranscribe).
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.models.job import Job, JobPhase, JobStatus, JobUpdate
from api.services import database, glossary
from api.services.database import get_job, update_job
from api.services.transcript_diff import mine_corrections
from api.services.utils import SRTCaption, reset_phases_for_reprocess

logger = logging.getLogger(__name__)

router = APIRouter()

TRANSCRIPTS_DIR = Path(os.getenv("TRANSCRIPTS_DIR", "transcripts"))
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "media"))

RAW_FILENAME = "transcription_raw.json"
EDITED_FILENAME = "transcription_edited.json"
APPROVED_COPY_FILENAME = "transcript_approved.srt"

MEDIA_CONTENT_TYPES = {
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


class TranscriptSegmentEdit(BaseModel):
    """One editable transcript segment."""

    id: int
    start: float
    end: float
    speaker: Optional[str] = None
    text: str


class TranscriptionDocument(BaseModel):
    """The editable working copy (PUT body / part of GET response)."""

    segments: List[TranscriptSegmentEdit]
    speaker_map: Dict[str, str] = Field(default_factory=dict)


class TranscriptionReview(BaseModel):
    """GET response: raw + edited state plus intake context."""

    job_id: int
    status: str
    raw_segments: List[Dict[str, Any]]
    edited: TranscriptionDocument
    diarized: bool
    language: Optional[str] = None
    duration_seconds: Optional[float] = None
    intake: Dict[str, Any] = Field(default_factory=dict)


class ApproveRequest(BaseModel):
    update_glossary: bool = True


class ApproveResponse(BaseModel):
    job_id: int
    status: str
    transcript_file: str
    corrections_added: int


class RetranscribeRequest(BaseModel):
    extra_terms: List[str] = Field(default_factory=list, max_length=50)


def _project_path(job: Job) -> Path:
    return Path(job.project_path)


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _require_media_job(job: Optional[Job], job_id: int) -> Job:
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.job_type != "media":
        raise HTTPException(status_code=400, detail=f"Job {job_id} is not a media job")
    return job


def _seconds_to_ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


def build_srt(segments: List[TranscriptSegmentEdit], speaker_map: Dict[str, str]) -> str:
    """Render edited segments as a speaker-labeled SRT document.

    Caption text gets a "Name: " prefix when the speaker label resolves to a
    non-empty name; unmapped labels are kept as-is only when the transcript
    has more than one distinct speaker (a lone unmapped bucket would just be
    noise on every caption).
    """
    distinct_labels = {seg.speaker for seg in segments if seg.speaker}
    captions = []
    index = 1
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        name = (speaker_map.get(seg.speaker or "", "") or "").strip()
        if name:
            text = f"{name}: {text}"
        elif seg.speaker and len(distinct_labels) > 1:
            text = f"{seg.speaker}: {text}"
        captions.append(
            SRTCaption(
                index=index,
                start_ms=_seconds_to_ms(seg.start),
                end_ms=_seconds_to_ms(seg.end),
                text=text,
            )
        )
        index += 1
    return "\n".join(caption.to_srt() for caption in captions)


@router.get("/{job_id}/transcription", response_model=TranscriptionReview)
async def get_transcription(job_id: int) -> TranscriptionReview:
    """Return the transcription review state (raw + edited + intake)."""
    job = _require_media_job(await get_job(job_id), job_id)

    project = _project_path(job)
    raw = _load_json(project / RAW_FILENAME)
    edited = _load_json(project / EDITED_FILENAME)
    if raw is None or edited is None:
        raise HTTPException(
            status_code=404,
            detail="Transcription artifacts not found — has the transcription stage completed?",
        )

    return TranscriptionReview(
        job_id=job.id,
        status=job.status.value,
        raw_segments=raw.get("segments", []),
        edited=TranscriptionDocument(
            segments=edited.get("segments", []),
            speaker_map=edited.get("speaker_map", {}),
        ),
        diarized=bool(edited.get("diarized")),
        language=edited.get("language"),
        duration_seconds=edited.get("duration_seconds"),
        intake=job.intake or {},
    )


@router.put("/{job_id}/transcription", response_model=TranscriptionDocument)
async def save_transcription_edits(job_id: int, document: TranscriptionDocument) -> TranscriptionDocument:
    """Overwrite the edited working copy (autosave target).

    Only allowed while the job is awaiting review.
    """
    job = _require_media_job(await get_job(job_id), job_id)
    if job.status != JobStatus.awaiting_review:
        raise HTTPException(
            status_code=400,
            detail=f"Edits are only allowed in awaiting_review (job is '{job.status.value}')",
        )

    project = _project_path(job)
    existing = _load_json(project / EDITED_FILENAME)
    if existing is None:
        raise HTTPException(status_code=404, detail="Transcription artifacts not found")

    existing["segments"] = [seg.model_dump() for seg in document.segments]
    existing["speaker_map"] = document.speaker_map
    (project / EDITED_FILENAME).write_text(json.dumps(existing, indent=2))
    return document


@router.post("/{job_id}/transcription/approve", response_model=ApproveResponse)
async def approve_transcription(job_id: int, request: ApproveRequest = ApproveRequest()) -> ApproveResponse:
    """Approve the reviewed transcript and hand the job to the LLM pipeline.

    Builds a speaker-labeled SRT in transcripts/, mines the raw-vs-edited
    diff for glossary corrections (misheard proper nouns -> corrected
    spellings), resets the LLM phases, and requeues the job.
    """
    job = _require_media_job(await get_job(job_id), job_id)
    if job.status != JobStatus.awaiting_review:
        raise HTTPException(
            status_code=400,
            detail=f"Only awaiting_review jobs can be approved (job is '{job.status.value}')",
        )

    project = _project_path(job)
    raw = _load_json(project / RAW_FILENAME)
    edited = _load_json(project / EDITED_FILENAME)
    if raw is None or edited is None:
        raise HTTPException(status_code=404, detail="Transcription artifacts not found")

    segments = [TranscriptSegmentEdit(**seg) for seg in edited.get("segments", [])]
    if not any(seg.text.strip() for seg in segments):
        raise HTTPException(status_code=400, detail="Transcript is empty — nothing to approve")
    speaker_map = edited.get("speaker_map", {})

    # 1. Speaker-labeled SRT into transcripts/ (+ provenance copy)
    srt_content = build_srt(segments, speaker_map)
    safe_name = database.sanitize_path_component(job.project_name or f"job_{job.id}")
    transcript_filename = f"{safe_name}.srt"
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    (TRANSCRIPTS_DIR / transcript_filename).write_text(srt_content)
    (project / APPROVED_COPY_FILENAME).write_text(srt_content)
    logger.info(f"Job {job_id}: approved transcript written to {transcript_filename}")

    # 2. Mine glossary corrections from the edit diff
    corrections_added = 0
    if request.update_glossary:
        raw_by_id = {seg.get("id"): (seg.get("text") or "").strip() for seg in raw.get("segments", [])}
        pairs = [
            (raw_by_id[seg.id], seg.text.strip())
            for seg in segments
            if seg.id in raw_by_id and raw_by_id[seg.id] != seg.text.strip()
        ]
        intake = job.intake or {}
        known_terms = (intake.get("speakers") or []) + (intake.get("context_terms") or [])
        # Mapped speaker names are known-correct too
        known_terms += [name for name in speaker_map.values() if name]
        try:
            entries = mine_corrections(pairs, known_terms=known_terms)
            if entries:
                corrections_added = glossary.add_corrections(entries)
        except Exception as e:
            logger.warning(f"Job {job_id}: glossary mining failed (continuing): {e}")

    # 3. Reset LLM phases (keep the completed transcription stage) and requeue.
    # Metrics come straight from the approved segments — no file re-read.
    word_count = sum(len(seg.text.split()) for seg in segments)
    last_end = max((seg.end for seg in segments if seg.text.strip()), default=0.0)
    duration_minutes = round(last_end / 60, 1) if last_end else None
    reset_phases = reset_phases_for_reprocess(job.phases, skip=("transcription",))
    updated = await update_job(
        job_id,
        JobUpdate(
            status=JobStatus.pending,
            transcript_file=transcript_filename,
            duration_minutes=duration_minutes,
            word_count=word_count,
            error_message="",
            actual_cost=0.0,
            phases=[JobPhase(**p) for p in reset_phases],
        ),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    logger.info(f"Job {job_id}: transcript approved, {corrections_added} glossary corrections, requeued")
    return ApproveResponse(
        job_id=job_id,
        status=updated.status.value,
        transcript_file=transcript_filename,
        corrections_added=corrections_added,
    )


@router.post("/{job_id}/transcription/retranscribe", response_model=Job)
async def retranscribe(job_id: int, request: RetranscribeRequest = RetranscribeRequest()) -> Job:
    """Re-run the transcription stage, optionally with extra prompt terms.

    Merges extra_terms into the job's intake context terms, resets the
    transcription phase, discards the edited working copy, and requeues.
    """
    job = _require_media_job(await get_job(job_id), job_id)
    if job.status not in (JobStatus.awaiting_review, JobStatus.failed):
        raise HTTPException(
            status_code=400,
            detail=f"Only awaiting_review or failed media jobs can be re-transcribed (job is '{job.status.value}')",
        )

    intake = dict(job.intake or {})
    if request.extra_terms:
        existing_terms = intake.get("context_terms") or []
        seen = {t.lower() for t in existing_terms}
        for term in request.extra_terms:
            cleaned = " ".join(term.split())
            if cleaned and cleaned.lower() not in seen:
                existing_terms.append(cleaned)
                seen.add(cleaned.lower())
        intake["context_terms"] = existing_terms

    # Reset every phase (transcription included) and drop the working copy
    reset_phases = reset_phases_for_reprocess(job.phases)
    project = _project_path(job)
    (project / EDITED_FILENAME).unlink(missing_ok=True)

    updated = await update_job(
        job_id,
        JobUpdate(
            status=JobStatus.pending,
            intake=intake,
            error_message="",
            current_phase="transcription",
            phases=[JobPhase(**p) for p in reset_phases],
        ),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    logger.info(f"Job {job_id}: re-transcription queued ({len(request.extra_terms)} extra terms)")
    return updated


@router.get("/{job_id}/media")
async def get_media(job_id: int) -> FileResponse:
    """Serve the job's audio file for review playback (supports Range)."""
    job = _require_media_job(await get_job(job_id), job_id)
    if not job.media_file:
        raise HTTPException(status_code=404, detail="Job has no media file")

    media_path = MEDIA_DIR / job.media_file
    if not media_path.exists():
        raise HTTPException(status_code=404, detail=f"Media file not found: {job.media_file}")

    content_type = MEDIA_CONTENT_TYPES.get(media_path.suffix.lower(), "application/octet-stream")
    return FileResponse(media_path, media_type=content_type, filename=media_path.name)
