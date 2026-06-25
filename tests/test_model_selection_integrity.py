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
    monkeypatch.setattr("api.services.config_path.DEFAULT_CONFIG", tmp_path / "default.json")
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
        return SimpleNamespace(
            content="formatted",
            cost=0.01,
            total_tokens=10,
            input_tokens=6,
            output_tokens=4,
            model="anthropic/claude-sonnet-4.6",
        )

    w.llm.chat = fake_chat
    monkeypatch.setattr(worker_mod, "log_event", AsyncMock())
    monkeypatch.setattr(w, "_load_agent_prompt", lambda phase: "system")

    chunks = [
        TranscriptChunk(
            index=0, content="a", start_timecode="00:00:00", end_timecode="00:00:05", word_count=1, overlap_prefix=""
        ),
        TranscriptChunk(
            index=1, content="b", start_timecode="00:00:05", end_timecode="00:00:10", word_count=1, overlap_prefix="a"
        ),
    ]

    await w._run_formatter_chunked(
        job_id=1,
        chunks=chunks,
        context={"analyst_output": ""},
        project_path=tmp_path,
        chunking_config={"max_parallel": 2},
        model_override="anthropic/claude-sonnet-4.6",
    )

    assert seen_models == ["anthropic/claude-sonnet-4.6", "anthropic/claude-sonnet-4.6"], seen_models


@pytest.mark.asyncio
async def test_chunked_formatter_first_chunk_told_it_is_a_section(monkeypatch, tmp_path):
    """Chunk 0 of a multi-chunk run must be told it's 'section 1 of N'.

    Regression for the job-12 bug: without this, chunk 0 sees its slice end
    mid-transcript, concludes the transcript is truncated, and emits false
    'incomplete / needs_review' review notes that trip the validator.
    """
    from types import SimpleNamespace

    from api.services import worker as worker_mod
    from api.services.chunking import TranscriptChunk
    from api.services.worker import JobWorker

    w = JobWorker.__new__(JobWorker)
    w.llm = MagicMock()
    w.llm.get_backend_for_phase = MagicMock(return_value="openrouter")
    w.llm.get_backend_config = MagicMock(return_value={"timeout": 120})

    captured = []

    async def fake_chat(**kwargs):
        captured.append(next(m["content"] for m in kwargs["messages"] if m["role"] == "user"))
        return SimpleNamespace(
            content="formatted", cost=0.01, total_tokens=10, input_tokens=6, output_tokens=4, model="m"
        )

    w.llm.chat = fake_chat
    monkeypatch.setattr(worker_mod, "log_event", AsyncMock())
    monkeypatch.setattr(w, "_load_agent_prompt", lambda phase: "system")

    # --- multi-chunk: chunk 0 must carry the "section 1 of N" caveat ---
    chunks = [
        TranscriptChunk(index=0, content="a", start_timecode="0", end_timecode="1", word_count=1, overlap_prefix=""),
        TranscriptChunk(index=1, content="b", start_timecode="1", end_timecode="2", word_count=1, overlap_prefix="a"),
    ]
    await w._run_formatter_chunked(
        job_id=1,
        chunks=chunks,
        context={"analyst_output": ""},
        project_path=tmp_path,
        chunking_config={"max_parallel": 2},
    )
    # Identify chunk 0 by content: continuation chunks carry the "Continue formatting
    # from where the previous section left off" phrase; chunk 0 does not.
    chunk0_prompt = next(p for p in captured if "Continue formatting from where the previous" not in p)
    assert "section 1 of 2" in chunk0_prompt
    assert "do NOT assess overall transcript completeness" in chunk0_prompt
    assert "needs_review" in chunk0_prompt

    # --- single chunk: no caveat (it really is the whole transcript) ---
    captured.clear()
    solo = [
        TranscriptChunk(index=0, content="a", start_timecode="0", end_timecode="1", word_count=1, overlap_prefix=""),
    ]
    await w._run_formatter_chunked(
        job_id=2,
        chunks=solo,
        context={"analyst_output": ""},
        project_path=tmp_path,
        chunking_config={"max_parallel": 1},
    )
    assert len(captured) == 1
    assert "section 1 of" not in captured[0]


@pytest.mark.asyncio
async def test_chunked_formatter_records_real_model(monkeypatch, tmp_path):
    """The chunked formatter result must report the model that ran, not 'chunked (...)'."""
    from types import SimpleNamespace

    from api.services import worker as worker_mod
    from api.services.chunking import TranscriptChunk
    from api.services.worker import JobWorker

    w = JobWorker.__new__(JobWorker)
    w.llm = MagicMock()
    w.llm.get_backend_for_phase = MagicMock(return_value="openrouter")
    w.llm.get_backend_config = MagicMock(return_value={"timeout": 120})

    async def fake_chat(**kwargs):
        return SimpleNamespace(
            content="formatted",
            cost=0.01,
            total_tokens=10,
            input_tokens=6,
            output_tokens=4,
            model="anthropic/claude-sonnet-4.6",
        )

    w.llm.chat = fake_chat
    monkeypatch.setattr(worker_mod, "log_event", AsyncMock())
    monkeypatch.setattr(w, "_load_agent_prompt", lambda phase: "system")

    chunks = [
        TranscriptChunk(
            index=0, content="a", start_timecode="00:00:00", end_timecode="00:00:05", word_count=1, overlap_prefix=""
        )
    ]
    result = await w._run_formatter_chunked(
        job_id=1,
        chunks=chunks,
        context={"analyst_output": ""},
        project_path=tmp_path,
        chunking_config={"max_parallel": 1},
        model_override="anthropic/claude-sonnet-4.6",
    )
    assert "claude-sonnet-4.6" in result["model"]
    assert "chunked" not in result["model"].split()[0]  # real id, not the opaque string


def test_revalidation_updates_validator_model():
    """After re-validation, the validator entry in phases[] must reflect the model that judged."""
    # Pure helper test: the update logic lives in a small helper we add.
    from api.services.worker import apply_validator_model

    phases = [
        {"name": "formatter", "model": "anthropic/claude-sonnet-4.6"},
        {"name": "validator", "model": "anthropic/claude-4.5-haiku-20251001"},
    ]
    updated = apply_validator_model(phases, "anthropic/claude-sonnet-4.6")
    val = next(p for p in updated if p["name"] == "validator")
    assert val["model"] == "anthropic/claude-sonnet-4.6"
