"""Tests for diarization integration in the worker pipeline."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestFindMediaFile:
    """Tests for TranscriptWorker._find_media_file()."""

    def _make_worker(self):
        """Create a JobWorker with mocked dependencies."""
        from api.services.worker import JobWorker
        return JobWorker.__new__(JobWorker)

    def test_finds_mp4_matching_media_id(self, tmp_path):
        """Finds an mp4 file in the transcripts directory matching the media ID."""
        worker = self._make_worker()
        media_file = tmp_path / "2WLI1209HD.mp4"
        media_file.write_bytes(b"fake video data")

        job = {"media_id": "2WLI1209HD", "transcript_file": "2WLI1209HD.srt"}

        result = worker._find_media_file(job, search_dirs=[tmp_path])
        assert result is not None
        assert result.name == "2WLI1209HD.mp4"

    def test_finds_wav_matching_media_id(self, tmp_path):
        """Finds a wav file matching the media ID."""
        worker = self._make_worker()
        media_file = tmp_path / "2WLI1209HD.wav"
        media_file.write_bytes(b"fake audio data")

        job = {"media_id": "2WLI1209HD", "transcript_file": "2WLI1209HD.srt"}

        result = worker._find_media_file(job, search_dirs=[tmp_path])
        assert result is not None
        assert result.name == "2WLI1209HD.wav"

    def test_returns_none_when_no_media_file(self, tmp_path):
        """Returns None when no media file matches the media ID."""
        worker = self._make_worker()
        # Only a transcript, no media
        (tmp_path / "2WLI1209HD.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")

        job = {"media_id": "2WLI1209HD", "transcript_file": "2WLI1209HD.srt"}

        result = worker._find_media_file(job, search_dirs=[tmp_path])
        assert result is None

    def test_returns_none_when_no_media_id(self, tmp_path):
        """Returns None when job has no media_id."""
        worker = self._make_worker()
        job = {"transcript_file": "unknown.txt"}

        result = worker._find_media_file(job, search_dirs=[tmp_path])
        assert result is None

    def test_prefers_mp4_over_wav(self, tmp_path):
        """When multiple media files exist, prefers video over audio."""
        worker = self._make_worker()
        (tmp_path / "2WLI1209HD.mp4").write_bytes(b"video")
        (tmp_path / "2WLI1209HD.wav").write_bytes(b"audio")

        job = {"media_id": "2WLI1209HD", "transcript_file": "2WLI1209HD.srt"}

        result = worker._find_media_file(job, search_dirs=[tmp_path])
        assert result is not None
        # mp4 is preferred (first in MEDIA_EXTENSIONS list)
        assert result.suffix == ".mp4"
