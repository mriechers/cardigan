"""Tests for the diarization HTTP client."""

from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest


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

    with (
        patch.object(client, "_client") as mock_client,
        patch("api.services.diarization_client.Path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=b"")),
    ):
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

    with (
        patch.object(client, "_client") as mock_client,
        patch("api.services.diarization_client.Path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=b"")),
    ):
        mock_client.post = AsyncMock(return_value=mock_response)
        result = await client.diarize("/tmp/test.mp4")

    assert result is None


@pytest.mark.asyncio
async def test_diarize_returns_none_on_timeout():
    """Client returns None when diarization times out (graceful degradation)."""
    import httpx

    from api.services.diarization_client import DiarizationClient

    client = DiarizationClient(base_url="http://localhost:8000")

    with (
        patch.object(client, "_client") as mock_client,
        patch("api.services.diarization_client.Path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=b"")),
    ):
        mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("Timed out"))
        result = await client.diarize("/tmp/test.mp4")

    assert result is None


# ---------------------------------------------------------------------------
# transcribe() — TranscribeOutcome discrimination (ok / busy / error)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transcribe_returns_ok_outcome():
    """200 response parses into an ok outcome with the result payload."""
    from api.services.diarization_client import DiarizationClient

    client = DiarizationClient(base_url="http://localhost:8000")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "language": "en",
        "duration_seconds": 62.0,
        "speakers": ["SPEAKER_00"],
        "diarized": True,
        "segments": [{"id": 0, "start": 0.0, "end": 4.2, "text": "Hello.", "speaker": "SPEAKER_00", "words": []}],
    }

    with (
        patch.object(client, "_client") as mock_client,
        patch("api.services.diarization_client.Path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=b"")),
    ):
        mock_client.post = AsyncMock(return_value=mock_response)
        outcome = await client.transcribe(
            "/tmp/test.m4a", initial_prompt="PBS Wisconsin.", min_speakers=1, max_speakers=2
        )

    assert outcome.status == "ok"
    assert outcome.result["language"] == "en"
    assert len(outcome.result["segments"]) == 1
    # Form fields forwarded
    _, kwargs = mock_client.post.call_args
    assert kwargs["data"]["initial_prompt"] == "PBS Wisconsin."
    assert kwargs["data"]["min_speakers"] == "1"
    assert kwargs["data"]["max_speakers"] == "2"


@pytest.mark.asyncio
async def test_transcribe_busy_parses_retry_after():
    """503 with Retry-After maps to a busy outcome carrying the delay."""
    from api.services.diarization_client import DiarizationClient

    client = DiarizationClient(base_url="http://localhost:8000")
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.headers = {"Retry-After": "300"}
    mock_response.text = '{"detail": "transcription busy"}'

    with (
        patch.object(client, "_client") as mock_client,
        patch("api.services.diarization_client.Path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=b"")),
    ):
        mock_client.post = AsyncMock(return_value=mock_response)
        outcome = await client.transcribe("/tmp/test.m4a")

    assert outcome.status == "busy"
    assert outcome.retry_after_s == 300


@pytest.mark.asyncio
async def test_transcribe_busy_defaults_retry_after_when_header_missing():
    """503 without a parseable Retry-After falls back to the default delay."""
    from api.services.diarization_client import (
        DEFAULT_BUSY_RETRY_AFTER_SECONDS,
        DiarizationClient,
    )

    client = DiarizationClient(base_url="http://localhost:8000")
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.headers = {}
    mock_response.text = "busy"

    with (
        patch.object(client, "_client") as mock_client,
        patch("api.services.diarization_client.Path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=b"")),
    ):
        mock_client.post = AsyncMock(return_value=mock_response)
        outcome = await client.transcribe("/tmp/test.m4a")

    assert outcome.status == "busy"
    assert outcome.retry_after_s == DEFAULT_BUSY_RETRY_AFTER_SECONDS


@pytest.mark.asyncio
async def test_transcribe_500_is_error_outcome():
    """5xx (non-503) responses map to error, not busy."""
    from api.services.diarization_client import DiarizationClient

    client = DiarizationClient(base_url="http://localhost:8000")
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Transcription failed: boom"

    with (
        patch.object(client, "_client") as mock_client,
        patch("api.services.diarization_client.Path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=b"")),
    ):
        mock_client.post = AsyncMock(return_value=mock_response)
        outcome = await client.transcribe("/tmp/test.m4a")

    assert outcome.status == "error"
    assert "500" in outcome.detail


@pytest.mark.asyncio
async def test_transcribe_timeout_is_error_outcome():
    """Timeouts map to error with a descriptive detail (never raises)."""
    import httpx

    from api.services.diarization_client import DiarizationClient

    client = DiarizationClient(base_url="http://localhost:8000")

    with (
        patch.object(client, "_client") as mock_client,
        patch("api.services.diarization_client.Path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=b"")),
    ):
        mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("Timed out"))
        outcome = await client.transcribe("/tmp/test.m4a")

    assert outcome.status == "error"
    assert "timed out" in outcome.detail.lower()


@pytest.mark.asyncio
async def test_transcribe_missing_file_is_error_outcome():
    """A nonexistent media path short-circuits to error without a request."""
    from api.services.diarization_client import DiarizationClient

    client = DiarizationClient(base_url="http://localhost:8000")
    outcome = await client.transcribe("/nonexistent/audio.m4a")

    assert outcome.status == "error"
    assert "not found" in outcome.detail
