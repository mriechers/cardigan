"""Diarization microservice — identifies speakers in audio/video files using WhisperX."""

import logging
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

logger = logging.getLogger("diarization")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Cardigan Diarization Service", version="0.1.0")

# WhisperX model — loaded once at startup
_model = None
_diarize_pipeline = None

# Configuration
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "base")
DEVICE = "cpu"
COMPUTE_TYPE = "int8"


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


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class TranscriptionResponse(BaseModel):
    duration_seconds: float
    language: str
    segments: list[TranscriptSegment]


@app.get("/health")
def health():
    """Health check. Returns ready=True only when the WhisperX model is loaded."""
    return {
        "status": "ok",
        "model_loaded": _model is not None,
        "ready": _model is not None,
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
        _diarize_pipeline = whisperx.DiarizationPipeline(
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


@app.post("/diarize", response_model=DiarizationResponse)
async def diarize(file: UploadFile = File(...)):
    """Run speaker diarization on an uploaded audio or video file.

    Accepts common audio formats (wav, mp3, m4a, flac) and video formats
    (mp4, mkv, mov, webm). Video files are automatically transcoded to audio.
    """
    import whisperx

    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    # Validate file extension
    suffix = Path(file.filename or "upload").suffix.lower()
    allowed = {".wav", ".mp3", ".m4a", ".flac", ".mp4", ".mkv", ".mov", ".webm"}
    if suffix not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {suffix}. Allowed: {sorted(allowed)}",
        )

    # Save upload to temp file (WhisperX needs a file path)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        logger.info(f"Processing file: {file.filename} ({suffix})")

        # Step 1: Transcribe with WhisperX
        audio = whisperx.load_audio(tmp_path)
        result = _model.transcribe(audio, batch_size=4)
        logger.info(f"Transcription complete: {len(result['segments'])} segments")

        # Step 2: Align timestamps
        align_model, align_metadata = whisperx.load_align_model(
            language_code=result["language"],
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

        # Step 3: Diarize speakers (if pipeline available)
        if _diarize_pipeline is not None:
            diarize_segments = _diarize_pipeline(audio)
            result = whisperx.assign_word_speakers(diarize_segments, result)
            logger.info("Speaker diarization complete")

        # Build response
        segments = []
        speakers_seen = set()
        duration_seconds = 0.0

        for seg in result["segments"]:
            speaker = seg.get("speaker", "Unknown")
            start = round(seg.get("start", 0.0), 2)
            end = round(seg.get("end", 0.0), 2)
            # WhisperX doesn't provide per-segment confidence for diarization;
            # use word-level confidence average if available, else 0.0
            words = seg.get("words", [])
            if words:
                confidences = [w.get("score", 0.0) for w in words if "score" in w]
                confidence = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
            else:
                confidence = 0.0

            segments.append(
                DiarizationSegment(
                    start=start,
                    end=end,
                    speaker=speaker,
                    confidence=confidence,
                )
            )
            speakers_seen.add(speaker)
            duration_seconds = max(duration_seconds, end)

        return DiarizationResponse(
            duration_seconds=round(duration_seconds, 1),
            speakers=sorted(speakers_seen),
            segments=segments,
        )

    except Exception as e:
        logger.exception(f"Diarization failed: {e}")
        raise HTTPException(status_code=500, detail=f"Diarization failed: {e}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe(file: UploadFile = File(...)):
    """Transcribe an uploaded audio/video file with WhisperX.

    Runs transcription + alignment (no speaker diarization) and returns
    aligned segments carrying their text — enough for the caller to build an
    SRT. This is the transcription counterpart to ``/diarize``, which drops the
    text and only returns speaker spans.
    """
    import whisperx

    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    suffix = Path(file.filename or "upload").suffix.lower()
    allowed = {".wav", ".mp3", ".m4a", ".flac", ".mp4", ".mkv", ".mov", ".webm"}
    if suffix not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {suffix}. Allowed: {sorted(allowed)}",
        )

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        logger.info(f"Transcribing file: {file.filename} ({suffix})")

        audio = whisperx.load_audio(tmp_path)
        result = _model.transcribe(audio, batch_size=4)
        language = result.get("language", "en")
        logger.info(f"Transcription complete: {len(result['segments'])} segments ({language})")

        align_model, align_metadata = whisperx.load_align_model(
            language_code=language,
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

        segments = []
        duration_seconds = 0.0
        for seg in result["segments"]:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            start = round(seg.get("start", 0.0), 3)
            end = round(seg.get("end", 0.0), 3)
            segments.append(TranscriptSegment(start=start, end=end, text=text))
            duration_seconds = max(duration_seconds, end)

        return TranscriptionResponse(
            duration_seconds=round(duration_seconds, 1),
            language=language,
            segments=segments,
        )

    except Exception as e:
        logger.exception(f"Transcription failed: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
