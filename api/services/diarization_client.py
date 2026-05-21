"""Async HTTP client for the Cardigan diarization microservice.

The diarization service is optional — if it's unavailable, the pipeline
runs without speaker verification. All methods return None/False rather
than raising on service errors.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# Default URL matches the Docker Compose service name
DEFAULT_BASE_URL = os.environ.get("DIARIZATION_SERVICE_URL", "http://diarization:8000")

# Generous timeout: diarization of a 30-min episode on CPU can take several minutes
DIARIZE_TIMEOUT_SECONDS = int(os.environ.get("DIARIZATION_TIMEOUT", "600"))
HEALTH_TIMEOUT_SECONDS = 5


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

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()
