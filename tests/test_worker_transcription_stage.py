"""Tests for JobWorker._run_transcription_stage (audio upload mode)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.models.job import JobStatus
from api.services.diarization_client import TranscribeOutcome

OK_RESULT = {
    "language": "en",
    "duration_seconds": 120.0,
    "speakers": ["SPEAKER_00", "SPEAKER_01"],
    "diarized": True,
    "segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 4.0,
            "text": "Tonight on Here and Now.",
            "speaker": "SPEAKER_00",
            "words": [],
        },
        {
            "id": 1,
            "start": 4.5,
            "end": 9.0,
            "text": "Thanks for having me.",
            "speaker": "SPEAKER_01",
            "words": [],
        },
    ],
}


def _make_worker():
    from api.services.worker import JobWorker

    worker = JobWorker.__new__(JobWorker)
    worker.llm = MagicMock()
    worker.llm.config = {}
    return worker


def _make_job(media_file="test_audio.m4a"):
    return {
        "id": 42,
        "job_type": "media",
        "media_file": media_file,
        "transcript_file": "",
        "intake": {
            "speakers": ["Frederica Freyberg", "Josh Kaul"],
            "context_terms": ["Act 10"],
            "language": "en",
        },
    }


def _phases():
    return [
        {"name": "transcription", "status": "pending"},
        {"name": "analyst", "status": "pending"},
        {"name": "formatter", "status": "pending"},
        {"name": "seo", "status": "pending"},
        {"name": "validator", "status": "pending"},
    ]


@pytest.fixture
def stage_env(tmp_path, monkeypatch):
    """Patch worker module collaborators; yields a namespace of mocks."""
    import api.services.worker as worker_module

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "test_audio.m4a").write_bytes(b"audio")
    project_path = tmp_path / "OUTPUT" / "project"
    project_path.mkdir(parents=True)

    monkeypatch.setattr(worker_module, "MEDIA_DIR", media_dir)

    mocks = MagicMock()
    mocks.media_dir = media_dir
    mocks.project_path = project_path
    mocks.defer_job = AsyncMock()
    mocks.update_job_phase = AsyncMock()
    mocks.update_job_status = AsyncMock()
    mocks.update_job = AsyncMock()
    mocks.log_event = AsyncMock()
    monkeypatch.setattr(worker_module, "defer_job", mocks.defer_job)
    monkeypatch.setattr(worker_module, "update_job_phase", mocks.update_job_phase)
    monkeypatch.setattr(worker_module, "update_job_status", mocks.update_job_status)
    monkeypatch.setattr(worker_module.glossary_service, "get_whisper_terms", lambda: ["Waukesha"])

    mocks.client = MagicMock()
    mocks.client.is_available = AsyncMock(return_value=True)
    mocks.client.transcribe = AsyncMock(return_value=TranscribeOutcome(status="ok", result=OK_RESULT))
    mocks.client.close = AsyncMock()

    with (
        patch("api.services.diarization_client.DiarizationClient", return_value=mocks.client),
        patch("api.services.database.update_job", mocks.update_job),
        patch("api.models.job.JobUpdate"),
    ):
        # update_job is imported inside the method from the worker module's
        # top-level import — patch that reference too.
        monkeypatch.setattr(worker_module, "update_job", mocks.update_job)
        monkeypatch.setattr(worker_module, "log_event", mocks.log_event)
        yield mocks


@pytest.mark.asyncio
async def test_defers_when_service_unavailable(stage_env):
    stage_env.client.is_available = AsyncMock(return_value=False)
    worker = _make_worker()

    await worker._run_transcription_stage(_make_job(), stage_env.project_path, _phases())

    stage_env.defer_job.assert_awaited_once()
    assert stage_env.defer_job.call_args.kwargs["detail"] == "diarization service unavailable"
    stage_env.client.transcribe.assert_not_awaited()
    stage_env.update_job_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_defers_when_busy_with_retry_after(stage_env):
    stage_env.client.transcribe = AsyncMock(
        return_value=TranscribeOutcome(status="busy", retry_after_s=300, detail="transcription busy")
    )
    worker = _make_worker()
    phases = _phases()

    await worker._run_transcription_stage(_make_job(), stage_env.project_path, phases)

    stage_env.defer_job.assert_awaited_once()
    assert stage_env.defer_job.call_args.kwargs["retry_after_s"] == 300
    # Phase returned to pending so the next claim retries it
    assert phases[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_error_fails_phase_and_job(stage_env):
    stage_env.client.transcribe = AsyncMock(
        return_value=TranscribeOutcome(status="error", detail="Service returned 500: boom")
    )
    worker = _make_worker()
    phases = _phases()

    await worker._run_transcription_stage(_make_job(), stage_env.project_path, phases)

    assert phases[0]["status"] == "failed"
    assert "boom" in phases[0]["error_message"]
    status_call = stage_env.update_job_status.call_args_list[-1]
    assert status_call.args[1] == JobStatus.failed
    stage_env.defer_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_media_file_fails_job(stage_env):
    worker = _make_worker()
    phases = _phases()

    await worker._run_transcription_stage(_make_job("nope.m4a"), stage_env.project_path, phases)

    assert phases[0]["status"] == "failed"
    status_call = stage_env.update_job_status.call_args_list[-1]
    assert status_call.args[1] == JobStatus.failed
    assert "not found" in status_call.kwargs["error_message"]


@pytest.mark.asyncio
async def test_success_writes_artifacts_and_awaits_review(stage_env):
    worker = _make_worker()
    phases = _phases()

    await worker._run_transcription_stage(_make_job(), stage_env.project_path, phases)

    raw = json.loads((stage_env.project_path / "transcription_raw.json").read_text())
    assert raw == OK_RESULT

    edited = json.loads((stage_env.project_path / "transcription_edited.json").read_text())
    assert len(edited["segments"]) == 2
    assert edited["segments"][0]["text"] == "Tonight on Here and Now."
    # Speaker map pre-filled from intake order
    assert edited["speaker_map"] == {"SPEAKER_00": "Frederica Freyberg", "SPEAKER_01": "Josh Kaul"}
    assert edited["diarized"] is True

    assert phases[0]["status"] == "completed"
    assert phases[0]["metadata"]["segments"] == 2
    assert "Frederica Freyberg" in phases[0]["metadata"]["initial_prompt"]
    assert "Waukesha" in phases[0]["metadata"]["initial_prompt"]  # glossary terms included

    final_status = stage_env.update_job_status.call_args_list[-1]
    assert final_status.args[1] == JobStatus.awaiting_review

    # Transcribe called with speaker-count diarization bounds
    kwargs = stage_env.client.transcribe.call_args.kwargs
    assert kwargs["min_speakers"] == 2
    assert kwargs["max_speakers"] == 3


@pytest.mark.asyncio
async def test_undiarized_result_gets_single_speaker_bucket(stage_env):
    undiarized = {
        "language": "en",
        "duration_seconds": 30.0,
        "speakers": [],
        "diarized": False,
        "segments": [
            {"id": 0, "start": 0.0, "end": 3.0, "text": "Hello there.", "speaker": None, "words": []},
        ],
    }
    stage_env.client.transcribe = AsyncMock(return_value=TranscribeOutcome(status="ok", result=undiarized))
    worker = _make_worker()

    await worker._run_transcription_stage(_make_job(), stage_env.project_path, _phases())

    edited = json.loads((stage_env.project_path / "transcription_edited.json").read_text())
    assert edited["segments"][0]["speaker"] == "SPEAKER_00"
    assert edited["speaker_map"] == {"SPEAKER_00": "Frederica Freyberg"}
    assert edited["diarized"] is False


@pytest.mark.asyncio
async def test_media_path_traversal_fails_job(stage_env, tmp_path):
    # media_file is PATCHable; a path escaping MEDIA_DIR must fail the job
    # without ever reaching the transcription service
    secret = tmp_path / "secret.txt"
    secret.write_text("confidential")
    worker = _make_worker()
    phases = _phases()

    await worker._run_transcription_stage(_make_job(str(secret)), stage_env.project_path, phases)

    assert phases[0]["status"] == "failed"
    stage_env.client.transcribe.assert_not_awaited()
    status_call = stage_env.update_job_status.call_args_list[-1]
    assert status_call.args[1] == JobStatus.failed
