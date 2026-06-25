"""Backfill correctness: v2.1 rows land in live DB tagged 'v2.1' with new IDs."""

import os
import sqlite3
import tempfile

import pytest
import pytest_asyncio

from api.services import database as db_mod
from scripts.backfill_v21_data import backfill


def _make_v21_fixture(path: str) -> None:
    """Build a tiny v2.1-shaped source DB with overlapping IDs."""
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,
            project_path TEXT NOT NULL,
            transcript_file TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 0,
            queued_at DATETIME,
            actual_cost FLOAT DEFAULT 0.0,
            agent_phases TEXT DEFAULT '["analyst","formatter"]'
        );
        CREATE TABLE session_stats (
            id INTEGER PRIMARY KEY,
            job_id INTEGER,
            timestamp DATETIME,
            event_type TEXT NOT NULL,
            data TEXT
        );
        CREATE TABLE chat_sessions (
            id TEXT PRIMARY KEY,
            job_id INTEGER NOT NULL,
            project_name TEXT NOT NULL,
            created_at DATETIME,
            updated_at DATETIME,
            total_tokens INTEGER DEFAULT 0,
            total_cost FLOAT DEFAULT 0.0,
            message_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active'
        );
        INSERT INTO jobs (id, project_path, transcript_file, status, queued_at, actual_cost)
            VALUES (1, '/p/legacy-1', '/t/1.txt', 'completed', '2026-01-01 02:16:47.617491', 0.05),
                   (2, '/p/legacy-2', '/t/2.txt', 'completed', '2026-01-02', 0.07);
        INSERT INTO session_stats (id, job_id, timestamp, event_type, data)
            VALUES (10, 1, '2026-01-01 02:23:10.298219', 'phase_completed', '{"cost":0.05}'),
                   (11, 2, '2026-01-02 03:00:00', 'phase_completed', '{"cost":0.07}');
    """)
    c.commit()
    c.close()


@pytest_asyncio.fixture
async def live_and_source_dbs():
    orig_engine = db_mod._engine
    orig_factory = db_mod._async_session_factory
    db_mod._engine = None
    db_mod._async_session_factory = None

    with tempfile.TemporaryDirectory() as tmp:
        live = os.path.join(tmp, "live.db")
        src = os.path.join(tmp, "v21.db")
        os.environ["DATABASE_PATH"] = live
        await db_mod.init_db()
        # Insert one collision-id v4 job so we can prove translation works
        from api.models.job import JobCreate

        existing = await db_mod.create_job(
            JobCreate(project_name="current", project_path="/p/current", transcript_file="/t/current.txt")
        )
        assert existing.id == 1  # collision target

        _make_v21_fixture(src)
        yield {"live": live, "source": src, "existing_v4_id": existing.id}

        await db_mod.close_db()
        db_mod._engine = orig_engine
        db_mod._async_session_factory = orig_factory


@pytest.mark.asyncio
async def test_backfill_inserts_v21_jobs_with_new_ids(live_and_source_dbs):
    paths = live_and_source_dbs
    summary = await backfill(source_db=paths["source"], app_version="v2.1")

    assert summary["jobs_inserted"] == 2
    assert summary["session_stats_inserted"] == 2

    c = sqlite3.connect(paths["live"]).cursor()
    counts = dict(c.execute("SELECT app_version, COUNT(*) FROM jobs GROUP BY app_version"))
    # Two backfilled rows tagged v2.1, plus exactly one pre-existing job that keeps
    # its current-app version. Assert the invariant structurally rather than against a
    # hardcoded version string: the ambient version is "v4.2-test" in CI (not "v4.1"),
    # which made the old ("v4.1", 1) assertion a stable-but-broken full-sweep failure
    # (#200). Decoupled from the app_version symbol so #119's rename can't break it.
    assert counts.get("v2.1") == 2
    assert sum(n for v, n in counts.items() if v != "v2.1") == 1


@pytest.mark.asyncio
async def test_backfill_translates_session_stats_job_id(live_and_source_dbs):
    paths = live_and_source_dbs
    await backfill(source_db=paths["source"], app_version="v2.1")

    c = sqlite3.connect(paths["live"]).cursor()
    # Every v2.1 session_stats row's job_id must point at a v2.1 job, never the existing v4 job
    rows = list(c.execute("""
        SELECT s.job_id, j.app_version
        FROM session_stats s JOIN jobs j ON j.id = s.job_id
        WHERE s.app_version = 'v2.1'
    """))
    assert len(rows) == 2
    for job_id, ver in rows:
        assert ver == "v2.1"
        assert job_id != paths["existing_v4_id"]


@pytest.mark.asyncio
async def test_backfill_is_idempotent(live_and_source_dbs):
    """Running backfill twice should not duplicate data."""
    paths = live_and_source_dbs
    s1 = await backfill(source_db=paths["source"], app_version="v2.1")
    s2 = await backfill(source_db=paths["source"], app_version="v2.1")
    assert s1["jobs_inserted"] == 2
    assert s2["jobs_inserted"] == 0  # second run sees nothing new
    assert s2["skipped_duplicate_jobs"] == 2
    assert s2["session_stats_inserted"] == 0  # second run skips events too


@pytest.mark.asyncio
async def test_backfill_dry_run_writes_nothing(live_and_source_dbs):
    """--dry-run reports counts without modifying the live DB."""
    paths = live_and_source_dbs
    summary = await backfill(source_db=paths["source"], app_version="v2.1", dry_run=True)

    # Counts reported as if real
    assert summary["jobs_inserted"] == 2
    assert summary["session_stats_inserted"] == 2

    # But nothing actually written
    c = sqlite3.connect(paths["live"]).cursor()
    rows = list(c.execute("SELECT COUNT(*) FROM jobs WHERE app_version = 'v2.1'"))
    assert rows[0][0] == 0
    rows = list(c.execute("SELECT COUNT(*) FROM session_stats WHERE app_version = 'v2.1'"))
    assert rows[0][0] == 0
