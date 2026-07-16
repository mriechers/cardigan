"""Diarization microservice — WhisperX transcription + speaker identification.

Two endpoints share one pipeline:

- ``POST /diarize``    — speaker segments only (pipeline verification hints).
- ``POST /transcribe`` — full transcript: text, word timestamps, speakers.
                         Accepts an ``initial_prompt`` (speaker names +
                         glossary terms) to bias Whisper toward known
                         spellings.

WhisperX runs on the CPU and a single job can take many minutes, so the
pipeline executes in a worker thread (keeping /health responsive) behind a
single-flight lock: a second request gets ``503`` with ``Retry-After``
instead of queueing, and the caller defers the job.
"""

import asyncio
import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("diarization")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Cardigan Diarization Service", version="0.2.0")

# WhisperX model — loaded once at startup
_model = None
_diarize_pipeline = None

# One WhisperX run at a time: the box is CPU-bound and a second concurrent
# run would roughly double both wall clocks. Callers get 503 + Retry-After.
_pipeline_lock = threading.Lock()
BUSY_RETRY_AFTER_SECONDS = 300

# Configuration
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "base")
DEVICE = "cpu"
COMPUTE_TYPE = "int8"

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".mp4", ".mkv", ".mov", ".webm"}


def _read_hf_token() -> str:
    """Read HuggingFace token from Docker secret file or environment."""
    token_file = os.environ.get("HF_TOKEN_FILE", "/run/secrets/hf_token")
    if os.path.exists(token_file):
        return Path(token_file).read_text().strip()
    return os.environ.get("HF_TOKEN", "")


HF_TOKEN = _read_hf_token()


class DiarizationSegment(BaseModel):
    start: float
    end: float
    speaker: str
    confidence: float


class DiarizationResponse(BaseModel):
    duration_seconds: float
    speakers: list[str]
    segments: list[DiarizationSegment]


class Word(BaseModel):
    word: str
    start: Optional[float] = None
    end: Optional[float] = None
    score: Optional[float] = None
    speaker: Optional[str] = None


class TranscriptSegment(BaseModel):
    id: int
    start: float
    end: float
    text: str
    speaker: Optional[str] = None
    words: list[Word] = []


class TranscribeResponse(BaseModel):
    language: str
    duration_seconds: float
    speakers: list[str]
    diarized: bool
    segments: list[TranscriptSegment]


@app.get("/health")
def health():
    """Health check. Returns ready=True only when the WhisperX model is loaded."""
    return {
        "status": "ok",
        "model_loaded": _model is not None,
        "ready": _model is not None,
        "busy": _pipeline_lock.locked(),
    }


@app.on_event("startup")
def load_model():
    """Load WhisperX model and diarization pipeline at startup."""
    global _model, _diarize_pipeline
    import whisperx

    logger.info(f"Loading WhisperX model: {WHISPER_MODEL_SIZE} (device={DEVICE})")
    _model = whisperx.load_model(
        WHISPER_MODEL_SIZE,
        device=DEVICE,
        compute_type=COMPUTE_TYPE,
    )
    logger.info("WhisperX model loaded")

    if HF_TOKEN:
        logger.info("Loading pyannote diarization pipeline")
        # whisperx >= 3.4 no longer re-exports DiarizationPipeline at top level
        from whisperx.diarize import DiarizationPipeline

        _diarize_pipeline = DiarizationPipeline(
            use_auth_token=HF_TOKEN,
            device=DEVICE,
        )
        logger.info("Diarization pipeline loaded")
    else:
        logger.warning(
            "HF_TOKEN not set — diarization pipeline unavailable. "
            "Speaker labels will not be assigned. "
            "Set HF_TOKEN to a HuggingFace token that has accepted pyannote terms."
        )


def _run_pipeline(
    media_path: str,
    initial_prompt: str = "",
    language: Optional[str] = None,
    diarize: bool = True,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
) -> tuple[dict, str, bool]:
    """Blocking WhisperX pipeline: load -> transcribe -> align -> diarize.

    Caller must hold ``_pipeline_lock``. Returns (aligned_result,
    detected_language, diarized).

    ``initial_prompt`` is applied by swapping the pipeline's options
    dataclass for the duration of the transcribe call — faster-whisper fixes
    the prompt at load_model() time, and whisperx itself uses the same
    dataclasses.replace swap for per-call option overrides. Safe because the
    lock guarantees a single concurrent run.
    """
    from dataclasses import replace as dc_replace

    import whisperx

    audio = whisperx.load_audio(media_path)

    original_options = _model.options
    if initial_prompt:
        _model.options = dc_replace(_model.options, initial_prompt=initial_prompt)
    try:
        result = _model.transcribe(audio, batch_size=4, language=language or None)
    finally:
        _model.options = original_options

    detected_language = result["language"]
    logger.info(f"Transcription complete: {len(result['segments'])} segments ({detected_language})")

    align_model, align_metadata = whisperx.load_align_model(
        language_code=detected_language,
        device=DEVICE,
    )
    result = whisperx.align(
        result["segments"],
        align_model,
        align_metadata,
        audio,
        DEVICE,
        return_char_alignments=False,
    )
    logger.info("Alignment complete")

    diarized = False
    if diarize and _diarize_pipeline is not None:
        diarize_segments = _diarize_pipeline(audio, min_speakers=min_speakers, max_speakers=max_speakers)
        result = whisperx.assign_word_speakers(diarize_segments, result)
        diarized = True
        logger.info("Speaker diarization complete")

    return result, detected_language, diarized


def build_transcribe_response(result: dict, language: str, diarized: bool) -> TranscribeResponse:
    """Shape an aligned WhisperX result dict into a TranscribeResponse.

    Pure function — unit-testable with a canned result dict.
    """
    segments = []
    speakers_seen = set()
    duration_seconds = 0.0

    for idx, seg in enumerate(result.get("segments", [])):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = seg.get("speaker")
        start = round(float(seg.get("start", 0.0)), 3)
        end = round(float(seg.get("end", 0.0)), 3)
        words = [
            Word(
                word=w.get("word", ""),
                start=w.get("start"),
                end=w.get("end"),
                score=w.get("score"),
                speaker=w.get("speaker"),
            )
            for w in seg.get("words", [])
        ]
        segments.append(TranscriptSegment(id=idx, start=start, end=end, text=text, speaker=speaker, words=words))
        if speaker:
            speakers_seen.add(speaker)
        duration_seconds = max(duration_seconds, end)

    return TranscribeResponse(
        language=language,
        duration_seconds=round(duration_seconds, 1),
        speakers=sorted(speakers_seen),
        diarized=diarized,
        segments=segments,
    )


def build_diarize_response(result: dict) -> DiarizationResponse:
    """Shape an aligned WhisperX result dict into the legacy /diarize response."""
    segments = []
    speakers_seen = set()
    duration_seconds = 0.0

    for seg in result.get("segments", []):
        speaker = seg.get("speaker", "Unknown")
        start = round(float(seg.get("start", 0.0)), 2)
        end = round(float(seg.get("end", 0.0)), 2)
        # WhisperX doesn't provide per-segment confidence for diarization;
        # use word-level confidence average if available, else 0.0
        words = seg.get("words", [])
        if words:
            confidences = [w.get("score", 0.0) for w in words if "score" in w]
            confidence = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
        else:
            confidence = 0.0

        segments.append(DiarizationSegment(start=start, end=end, speaker=speaker, confidence=confidence))
        speakers_seen.add(speaker)
        duration_seconds = max(duration_seconds, end)

    return DiarizationResponse(
        duration_seconds=round(duration_seconds, 1),
        speakers=sorted(speakers_seen),
        segments=segments,
    )


def _busy_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "transcription busy"},
        headers={"Retry-After": str(BUSY_RETRY_AFTER_SECONDS)},
    )


def _validate_upload(file: UploadFile) -> str:
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {suffix}. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )
    return suffix


async def _save_upload(file: UploadFile, suffix: str) -> str:
    """Stream the upload to a temp file (WhisperX needs a real path)."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        await asyncio.to_thread(shutil.copyfileobj, file.file, tmp)
        return tmp.name


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(
    file: UploadFile = File(...),
    initial_prompt: str = Form(""),
    language: str = Form(""),
    diarize: bool = Form(True),
    min_speakers: Optional[int] = Form(None),
    max_speakers: Optional[int] = Form(None),
):
    """Transcribe an uploaded audio/video file, with optional diarization.

    Returns full transcript segments (text, timestamps, words, speaker
    labels). ``diarized`` is false when the pyannote pipeline is unavailable
    (no HF token) or ``diarize=false`` — callers should degrade to a single
    speaker bucket, not error.
    """
    suffix = _validate_upload(file)

    if not _pipeline_lock.acquire(blocking=False):
        return _busy_response()
    tmp_path = None
    try:
        tmp_path = await _save_upload(file, suffix)
        logger.info(f"Transcribing: {file.filename} ({suffix}), prompt={len(initial_prompt)} chars")
        result, detected_language, diarized = await asyncio.to_thread(
            _run_pipeline,
            tmp_path,
            initial_prompt,
            language or None,
            diarize,
            min_speakers,
            max_speakers,
        )
        return build_transcribe_response(result, detected_language, diarized)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Transcription failed: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")
    finally:
        _pipeline_lock.release()
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


@app.post("/diarize", response_model=DiarizationResponse)
async def diarize(file: UploadFile = File(...)):
    """Run speaker diarization on an uploaded audio or video file.

    Accepts common audio formats (wav, mp3, m4a, flac) and video formats
    (mp4, mkv, mov, webm). Video files are automatically transcoded to audio.
    """
    suffix = _validate_upload(file)

    if not _pipeline_lock.acquire(blocking=False):
        return _busy_response()
    tmp_path = None
    try:
        tmp_path = await _save_upload(file, suffix)
        logger.info(f"Processing file: {file.filename} ({suffix})")
        result, _, _ = await asyncio.to_thread(_run_pipeline, tmp_path)
        return build_diarize_response(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Diarization failed: {e}")
        raise HTTPException(status_code=500, detail=f"Diarization failed: {e}")
    finally:
        _pipeline_lock.release()
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
