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
HF_TOKEN = os.environ.get("HF_TOKEN", "")
DEVICE = "cpu"
COMPUTE_TYPE = "int8"


class DiarizationSegment(BaseModel):
    start: float
    end: float
    speaker: str
    confidence: float


class DiarizationResponse(BaseModel):
    duration_seconds: float
    speakers: list[str]
    segments: list[DiarizationSegment]


@app.get("/health")
def health():
    """Health check. Returns ready=True only when the WhisperX model is loaded."""
    return {
        "status": "ok",
        "model_loaded": _model is not None,
        "ready": _model is not None,
    }
