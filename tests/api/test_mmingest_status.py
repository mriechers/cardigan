"""Tests for GET /api/mmingest/status (crawler health endpoint).

Verifies the endpoint reports live index counts and the last crawl run, and
that it returns 200 with zeroed/null fields on a freshly-migrated (empty) DB
rather than 500ing.  Uses the same TestClient-over-migrated-SQLite harness as
test_mmingest_router.py.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest_asyncio.fixture
async def migrated_engine():
    fd, db_path = tempfile.mkstemp(suffix="_mmingest_status_test.db")
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
    yield engine, db_path

    await engine.dispose()
    try:
        os.unlink(db_path)
    except Exception:
        pass


def _make_client(db_path: str) -> TestClient:
    os.environ["DATABASE_PATH"] = db_path

    import api.routers.mmingest

    importlib.reload(api.routers.mmingest)

    import api.main

    importlib.reload(api.main)

    return TestClient(api.main.app, raise_server_exceptions=True)


@pytest.mark.asyncio
async def test_status_empty_db_returns_200_with_zeros(migrated_engine):
    """A migrated-but-empty DB returns 200, null last_run, zeroed counts."""
    engine, db_path = migrated_engine
    client = _make_client(db_path)

    resp = client.get("/api/mmingest/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["last_run"] is None
    assert body["running"] is False
    assert body["counts"] == {"files": 0, "current_files": 0, "sidecars": 0}


@pytest.mark.asyncio
async def test_status_reports_counts_and_last_run(migrated_engine):
    """With data + a recorded run, /status reports both."""
    engine, db_path = migrated_engine

    async with engine.begin() as conn:
        # Two files, one superseded.
        await conn.execute(text("""
            INSERT INTO mmingest_files (remote_url, filename, file_type, media_id, prefix, first_seen_at, superseded_by)
            VALUES ('u1', '6POL0101.mp4', 'mp4', '6POL0101', '6POL', '2026-03-19T10:00:00', NULL)
        """))
        await conn.execute(text("""
            INSERT INTO mmingest_files (remote_url, filename, file_type, media_id, prefix, first_seen_at, superseded_by)
            VALUES ('u2', '6POL0101_old.mp4', 'mp4', '6POL0101', '6POL', '2026-03-19T10:00:00', 1)
        """))
        await conn.execute(text("""
            INSERT INTO mmingest_sidecars (file_id, kind, body_text)
            VALUES (1, 'srt', 'hello world')
        """))
        await conn.execute(text("""
            INSERT INTO mmingest_crawl_runs
                (started_at, finished_at, status, files_seen, files_new, sidecars_persisted, fts_parity_delta, elapsed_seconds)
            VALUES ('2026-06-29T17:00:00', '2026-06-29T17:01:00', 'completed', 2, 2, 1, 0, 42.0)
        """))

    client = _make_client(db_path)
    resp = client.get("/api/mmingest/status")
    assert resp.status_code == 200
    body = resp.json()

    assert body["counts"] == {"files": 2, "current_files": 1, "sidecars": 1}
    assert body["running"] is False
    assert body["last_run"]["status"] == "completed"
    assert body["last_run"]["files_seen"] == 2
    assert body["last_run"]["sidecars_persisted"] == 1
    assert body["last_run"]["fts_parity_delta"] == 0


@pytest.mark.asyncio
async def test_status_running_flag_for_in_flight_pass(migrated_engine):
    """A 'running' row with NULL finished_at sets running=true."""
    engine, db_path = migrated_engine

    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO mmingest_crawl_runs (started_at, status)
            VALUES ('2026-06-29T17:00:00', 'running')
        """))

    client = _make_client(db_path)
    resp = client.get("/api/mmingest/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is True
    assert body["last_run"]["status"] == "running"
    assert body["last_run"]["finished_at"] is None
