"""Tests for the transcription review API (audio upload mode)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.models.job import JobCreate, JobStatus, JobUpdate
from api.services import database

client = TestClient(app)

RAW = {
    "language": "en",
    "duration_seconds": 9.0,
    "speakers": ["SPEAKER_00", "SPEAKER_01"],
    "diarized": True,
    "segments": [
        {"id": 0, "start": 0.0, "end": 4.0, "text": "Tonight on Here and Now with Frederica Fryberg.", "speaker": "SPEAKER_00", "words": []},
        {"id": 1, "start": 4.5, "end": 9.0, "text": "Thanks for having me, Frederica Fryberg.", "speaker": "SPEAKER_01", "words": []},
    ],
}


def _edited_from_raw():
    return {
        "segments": [
            {"id": s["id"], "start": s["start"], "end": s["end"], "speaker": s["speaker"], "text": s["text"].strip()}
            for s in RAW["segments"]
        ],
        "speaker_map": {"SPEAKER_00": "Frederica Freyberg", "SPEAKER_01": ""},
        "diarized": True,
        "language": "en",
        "duration_seconds": 9.0,
    }


@pytest.fixture
def media_job(tmp_path, monkeypatch):
    """A media job in awaiting_review with artifacts on disk."""
    from api.routers import transcription as transcription_router

    project = tmp_path / "OUTPUT" / "Review_Test"
    project.mkdir(parents=True)
    (project / "transcription_raw.json").write_text(json.dumps(RAW))
    (project / "transcription_edited.json").write_text(json.dumps(_edited_from_raw()))

    transcripts_dir = tmp_path / "transcripts"
    monkeypatch.setattr(transcription_router, "TRANSCRIPTS_DIR", transcripts_dir)
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "review_test.m4a").write_bytes(b"audio-bytes")
    monkeypatch.setattr(transcription_router, "MEDIA_DIR", media_dir)

    import asyncio

    async def make():
        job = await database.create_job(
            JobCreate(
                project_name="Review Test",
                transcript_file="",
                project_path=str(project),
                job_type="media",
                media_file="review_test.m4a",
                intake={"speakers": ["Frederica Freyberg"], "context_terms": [], "language": "en"},
            )
        )
        # Transcription phase completed, then the review gate
        phases = [p.model_dump() for p in job.phases]
        phases[0]["status"] = "completed"
        from api.models.job import JobPhase

        await database.update_job(
            job.id,
            JobUpdate(status=JobStatus.awaiting_review, phases=[JobPhase(**p) for p in phases]),
        )
        return job.id

    loop = asyncio.new_event_loop()
    job_id = loop.run_until_complete(make())
    loop.close()
    return {"job_id": job_id, "project": project, "transcripts_dir": transcripts_dir}


class TestGetTranscription:
    def test_returns_raw_edited_and_intake(self, media_job):
        response = client.get(f"/api/jobs/{media_job['job_id']}/transcription")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "awaiting_review"
        assert len(data["raw_segments"]) == 2
        assert len(data["edited"]["segments"]) == 2
        assert data["edited"]["speaker_map"]["SPEAKER_00"] == "Frederica Freyberg"
        assert data["diarized"] is True
        assert data["intake"]["speakers"] == ["Frederica Freyberg"]

    def test_404_when_artifacts_missing(self, media_job):
        (media_job["project"] / "transcription_edited.json").unlink()
        response = client.get(f"/api/jobs/{media_job['job_id']}/transcription")
        assert response.status_code == 404

    def test_400_for_non_media_job(self):
        import asyncio

        loop = asyncio.new_event_loop()
        job = loop.run_until_complete(
            database.create_job(JobCreate(project_name="Classic", transcript_file="c.txt"))
        )
        loop.close()
        response = client.get(f"/api/jobs/{job.id}/transcription")
        assert response.status_code == 400


class TestPutTranscription:
    def test_roundtrip(self, media_job):
        doc = _edited_from_raw()
        doc["segments"][0]["text"] = "Tonight on Here and Now with Frederica Freyberg."
        doc["speaker_map"]["SPEAKER_01"] = "Josh Kaul"
        body = {"segments": doc["segments"], "speaker_map": doc["speaker_map"]}

        put = client.put(f"/api/jobs/{media_job['job_id']}/transcription", json=body)
        assert put.status_code == 200, put.text

        get = client.get(f"/api/jobs/{media_job['job_id']}/transcription").json()
        assert get["edited"]["segments"][0]["text"].endswith("Frederica Freyberg.")
        assert get["edited"]["speaker_map"]["SPEAKER_01"] == "Josh Kaul"
        # Non-document fields survive the overwrite
        on_disk = json.loads((media_job["project"] / "transcription_edited.json").read_text())
        assert on_disk["diarized"] is True

    def test_blocked_outside_awaiting_review(self, media_job):
        import asyncio

        loop = asyncio.new_event_loop()
        loop.run_until_complete(database.update_job(media_job["job_id"], JobUpdate(status=JobStatus.pending)))
        loop.close()
        body = {"segments": _edited_from_raw()["segments"], "speaker_map": {}}
        response = client.put(f"/api/jobs/{media_job['job_id']}/transcription", json=body)
        assert response.status_code == 400


class TestApprove:
    def test_approve_builds_srt_resets_phases_requeues(self, media_job):
        # Editor fixes the misheard name in both segments and maps speaker 2
        doc = _edited_from_raw()
        for seg in doc["segments"]:
            seg["text"] = seg["text"].replace("Frederica Fryberg", "Frederica Freyberg")
        doc["speaker_map"]["SPEAKER_01"] = "Josh Kaul"
        client.put(
            f"/api/jobs/{media_job['job_id']}/transcription",
            json={"segments": doc["segments"], "speaker_map": doc["speaker_map"]},
        )

        response = client.post(f"/api/jobs/{media_job['job_id']}/transcription/approve", json={"update_glossary": False})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "pending"
        assert data["transcript_file"] == "Review_Test.srt"

        srt = (media_job["transcripts_dir"] / "Review_Test.srt").read_text()
        assert "Frederica Freyberg: Tonight on Here and Now" in srt
        assert "Josh Kaul: Thanks for having me" in srt
        assert "00:00:00,000 --> 00:00:04,000" in srt
        # Provenance copy in the project dir
        assert (media_job["project"] / "transcript_approved.srt").exists()

        job = client.get(f"/api/jobs/{media_job['job_id']}").json()
        assert job["status"] == "pending"
        assert job["transcript_file"] == "Review_Test.srt"
        phases = {p["name"]: p["status"] for p in job["phases"]}
        assert phases["transcription"] == "completed"
        assert phases["analyst"] == "pending"
        assert phases["validator"] == "pending"
        assert job["word_count"] > 0

    def test_approve_mines_glossary(self, media_job, tmp_path, monkeypatch):
        glossary_file = tmp_path / "knowledge" / "glossary.md"
        glossary_file.parent.mkdir()
        glossary_file.write_text(
            "# Glossary\n\n## Whisper Prompt Terms\n\nHeader prose.\n\n- PBS Wisconsin\n\n"
            "## Editor Corrections\n\n| Correct | Model Tendency | Context |\n|---|---|---|\n"
        )
        monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path / "knowledge"))

        doc = _edited_from_raw()
        for seg in doc["segments"]:
            seg["text"] = seg["text"].replace("Frederica Fryberg", "Frederica Freyberg")
        client.put(
            f"/api/jobs/{media_job['job_id']}/transcription",
            json={"segments": doc["segments"], "speaker_map": doc["speaker_map"]},
        )

        response = client.post(f"/api/jobs/{media_job['job_id']}/transcription/approve", json={})
        assert response.status_code == 200, response.text
        assert response.json()["corrections_added"] >= 1
        text = glossary_file.read_text()
        assert "| Freyberg | Fryberg | Transcript review correction |" in text

    def test_approve_blocked_outside_awaiting_review(self, media_job):
        import asyncio

        loop = asyncio.new_event_loop()
        loop.run_until_complete(database.update_job(media_job["job_id"], JobUpdate(status=JobStatus.failed)))
        loop.close()
        response = client.post(f"/api/jobs/{media_job['job_id']}/transcription/approve", json={})
        assert response.status_code == 400


class TestRetranscribe:
    def test_retranscribe_resets_and_merges_terms(self, media_job):
        response = client.post(
            f"/api/jobs/{media_job['job_id']}/transcription/retranscribe",
            json={"extra_terms": ["Oconomowoc", "oconomowoc", "Act 10"]},
        )
        assert response.status_code == 200, response.text
        job = response.json()
        assert job["status"] == "pending"
        assert job["intake"]["context_terms"] == ["Oconomowoc", "Act 10"]
        phases = {p["name"]: p["status"] for p in job["phases"]}
        assert phases["transcription"] == "pending"
        # Working copy discarded; raw provenance kept
        assert not (media_job["project"] / "transcription_edited.json").exists()
        assert (media_job["project"] / "transcription_raw.json").exists()


class TestMediaEndpoint:
    def test_serves_audio(self, media_job):
        response = client.get(f"/api/jobs/{media_job['job_id']}/media")
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/mp4"
        assert response.content == b"audio-bytes"

    def test_range_request(self, media_job):
        response = client.get(
            f"/api/jobs/{media_job['job_id']}/media", headers={"Range": "bytes=0-3"}
        )
        assert response.status_code == 206
        assert response.content == b"audi"
