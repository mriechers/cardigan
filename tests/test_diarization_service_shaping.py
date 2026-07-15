"""Tests for the diarization service's pure response-shaping helpers.

Loads diarization/app.py by path (it's a standalone service, not a package).
whisperx is imported lazily inside handlers, so module import is safe here.
"""

import importlib.util
from pathlib import Path

import pytest

APP_PATH = Path(__file__).parent.parent / "diarization" / "app.py"


@pytest.fixture(scope="module")
def diarization_app():
    spec = importlib.util.spec_from_file_location("diarization_app", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CANNED_ALIGNED_RESULT = {
    "segments": [
        {
            "start": 0.031,
            "end": 4.219,
            "text": " Tonight on Here and Now, we talk with the attorney general.",
            "speaker": "SPEAKER_00",
            "words": [
                {"word": "Tonight", "start": 0.031, "end": 0.451, "score": 0.95, "speaker": "SPEAKER_00"},
                {"word": "on", "start": 0.471, "end": 0.551, "score": 0.99, "speaker": "SPEAKER_00"},
            ],
        },
        {
            "start": 4.5,
            "end": 9.87,
            "text": " Thanks for having me, Frederica.",
            "speaker": "SPEAKER_01",
            "words": [
                {"word": "Thanks", "start": 4.5, "end": 4.9, "score": 0.9, "speaker": "SPEAKER_01"},
            ],
        },
        {"start": 10.0, "end": 10.5, "text": "   ", "speaker": "SPEAKER_01", "words": []},
    ]
}


class TestBuildTranscribeResponse:
    def test_shapes_segments_and_speakers(self, diarization_app):
        resp = diarization_app.build_transcribe_response(CANNED_ALIGNED_RESULT, "en", diarized=True)
        assert resp.language == "en"
        assert resp.diarized is True
        assert resp.speakers == ["SPEAKER_00", "SPEAKER_01"]
        assert len(resp.segments) == 2  # whitespace-only segment dropped
        first = resp.segments[0]
        assert first.id == 0
        assert first.text == "Tonight on Here and Now, we talk with the attorney general."
        assert first.speaker == "SPEAKER_00"
        assert first.words[0].word == "Tonight"
        assert resp.duration_seconds == 9.9

    def test_undiarized_result_has_no_speakers(self, diarization_app):
        undiarized = {
            "segments": [
                {"start": 0.0, "end": 2.0, "text": " Hello there.", "words": []},
            ]
        }
        resp = diarization_app.build_transcribe_response(undiarized, "en", diarized=False)
        assert resp.diarized is False
        assert resp.speakers == []
        assert resp.segments[0].speaker is None

    def test_empty_result(self, diarization_app):
        resp = diarization_app.build_transcribe_response({"segments": []}, "en", diarized=False)
        assert resp.segments == []
        assert resp.duration_seconds == 0.0


class TestBuildDiarizeResponse:
    def test_legacy_shape_preserved(self, diarization_app):
        resp = diarization_app.build_diarize_response(CANNED_ALIGNED_RESULT)
        assert resp.speakers == ["SPEAKER_00", "SPEAKER_01"]
        # Legacy endpoint keeps every segment (no text filtering) and rounds to 2dp
        assert len(resp.segments) == 3
        assert resp.segments[0].start == 0.03
        assert resp.segments[0].confidence == 0.97  # mean of 0.95, 0.99
        assert resp.segments[2].speaker == "SPEAKER_01"
