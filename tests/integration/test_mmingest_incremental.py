"""Integration tests for incremental (per-directory) persistence + telemetry.

These cover the fix for the empty-index-on-prod bug (2026-06-29): the indexer
now persists each directory's work items as the crawler discovers them, so an
interrupted walk still populates the DB.  Telemetry (mmingest_crawl_runs) is
exercised via the run_status helpers.

The shared migrated_engine fixture mirrors tests/integration/test_mmingest_index.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.services.mmingest.crawler import FileWorkItem
from api.services.mmingest.indexer import MmingestIndexer
from api.services.mmingest.run_status import read_status, record_run_finish, record_run_start


@pytest_asyncio.fixture
async def migrated_engine():
    """Stand up a fresh DB via `alembic upgrade head`, return an async engine."""
    fd, db_path = tempfile.mkstemp(suffix="_incremental_test.db")
    os.close(fd)

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
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


def _file(url: str, filename: str, media_id: str, *, file_type: str = "mp4") -> FileWorkItem:
    return FileWorkItem(
        url=url,
        directory_path="/IWP/",
        filename=filename,
        media_id=media_id,
        prefix="6POL",
        prefix_category="non-broadcast",
        show_name="Inside Wisconsin Politics",
        season=1,
        episode=1,
        hd=None,
        revision_date=None,
        variant_tag=None,
        unknown_tag=None,
        file_type=file_type,
        remote_modified_at=None,
        file_size_bytes=1000,
        change_triple=(None, None, 1000),
        lane="primary",
    )


async def _count_files(engine) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(text("SELECT COUNT(*) FROM mmingest_files"))).scalar_one()


# ---------------------------------------------------------------------------
# Incremental persistence: interrupted walk still leaves earlier dirs in the DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_walk_persists_completed_directories(migrated_engine):
    """If the walk streams one directory then crashes, that directory persists.

    A crawler double invokes on_directory for the first directory's items, then
    raises (simulating a container restart / network death mid-walk).  run_once
    propagates the error, but the streamed rows must already be committed.
    """
    dir1 = [
        _file("https://mmingest.pbswi.wisc.edu/IWP/6POL0101.mp4", "6POL0101.mp4", "6POL0101"),
        _file("https://mmingest.pbswi.wisc.edu/IWP/6POL0102.mp4", "6POL0102.mp4", "6POL0102"),
    ]

    async def fake_delta_walk(*, directories, known, on_directory):  # noqa: ARG001
        # Stream the first directory's items (these should persist) ...
        await on_directory(dir1)
        # ... then die before the walk completes.
        raise RuntimeError("simulated mid-walk crash")

    mock_crawler = AsyncMock()
    mock_crawler.delta_walk = AsyncMock(side_effect=fake_delta_walk)

    with patch("api.services.mmingest.indexer.MmingestCrawler", return_value=mock_crawler):
        indexer = MmingestIndexer(engine=migrated_engine)
        with pytest.raises(RuntimeError, match="simulated mid-walk crash"):
            await indexer.run_once()

    # The two streamed rows survived the crash — this is the whole point of the fix.
    assert await _count_files(migrated_engine) == 2


@pytest.mark.asyncio
async def test_streaming_batches_are_not_double_counted(migrated_engine):
    """Items streamed via on_directory are not re-persisted by the final sweep.

    The crawler streams two directories, then returns the full flat list (as the
    real crawler does).  run_once's residual sweep must skip already-persisted
    URLs, so sidecar/file counts reflect each item exactly once.
    """
    dir1 = [_file("https://mmingest.pbswi.wisc.edu/A/6POL0101.mp4", "6POL0101.mp4", "6POL0101")]
    dir2 = [_file("https://mmingest.pbswi.wisc.edu/B/6POL0202.mp4", "6POL0202.mp4", "6POL0202")]

    async def fake_delta_walk(*, directories, known, on_directory):  # noqa: ARG001
        await on_directory(dir1)
        await on_directory(dir2)
        return dir1 + dir2  # full list returned, exactly like the real crawler

    mock_crawler = AsyncMock()
    mock_crawler.delta_walk = AsyncMock(side_effect=fake_delta_walk)

    with patch("api.services.mmingest.indexer.MmingestCrawler", return_value=mock_crawler):
        indexer = MmingestIndexer(engine=migrated_engine)
        run = await indexer.run_once()

    assert run.files_seen == 2
    assert await _count_files(migrated_engine) == 2  # not 4 — no double persistence


# ---------------------------------------------------------------------------
# Telemetry: run_status roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_run_start_and_finish_roundtrip(migrated_engine):
    """record_run_start writes a 'running' row; record_run_finish completes it."""
    run_id = await record_run_start(migrated_engine)
    assert run_id is not None

    status = await read_status(migrated_engine)
    assert status is not None
    assert status["running"] is True
    assert status["last_run"]["status"] == "running"
    assert status["last_run"]["finished_at"] is None

    # Build a minimal IndexerRun-like object for counts.
    class _Run:
        files_seen = 7
        files_new = 7
        sidecars_fetched = 3
        sidecars_persisted = 3
        fts_parity_delta = 0
        elapsed_seconds = 12.5

    await record_run_finish(migrated_engine, run_id, status="completed", run=_Run())

    status2 = await read_status(migrated_engine)
    assert status2["running"] is False
    lr = status2["last_run"]
    assert lr["status"] == "completed"
    assert lr["files_seen"] == 7
    assert lr["sidecars_persisted"] == 3
    assert lr["fts_parity_delta"] == 0
    assert lr["finished_at"] is not None


@pytest.mark.asyncio
async def test_record_run_finish_failed_captures_error(migrated_engine):
    """A failed run records status='failed' and the error string."""
    run_id = await record_run_start(migrated_engine)
    await record_run_finish(migrated_engine, run_id, status="failed", error="boom: connection reset")

    status = await read_status(migrated_engine)
    assert status["running"] is False
    assert status["last_run"]["status"] == "failed"
    assert "boom" in status["last_run"]["error"]
