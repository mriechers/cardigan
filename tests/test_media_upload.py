"""Tests for POST /api/upload/media (audio upload mode)."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)

INTAKE = {
    "project_name": "Here And Now Test",
    "speakers": ["Frederica Freyberg", "Josh Kaul"],
    "context_terms": ["Act 10"],
    "add_to_glossary": False,
    "language": "en",
}


@pytest.fixture
def media_dir(tmp_path, monkeypatch):
    from api.routers import upload

    monkeypatch.setattr(upload, "MEDIA_DIR", tmp_path)
    return tmp_path


def _post_media(filename: str, content: bytes = b"fake-audio-bytes", intake: dict = INTAKE):
    return client.post(
        "/api/upload/media",
        files={"file": (filename, content, "application/octet-stream")},
        data={"intake": json.dumps(intake)},
    )


class TestMediaUploadValidation:
    def test_rejects_unsupported_extension(self, media_dir):
        response = _post_media("notes.txt")
        assert response.status_code == 400
        assert "Unsupported media type" in response.json()["detail"]

    def test_rejects_invalid_intake_json(self, media_dir):
        response = client.post(
            "/api/upload/media",
            files={"file": ("audio.mp3", b"x", "application/octet-stream")},
            data={"intake": "{not json"},
        )
        assert response.status_code == 400
        assert "Invalid intake form" in response.json()["detail"]

    def test_rejects_missing_project_name(self, media_dir):
        bad = dict(INTAKE, project_name="")
        response = _post_media("audio.mp3", intake=bad)
        assert response.status_code == 400

    def test_oversize_upload_aborts_and_cleans_up(self, media_dir, monkeypatch):
        from api.routers import upload

        monkeypatch.setattr(upload, "MEDIA_MAX_UPLOAD_BYTES", 10)
        response = _post_media("audio.mp3", content=b"x" * 64)
        assert response.status_code == 413
        assert list(media_dir.iterdir()) == []


class TestAudioUpload:
    def test_audio_file_creates_media_job(self, media_dir):
        with patch(
            "api.routers.upload.media_service.get_duration_seconds",
            new=AsyncMock(return_value=125.0),
        ):
            response = _post_media("interview.mp3")

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["media_file"] == "interview.mp3"
        assert data["audio_extracted"] is False
        assert data["duration_seconds"] == 125.0
        assert (media_dir / "interview.mp3").read_bytes() == b"fake-audio-bytes"

        job = client.get(f"/api/jobs/{data['job_id']}").json()
        assert job["job_type"] == "media"
        assert job["media_file"] == "interview.mp3"
        assert job["transcript_file"] == ""
        assert job["intake"]["speakers"] == ["Frederica Freyberg", "Josh Kaul"]
        assert [p["name"] for p in job["phases"]] == [
            "transcription",
            "analyst",
            "formatter",
            "seo",
            "validator",
        ]
        assert job["duration_minutes"] == 2.1

    def test_duplicate_filename_gets_suffixed(self, media_dir):
        with patch(
            "api.routers.upload.media_service.get_duration_seconds",
            new=AsyncMock(return_value=None),
        ):
            first = _post_media("show.mp3")
            second = _post_media("show.mp3")

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["media_file"] == "show-2.mp3"


class TestVideoUpload:
    def test_video_triggers_extraction_and_deletes_original(self, media_dir):
        async def fake_extract(video_path, output_path):
            output_path.write_bytes(b"extracted-audio")
            return output_path

        with (
            patch(
                "api.routers.upload.media_service.extract_audio", new=AsyncMock(side_effect=fake_extract)
            ) as mock_extract,
            patch("api.routers.upload.media_service.get_duration_seconds", new=AsyncMock(return_value=60.0)),
        ):
            response = _post_media("episode.mp4")

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["audio_extracted"] is True
        assert data["media_file"] == "episode.m4a"
        mock_extract.assert_awaited_once()
        # Original video removed, extracted audio kept
        assert not (media_dir / "episode.mp4").exists()
        assert (media_dir / "episode.m4a").read_bytes() == b"extracted-audio"

    def test_extraction_failure_returns_422_and_cleans_up(self, media_dir):
        from api.services.media import AudioExtractionError

        with patch(
            "api.routers.upload.media_service.extract_audio",
            new=AsyncMock(side_effect=AudioExtractionError("ffmpeg failed (rc=1): bad stream")),
        ):
            response = _post_media("episode.mp4")

        assert response.status_code == 422
        assert "Audio extraction failed" in response.json()["detail"]
        assert list(media_dir.iterdir()) == []


class TestGlossaryOptIn:
    def test_opt_in_adds_speakers_and_terms(self, media_dir):
        intake = dict(INTAKE, add_to_glossary=True)
        with (
            patch("api.routers.upload.glossary.add_whisper_terms", return_value=3) as mock_add,
            patch("api.routers.upload.media_service.get_duration_seconds", new=AsyncMock(return_value=None)),
        ):
            response = _post_media("audio.mp3", intake=intake)

        assert response.status_code == 200
        assert response.json()["glossary_terms_added"] == 3
        mock_add.assert_called_once_with(["Frederica Freyberg", "Josh Kaul", "Act 10"])

    def test_opt_out_skips_glossary(self, media_dir):
        with (
            patch("api.routers.upload.glossary.add_whisper_terms") as mock_add,
            patch("api.routers.upload.media_service.get_duration_seconds", new=AsyncMock(return_value=None)),
        ):
            response = _post_media("audio2.mp3")

        assert response.status_code == 200
        mock_add.assert_not_called()
