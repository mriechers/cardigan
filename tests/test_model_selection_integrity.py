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
