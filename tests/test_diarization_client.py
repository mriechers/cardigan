"""Tests for the diarization HTTP client."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock, mock_open


@pytest.mark.asyncio
async def test_is_available_returns_true_when_healthy():
    """Client reports available when the service health check returns ready=True."""
    from api.services.diarization_client import DiarizationClient

    client = DiarizationClient(base_url="http://localhost:8000")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"status": "ok", "ready": True}

    with patch.object(client, "_client") as mock_client:
        mock_client.get = AsyncMock(return_value=mock_response)
        result = await client.is_available()

    assert result is True


@pytest.mark.asyncio
async def test_is_available_returns_false_when_not_ready():
    """Client reports unavailable when service is up but model not loaded."""
    from api.services.diarization_client import DiarizationClient

    client = DiarizationClient(base_url="http://localhost:8000")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"status": "ok", "ready": False}

    with patch.object(client, "_client") as mock_client:
        mock_client.get = AsyncMock(return_value=mock_response)
        result = await client.is_available()

    assert result is False


@pytest.mark.asyncio
async def test_is_available_returns_false_on_connection_error():
    """Client reports unavailable when service is unreachable."""
    import httpx
    from api.services.diarization_client import DiarizationClient

    client = DiarizationClient(base_url="http://localhost:8000")

    with patch.object(client, "_client") as mock_client:
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        result = await client.is_available()

    assert result is False


@pytest.mark.asyncio
async def test_diarize_returns_parsed_response():
    """Client parses a successful diarization response."""
    from api.services.diarization_client import DiarizationClient

    client = DiarizationClient(base_url="http://localhost:8000")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "duration_seconds": 1088.5,
        "speakers": ["Speaker 1", "Speaker 2"],
        "segments": [
            {"start": 0.0, "end": 4.2, "speaker": "Speaker 1", "confidence": 0.94},
            {"start": 4.2, "end": 11.8, "speaker": "Speaker 2", "confidence": 0.87},
        ],
    }

    with patch.object(client, "_client") as mock_client, \
         patch("api.services.diarization_client.Path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=b"")):
        mock_client.post = AsyncMock(return_value=mock_response)
        result = await client.diarize("/tmp/test.mp4")

    assert result is not None
    assert result["duration_seconds"] == 1088.5
    assert len(result["speakers"]) == 2
    assert len(result["segments"]) == 2


@pytest.mark.asyncio
async def test_diarize_returns_none_on_error():
    """Client returns None when diarization service returns an error."""
    from api.services.diarization_client import DiarizationClient

    client = DiarizationClient(base_url="http://localhost:8000")
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"

    with patch.object(client, "_client") as mock_client, \
         patch("api.services.diarization_client.Path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=b"")):
        mock_client.post = AsyncMock(return_value=mock_response)
        result = await client.diarize("/tmp/test.mp4")

    assert result is None


@pytest.mark.asyncio
async def test_diarize_returns_none_on_timeout():
    """Client returns None when diarization times out (graceful degradation)."""
    import httpx
    from api.services.diarization_client import DiarizationClient

    client = DiarizationClient(base_url="http://localhost:8000")

    with patch.object(client, "_client") as mock_client, \
         patch("api.services.diarization_client.Path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=b"")):
        mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("Timed out"))
        result = await client.diarize("/tmp/test.mp4")

    assert result is None
