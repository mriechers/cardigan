"""Async HTTP client for the Cardigan diarization microservice.

The diarization service is optional — if it's unavailable, the pipeline
runs without speaker verification. ``diarize()`` returns None/False rather
than raising on service errors; ``transcribe()`` returns a TranscribeOutcome
because its caller must distinguish busy (defer the job) from broken (fail
the transcription phase).
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Optional

import httpx

logger = logging.getLogger(__name__)

# Default URL matches the Docker Compose service name
DEFAULT_BASE_URL = os.environ.get("DIARIZATION_SERVICE_URL", "http://diarization:8000")

# Generous timeout: diarization of a 30-min episode on CPU can take several minutes
DIARIZE_TIMEOUT_SECONDS = int(os.environ.get("DIARIZATION_TIMEOUT", "600"))
# Full transcription of hour-long audio (transcribe + align + pyannote) can
# approach real-time on CPU.
TRANSCRIBE_TIMEOUT_SECONDS = int(os.environ.get("DIARIZATION_TRANSCRIBE_TIMEOUT", "3600"))
HEALTH_TIMEOUT_SECONDS = 5

DEFAULT_BUSY_RETRY_AFTER_SECONDS = 300


@dataclass
class TranscribeOutcome:
    """Result of a /transcribe call.

    status: 'ok'    — result holds the parsed TranscribeResponse dict
            'busy'  — service is single-flight and occupied; defer and retry
                      after retry_after_s
            'error' — request failed; detail explains why
    """

    status: Literal["ok", "busy", "error"]
    result: Optional[Dict[str, Any]] = None
    retry_after_s: Optional[int] = None
    detail: Optional[str] = None


class DiarizationClient:
    """Async client for the diarization microservice.

    Usage:
        client = DiarizationClient()
        if await client.is_available():
            result = await client.diarize("/path/to/audio.mp4")
            if result:
                print(result["speakers"])
    """

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(DIARIZE_TIMEOUT_SECONDS))

    async def is_available(self) -> bool:
        """Check if the diarization service is up and the model is loaded.

        Returns False (never raises) if the service is unreachable or not ready.
        """
        try:
            resp = await self._client.get(
                f"{self.base_url}/health",
                timeout=HEALTH_TIMEOUT_SECONDS,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("ready", False)
            return False
        except (httpx.HTTPError, Exception) as e:
            logger.debug(f"Diarization service unavailable: {e}")
            return False

    async def diarize(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Send an audio/video file to the diarization service.

        Args:
            file_path: Path to the audio or video file on disk.

        Returns:
            Parsed JSON response dict with keys: duration_seconds, speakers, segments.
            Returns None if the request fails for any reason.
        """
        path = Path(file_path)
        if not path.exists():
            logger.warning(f"Media file not found for diarization: {file_path}")
            return None

        try:
            logger.info(f"Sending file to diarization service: {path.name}")
            with open(path, "rb") as f:
                resp = await self._client.post(
                    f"{self.base_url}/diarize",
                    files={"file": (path.name, f, "application/octet-stream")},
                )

            if resp.status_code == 200:
                result = resp.json()
                logger.info(
                    f"Diarization complete: {len(result.get('speakers', []))} speakers, "
                    f"{len(result.get('segments', []))} segments"
                )
                return result

            logger.warning(f"Diarization service returned {resp.status_code}: {resp.text[:200]}")
            return None

        except httpx.TimeoutException:
            logger.warning(f"Diarization timed out after {DIARIZE_TIMEOUT_SECONDS}s for {path.name}")
            return None
        except (httpx.HTTPError, Exception) as e:
            logger.warning(f"Diarization request failed: {e}")
            return None

    async def transcribe(
        self,
        file_path: str,
        *,
        initial_prompt: str = "",
        language: str = "",
        diarize: bool = True,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
    ) -> TranscribeOutcome:
        """Send an audio file to the /transcribe endpoint.

        Args:
            file_path: Path to the audio file on disk.
            initial_prompt: Whisper initial_prompt (speaker names, glossary terms).
            language: ISO language hint; empty string lets Whisper detect.
            diarize: Request pyannote speaker labels when the service has them.
            min_speakers / max_speakers: Diarization bounds (from intake).

        Returns:
            TranscribeOutcome — never raises.
        """
        path = Path(file_path)
        if not path.exists():
            return TranscribeOutcome(status="error", detail=f"Media file not found: {file_path}")

        data: Dict[str, Any] = {
            "initial_prompt": initial_prompt,
            "language": language,
            "diarize": "true" if diarize else "false",
        }
        if min_speakers is not None:
            data["min_speakers"] = str(min_speakers)
        if max_speakers is not None:
            data["max_speakers"] = str(max_speakers)

        try:
            logger.info(f"Sending file to transcription service: {path.name}")
            with open(path, "rb") as f:
                resp = await self._client.post(
                    f"{self.base_url}/transcribe",
                    files={"file": (path.name, f, "application/octet-stream")},
                    data=data,
                    timeout=httpx.Timeout(TRANSCRIBE_TIMEOUT_SECONDS),
                )

            if resp.status_code == 200:
                result = resp.json()
                logger.info(
                    f"Transcription complete: {len(result.get('segments', []))} segments, "
                    f"{len(result.get('speakers', []))} speakers, diarized={result.get('diarized')}"
                )
                return TranscribeOutcome(status="ok", result=result)

            if resp.status_code == 503:
                try:
                    retry_after = int(resp.headers.get("Retry-After", ""))
                except ValueError:
                    retry_after = DEFAULT_BUSY_RETRY_AFTER_SECONDS
                logger.info(f"Transcription service busy; retry after {retry_after}s")
                return TranscribeOutcome(status="busy", retry_after_s=retry_after, detail=resp.text[:200])

            logger.warning(f"Transcription service returned {resp.status_code}: {resp.text[:200]}")
            return TranscribeOutcome(
                status="error", detail=f"Service returned {resp.status_code}: {resp.text[:200]}"
            )

        except httpx.TimeoutException:
            msg = f"Transcription timed out after {TRANSCRIBE_TIMEOUT_SECONDS}s for {path.name}"
            logger.warning(msg)
            return TranscribeOutcome(status="error", detail=msg)
        except (httpx.HTTPError, Exception) as e:
            logger.warning(f"Transcription request failed: {e}")
            return TranscribeOutcome(status="error", detail=str(e))

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()
