"""Tests for the ingest scan scheduler (api/services/ingest_scheduler.py)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from apscheduler.triggers.interval import IntervalTrigger


@pytest.mark.asyncio
async def test_configure_scheduler_uses_interval_from_config(monkeypatch):
    """#211: the ingest_scan job must fire every scan_interval_hours, not once
    daily. The scheduler previously built a daily CronTrigger from scan_time and
    silently ignored scan_interval_hours."""
    import api.services.ingest_scheduler as sched_mod

    # Fresh scheduler instance for test isolation.
    sched_mod._scheduler = None

    async def fake_config():
        return SimpleNamespace(enabled=True, scan_interval_hours=3, scan_time="07:00")

    monkeypatch.setattr(sched_mod, "get_ingest_config", fake_config)

    await sched_mod.configure_scheduler()

    job = sched_mod.get_scheduler().get_job("ingest_scan")
    assert job is not None, "ingest_scan job should be registered"
    assert isinstance(job.trigger, IntervalTrigger), f"expected IntervalTrigger, got {type(job.trigger)}"
    assert job.trigger.interval == timedelta(hours=3)


@pytest.mark.asyncio
async def test_configure_scheduler_skips_when_disabled(monkeypatch):
    """When ingest scanning is disabled, no job is scheduled."""
    import api.services.ingest_scheduler as sched_mod

    sched_mod._scheduler = None

    async def fake_config():
        return SimpleNamespace(enabled=False, scan_interval_hours=2, scan_time="07:00")

    monkeypatch.setattr(sched_mod, "get_ingest_config", fake_config)

    await sched_mod.configure_scheduler()

    assert sched_mod.get_scheduler().get_job("ingest_scan") is None
