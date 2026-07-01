"""Tests that run_delta_walk records crawl-run telemetry in every branch.

Locks the "no silently-empty index" guarantee: a completed pass records
status='completed' with counts, and a failing pass records status='failed'
with the error (and still re-logs via logger.exception).  Mirrors the migrated
SQLite harness used elsewhere; the DB engine singleton is patched so the
scheduler resolves a real, migrated engine.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

import api.services.database as _db_module
from api.services.mmingest.indexer import IndexerRun
from api.services.mmingest.run_status import read_status


@pytest_asyncio.fixture
async def migrated_engine():
    fd, db_path = tempfile.mkstemp(suffix="_scheduler_telemetry_test.db")
    os.close(fd)

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    env = {**os.environ, "DATABASE_PATH": db_path}

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    yield engine

    await engine.dispose()
    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_completed_run_records_telemetry(migrated_engine):
    """A successful run_delta_walk records status='completed' with counts."""
    from api.services.mmingest import scheduler

    fake_run = IndexerRun(files_seen=5, files_new=5, sidecars_persisted=2, fts_parity_delta=0, elapsed_seconds=3.0)
    mock_indexer = AsyncMock()
    mock_indexer.run_once = AsyncMock(return_value=fake_run)

    # run_delta_walk imports MmingestIndexer locally at call time, so patch the
    # source module where the name is resolved.
    with (
        patch.object(_db_module, "_engine", migrated_engine),
        patch("api.services.mmingest.indexer.MmingestIndexer", return_value=mock_indexer),
    ):
        await scheduler.run_delta_walk()

    status = await read_status(migrated_engine)
    assert status["running"] is False
    assert status["last_run"]["status"] == "completed"
    assert status["last_run"]["files_seen"] == 5
    assert status["last_run"]["sidecars_persisted"] == 2


@pytest.mark.asyncio
async def test_failed_run_records_failed_status(migrated_engine):
    """A run that raises records status='failed' with the error (not silent)."""
    from api.services.mmingest import scheduler

    mock_indexer = AsyncMock()
    mock_indexer.run_once = AsyncMock(side_effect=ValueError("crawl exploded"))

    with (
        patch.object(_db_module, "_engine", migrated_engine),
        patch("api.services.mmingest.indexer.MmingestIndexer", return_value=mock_indexer),
    ):
        # run_delta_walk catches the exception (logs + records); it does not re-raise.
        await scheduler.run_delta_walk()

    status = await read_status(migrated_engine)
    assert status["running"] is False
    assert status["last_run"]["status"] == "failed"
    assert "crawl exploded" in status["last_run"]["error"]
