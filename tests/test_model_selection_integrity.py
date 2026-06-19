from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_process_job_reloads_config_before_running(monkeypatch):
    """process_job must reload config (picking up Settings changes) before phases run."""
    from api.services import worker as worker_mod
    from api.services.worker import JobWorker

    w = JobWorker.__new__(JobWorker)
    w.llm = MagicMock()
    w.llm.reload_config = MagicMock()
    w._current_job_id = None

    calls = []
    w.llm.reload_config.side_effect = lambda: calls.append("reload")

    # Short-circuit process_job right after the reload point by making
    # project-dir setup raise, and record ordering.
    def boom(_job):
        calls.append("setup")
        raise RuntimeError("stop here")

    monkeypatch.setattr(worker_mod, "start_run_tracking", lambda job_id: MagicMock())
    monkeypatch.setattr(worker_mod, "log_event", AsyncMock())
    monkeypatch.setattr(worker_mod, "update_job_status", AsyncMock())
    monkeypatch.setattr(worker_mod, "end_run_tracking", AsyncMock(return_value={"total_cost": 0}))
    monkeypatch.setattr(w, "_setup_project_dir", boom)
    monkeypatch.setattr(w, "_heartbeat_loop", AsyncMock())

    # process_job catches exceptions internally; we just let it complete.
    await w.process_job({"id": 1, "project_name": "X"})

    assert calls and calls[0] == "reload", f"reload must precede setup; got {calls}"


def test_resolve_config_path_uses_env(monkeypatch, tmp_path):
    from api.services.config_path import resolve_config_path

    target = tmp_path / "shared" / "llm-config.json"
    monkeypatch.setenv("LLM_CONFIG_PATH", str(target))
    # Seed default content so seeding logic has a source.
    monkeypatch.setattr("api.services.config_path.DEFAULT_CONFIG",
                        tmp_path / "default.json")
    (tmp_path / "default.json").write_text('{"primary_backend": "openrouter"}')

    resolved = resolve_config_path()
    assert resolved == target
    assert target.exists(), "missing target must be seeded from default"
    assert "primary_backend" in target.read_text()


def test_resolve_config_path_defaults_relative(monkeypatch):
    from api.services.config_path import resolve_config_path
    monkeypatch.delenv("LLM_CONFIG_PATH", raising=False)
    assert str(resolve_config_path()).endswith("config/llm-config.json")


@pytest.mark.asyncio
async def test_chunked_formatter_passes_model_override(monkeypatch, tmp_path):
    """Each chunk's chat() call must receive the model_override."""
    from types import SimpleNamespace

    from api.services import worker as worker_mod
    from api.services.chunking import TranscriptChunk
    from api.services.worker import JobWorker

    w = JobWorker.__new__(JobWorker)
    w.llm = MagicMock()
    w.llm.get_backend_for_phase = MagicMock(return_value="openrouter")
    w.llm.get_backend_config = MagicMock(return_value={"timeout": 120})

    seen_models = []

    async def fake_chat(**kwargs):
        seen_models.append(kwargs.get("model"))
        return SimpleNamespace(content="formatted", cost=0.01, total_tokens=10,
                               input_tokens=6, output_tokens=4,
                               model="anthropic/claude-sonnet-4.6")

    w.llm.chat = fake_chat
    monkeypatch.setattr(worker_mod, "log_event", AsyncMock())
    monkeypatch.setattr(w, "_load_agent_prompt", lambda phase: "system")

    chunks = [
        TranscriptChunk(index=0, content="a", start_timecode="00:00:00", end_timecode="00:00:05", word_count=1, overlap_prefix=""),
        TranscriptChunk(index=1, content="b", start_timecode="00:00:05", end_timecode="00:00:10", word_count=1, overlap_prefix="a"),
    ]

    await w._run_formatter_chunked(
        job_id=1, chunks=chunks, context={"analyst_output": ""},
        project_path=tmp_path, chunking_config={"max_parallel": 2},
        model_override="anthropic/claude-sonnet-4.6",
    )

    assert seen_models == ["anthropic/claude-sonnet-4.6", "anthropic/claude-sonnet-4.6"], seen_models
