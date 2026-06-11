"""Tests for the three mmingest MCP tools added in Sprint 4A.

Verification gates covered (per sprint-4a-handoff.md):
  Gate 1  — search_mmingest happy path: FTS5 hit with correct display fields
  Gate 2a — search_mmingest prefix filter narrows results
  Gate 2b — search_mmingest since filter by remote_modified_at
  Gate 2c — search_mmingest limit cap
  Gate 3  — search_mmingest returns same media_ids as HTTP /search
  Gate 4  — get_mmingest_asset primary-only happy path (Airtable mocked)
  Gate 5  — get_mmingest_asset with PLEDGE variant
  Gate 6  — get_mmingest_asset 404 case returns friendly error TextContent
  Gate 7  — get_mmingest_asset Airtable failure fallback (no exception)
  Gate 8a — list_recent_mmingest_assets since= filter
  Gate 8b — list_recent_mmingest_assets limit cap
  Gate 9  — list_recent_mmingest_assets returns same media_ids as HTTP /recent
  Gate 10 — search_mmingest empty-query guard
  Gate 11 — search_mmingest FTS5 syntax error returns friendly error
  Gate 12 — list_recent_mmingest_assets empty result returns friendly message

Uses an isolated migrated SQLite DB (same fixture pattern as
tests/api/test_mmingest_router.py).  AirtableClient is patched at the
os.environ level so no real Airtable calls are made.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# ---------------------------------------------------------------------------
# Shared fixture: isolated migrated DB engine
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def migrated_engine():
    """Stand up a fresh DB via `alembic upgrade head`, return (engine, db_path).

    Mirrors the pattern in tests/api/test_mmingest_router.py exactly.
    """
    fd, db_path = tempfile.mkstemp(suffix="_mmingest_mcp_test.db")
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


# ---------------------------------------------------------------------------
# DB seed helpers (mirrors test_mmingest_router.py)
# ---------------------------------------------------------------------------


async def _insert_file(
    conn,
    *,
    remote_url: str,
    filename: str,
    file_type: str = "srt",
    media_id: str = "6POL0101",
    prefix: str = "6POL",
    show_name: str = "Wisconsin Politics",
    season: str = "01",
    episode: str = "01",
    revision_date: str = "2026-03-19",
    variant_tag: str | None = None,
    superseded_by: int | None = None,
    remote_modified_at: str | None = None,
    first_seen_at: str | None = None,
    airtable_record_id: str | None = None,
) -> int:
    await conn.execute(
        text("""
            INSERT INTO mmingest_files
                (remote_url, filename, file_type, media_id, prefix, show_name,
                 season, episode, revision_date, variant_tag, superseded_by,
                 remote_modified_at, first_seen_at, airtable_record_id)
            VALUES
                (:remote_url, :filename, :file_type, :media_id, :prefix, :show_name,
                 :season, :episode, :revision_date, :variant_tag, :superseded_by,
                 :remote_modified_at, :first_seen_at, :airtable_record_id)
        """),
        {
            "remote_url": remote_url,
            "filename": filename,
            "file_type": file_type,
            "media_id": media_id,
            "prefix": prefix,
            "show_name": show_name,
            "season": season,
            "episode": episode,
            "revision_date": revision_date,
            "variant_tag": variant_tag,
            "superseded_by": superseded_by,
            "remote_modified_at": remote_modified_at or "2026-03-19T10:00:00",
            "first_seen_at": first_seen_at or "2026-03-19T10:00:00",
            "airtable_record_id": airtable_record_id,
        },
    )
    return (await conn.execute(text("SELECT last_insert_rowid()"))).scalar_one()


async def _insert_sidecar(
    conn,
    *,
    file_id: int,
    kind: str = "srt",
    body_text: str | None = None,
) -> int:
    await conn.execute(
        text("""
            INSERT INTO mmingest_sidecars (file_id, kind, body_text)
            VALUES (:file_id, :kind, :body_text)
        """),
        {"file_id": file_id, "kind": kind, "body_text": body_text},
    )
    return (await conn.execute(text("SELECT last_insert_rowid()"))).scalar_one()


# ---------------------------------------------------------------------------
# Helper: call handler with DATABASE_PATH pointing at the test DB
# ---------------------------------------------------------------------------


async def _call_search(db_path: str, **kwargs) -> list:
    """Invoke handle_search_mmingest with the test DB path."""
    from mcp_server.server import handle_search_mmingest

    os.environ["DATABASE_PATH"] = db_path
    return await handle_search_mmingest(kwargs)


async def _call_get_asset(db_path: str, media_id: str, mock_at=None) -> list:
    """Invoke handle_get_mmingest_asset with the test DB path.

    If mock_at is provided, patch AirtableClient with it.
    """
    from mcp_server.server import handle_get_mmingest_asset

    os.environ["DATABASE_PATH"] = db_path
    os.environ["AIRTABLE_API_KEY"] = "pat-ci-dummy-not-real"

    if mock_at is not None:
        with patch("mcp_server.server._AirtableClient", return_value=mock_at):
            return await handle_get_mmingest_asset({"media_id": media_id})
    return await handle_get_mmingest_asset({"media_id": media_id})


async def _call_recent(db_path: str, **kwargs) -> list:
    """Invoke handle_list_recent_mmingest_assets with the test DB path."""
    from mcp_server.server import handle_list_recent_mmingest_assets

    os.environ["DATABASE_PATH"] = db_path
    return await handle_list_recent_mmingest_assets(kwargs)


# ---------------------------------------------------------------------------
# Gate 1 — search_mmingest happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_happy_path(migrated_engine):
    """Gate 1: FTS5 hit returns formatted markdown with correct fields."""
    engine, db_path = migrated_engine

    async with engine.begin() as conn:
        fid = await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101.srt",
            filename="6POL0101_REV20260319.srt",
            media_id="6POL0101",
            prefix="6POL",
            revision_date="2026-03-19",
        )
        await _insert_sidecar(
            conn,
            file_id=fid,
            body_text="inside wisconsin politics: a look at the state legislature",
        )

    results = await _call_search(db_path, query="politics")
    assert len(results) == 1
    text_out = results[0].text
    assert "6POL0101" in text_out
    assert "6POL" in text_out
    # snippet should contain the matched term
    assert "politics" in text_out.lower() or "wisconsin" in text_out.lower()


# ---------------------------------------------------------------------------
# Gate 2a — search_mmingest prefix filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_prefix_filter(migrated_engine):
    """Gate 2a: prefix= filter narrows to matching show prefix only."""
    engine, db_path = migrated_engine

    async with engine.begin() as conn:
        fid_a = await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101.srt",
            filename="6POL0101.srt",
            media_id="6POL0101",
            prefix="6POL",
        )
        await _insert_sidecar(conn, file_id=fid_a, body_text="wisconsin politics")

        fid_b = await _insert_file(
            conn,
            remote_url="http://mmingest.example/WLIA0101.srt",
            filename="WLIA0101.srt",
            media_id="WLIA0101",
            prefix="WLIA",
        )
        await _insert_sidecar(conn, file_id=fid_b, body_text="wisconsin nature politics")

    results = await _call_search(db_path, query="politics", prefix="6POL")
    text_out = results[0].text
    assert "6POL0101" in text_out
    assert "WLIA0101" not in text_out


# ---------------------------------------------------------------------------
# Gate 2b — search_mmingest since filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_since_filter(migrated_engine):
    """Gate 2b: since= filters by remote_modified_at."""
    engine, db_path = migrated_engine

    async with engine.begin() as conn:
        fid_old = await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101_old.srt",
            filename="6POL0101_old.srt",
            media_id="6POL0101",
            prefix="6POL",
            remote_modified_at="2025-01-01T00:00:00",
        )
        await _insert_sidecar(conn, file_id=fid_old, body_text="wisconsin politics old episode")

        fid_new = await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0102_new.srt",
            filename="6POL0102_new.srt",
            media_id="6POL0102",
            prefix="6POL",
            remote_modified_at="2026-03-19T10:00:00",
        )
        await _insert_sidecar(conn, file_id=fid_new, body_text="wisconsin politics new episode")

    results = await _call_search(db_path, query="politics", since="2026-03-01T00:00:00")
    text_out = results[0].text
    assert "6POL0101" not in text_out, "Old row should be filtered out"
    assert "6POL0102" in text_out


# ---------------------------------------------------------------------------
# Gate 2c — search_mmingest limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_limit(migrated_engine):
    """Gate 2c: limit= caps results returned."""
    engine, db_path = migrated_engine

    async with engine.begin() as conn:
        for i in range(5):
            fid = await _insert_file(
                conn,
                remote_url=f"http://mmingest.example/6POL010{i}.srt",
                filename=f"6POL010{i}.srt",
                media_id=f"6POL010{i}",
                prefix="6POL",
            )
            await _insert_sidecar(conn, file_id=fid, body_text=f"wisconsin politics episode {i}")

    results = await _call_search(db_path, query="politics", limit=2)
    text_out = results[0].text
    # "showing 2" should appear in the header line
    assert "showing 2" in text_out


# ---------------------------------------------------------------------------
# Gate 3 — search_mmingest same results as HTTP /search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_same_results_as_http(migrated_engine):
    """Gate 3: MCP search returns same media_ids as the HTTP router.

    Both paths share the same SQL query (Option B mirror).  With identical
    seed data, both should return the same set of media_ids for a given query.
    """
    import importlib

    engine, db_path = migrated_engine

    async with engine.begin() as conn:
        for mid, body in [
            ("6POL0101", "inside wisconsin politics legislature"),
            ("6POL0102", "wisconsin politics budget debate"),
            ("2WLI0101", "wisconsin life farming nature"),
        ]:
            fid = await _insert_file(
                conn,
                remote_url=f"http://mmingest.example/{mid}.srt",
                filename=f"{mid}.srt",
                media_id=mid,
                prefix=mid[:4],
            )
            await _insert_sidecar(conn, file_id=fid, body_text=body)

    # MCP results
    mcp_results = await _call_search(db_path, query="politics")
    mcp_text = mcp_results[0].text
    mcp_media_ids = {line.split()[1] for line in mcp_text.splitlines() if line.startswith("##")}

    # HTTP router results (via FastAPI TestClient)
    os.environ["DATABASE_PATH"] = db_path
    from api.services.airtable import get_airtable_client

    mock_at = MagicMock(spec=["batch_search_sst_by_media_ids"])
    mock_at.batch_search_sst_by_media_ids = AsyncMock(return_value={})

    importlib.reload(importlib.import_module("api.routers.mmingest"))
    importlib.reload(importlib.import_module("api.main"))

    import api.main

    api.main.app.dependency_overrides[get_airtable_client] = lambda: mock_at

    from fastapi.testclient import TestClient

    client = TestClient(api.main.app, raise_server_exceptions=True)
    resp = client.get("/api/mmingest/search?q=politics")
    assert resp.status_code == 200
    http_media_ids = {r["media_id"] for r in resp.json()["results"]}

    assert mcp_media_ids == http_media_ids, (
        f"MCP and HTTP returned different media_ids:\n" f"  MCP:  {mcp_media_ids}\n" f"  HTTP: {http_media_ids}"
    )


# ---------------------------------------------------------------------------
# Gate 4 — get_mmingest_asset primary-only happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_asset_primary_only(migrated_engine):
    """Gate 4: Primary-only asset returns primary section + Airtable record ID."""
    engine, db_path = migrated_engine
    at_record_id = "recTEST123456"

    async with engine.begin() as conn:
        await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101.mp4",
            filename="6POL0101.mp4",
            file_type="mp4",
            media_id="6POL0101",
            variant_tag=None,
            superseded_by=None,
        )

    mock_at = MagicMock(spec=["batch_search_sst_by_media_ids"])
    mock_at.batch_search_sst_by_media_ids = AsyncMock(
        return_value={
            "6POL0101": {"id": at_record_id, "fields": {"Media ID": "6POL0101"}},
        }
    )

    results = await _call_get_asset(db_path, "6POL0101", mock_at=mock_at)
    text_out = results[0].text
    assert "6POL0101" in text_out
    assert "Primary" in text_out
    assert at_record_id in text_out
    assert "Variants" in text_out
    assert "Superseded" in text_out


# ---------------------------------------------------------------------------
# Gate 5 — get_mmingest_asset with PLEDGE variant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_asset_with_variant(migrated_engine):
    """Gate 5: Primary + PLEDGE variant both appear in correct sections."""
    engine, db_path = migrated_engine

    async with engine.begin() as conn:
        await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101.mp4",
            filename="6POL0101.mp4",
            file_type="mp4",
            media_id="6POL0101",
            variant_tag=None,
            superseded_by=None,
        )
        await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101_PLEDGE.mp4",
            filename="6POL0101_PLEDGE.mp4",
            file_type="mp4",
            media_id="6POL0101",
            variant_tag="PLEDGE",
            superseded_by=None,
        )

    mock_at = MagicMock(spec=["batch_search_sst_by_media_ids"])
    mock_at.batch_search_sst_by_media_ids = AsyncMock(return_value={})

    results = await _call_get_asset(db_path, "6POL0101", mock_at=mock_at)
    text_out = results[0].text
    assert "Primary" in text_out
    assert "PLEDGE" in text_out
    assert "6POL0101_PLEDGE.mp4" in text_out


# ---------------------------------------------------------------------------
# Gate 6 — get_mmingest_asset 404 case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_asset_not_found(migrated_engine):
    """Gate 6: Unknown media_id returns friendly error TextContent, no exception."""
    _, db_path = migrated_engine

    mock_at = MagicMock(spec=["batch_search_sst_by_media_ids"])
    mock_at.batch_search_sst_by_media_ids = AsyncMock(return_value={})

    results = await _call_get_asset(db_path, "NONEXISTENT999", mock_at=mock_at)
    assert len(results) == 1
    text_out = results[0].text
    assert "No asset found" in text_out
    assert "NONEXISTENT999" in text_out


# ---------------------------------------------------------------------------
# Gate 7 — get_mmingest_asset Airtable failure fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_asset_airtable_failure_fallback(migrated_engine):
    """Gate 7: Airtable exception does not crash the tool; fallback annotation shown."""
    engine, db_path = migrated_engine

    async with engine.begin() as conn:
        await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101.mp4",
            filename="6POL0101.mp4",
            file_type="mp4",
            media_id="6POL0101",
            variant_tag=None,
            superseded_by=None,
            airtable_record_id="recCACHED000001",
        )

    mock_at = MagicMock(spec=["batch_search_sst_by_media_ids"])
    mock_at.batch_search_sst_by_media_ids = AsyncMock(side_effect=Exception("Airtable timeout"))

    results = await _call_get_asset(db_path, "6POL0101", mock_at=mock_at)
    assert len(results) == 1
    text_out = results[0].text
    # Must not raise; must return asset info
    assert "6POL0101" in text_out
    assert "Primary" in text_out
    # Must note the fallback failure
    assert "lookup failed" in text_out.lower() or "cached" in text_out.lower()


# ---------------------------------------------------------------------------
# Gate 8a — list_recent_mmingest_assets since= filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_since_filter(migrated_engine):
    """Gate 8a: since= filter returns only files arrived on/after that timestamp."""
    engine, db_path = migrated_engine

    t_old = "2026-01-01T00:00:00"
    t_new = "2026-06-01T00:00:00"

    async with engine.begin() as conn:
        await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101.mp4",
            filename="6POL0101.mp4",
            file_type="mp4",
            media_id="6POL0101",
            prefix="6POL",
            first_seen_at=t_old,
        )
        await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0102.mp4",
            filename="6POL0102.mp4",
            file_type="mp4",
            media_id="6POL0102",
            prefix="6POL",
            first_seen_at=t_new,
        )

    results = await _call_recent(db_path, since="2026-05-01T00:00:00")
    text_out = results[0].text
    assert "6POL0102" in text_out
    assert "6POL0101" not in text_out


# ---------------------------------------------------------------------------
# Gate 8b — list_recent_mmingest_assets limit cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_limit(migrated_engine):
    """Gate 8b: limit= caps results returned."""
    engine, db_path = migrated_engine

    now = datetime.now(timezone.utc)
    async with engine.begin() as conn:
        for i in range(5):
            ts = (now - timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%S")
            await _insert_file(
                conn,
                remote_url=f"http://mmingest.example/6POL010{i}.mp4",
                filename=f"6POL010{i}.mp4",
                file_type="mp4",
                media_id=f"6POL010{i}",
                prefix="6POL",
                first_seen_at=ts,
            )

    results = await _call_recent(db_path, since="2000-01-01T00:00:00", limit=2)
    text_out = results[0].text
    assert "showing 2" in text_out


# ---------------------------------------------------------------------------
# Gate 9 — list_recent_mmingest_assets same results as HTTP /recent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_same_results_as_http(migrated_engine):
    """Gate 9: MCP recent returns same media_ids as the HTTP router for a given window."""
    import importlib

    engine, db_path = migrated_engine

    # Use naive UTC timestamps (no +00:00 offset) so the URL query param is clean.
    now = datetime.now(timezone.utc)
    ts_new = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    ts_old = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")
    # since_str: 3 hours ago, naive format — safe in URLs and accepted by both paths
    since_str = (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S")

    async with engine.begin() as conn:
        for mid, ts in [("6POL0101", ts_new), ("6POL0102", ts_new), ("2WLI0101", ts_old)]:
            await _insert_file(
                conn,
                remote_url=f"http://mmingest.example/{mid}.mp4",
                filename=f"{mid}.mp4",
                file_type="mp4",
                media_id=mid,
                prefix=mid[:4],
                first_seen_at=ts,
            )

    # MCP results
    mcp_results = await _call_recent(db_path, since=since_str)
    mcp_text = mcp_results[0].text
    # Extract media_ids from bold labels (lines starting with "- **")
    mcp_media_ids = set()
    for line in mcp_text.splitlines():
        if line.startswith("- **"):
            # "- **6POL0101 — Wisconsin Politics**" -> "6POL0101"
            label = line[4:].split("**")[0].strip()
            mid_part = label.split(" ")[0].split("—")[0].strip()
            if mid_part:
                mcp_media_ids.add(mid_part)

    # HTTP router results
    os.environ["DATABASE_PATH"] = db_path
    from api.services.airtable import get_airtable_client

    mock_at = MagicMock(spec=["batch_search_sst_by_media_ids"])
    mock_at.batch_search_sst_by_media_ids = AsyncMock(return_value={})

    importlib.reload(importlib.import_module("api.routers.mmingest"))
    importlib.reload(importlib.import_module("api.main"))

    import api.main

    api.main.app.dependency_overrides[get_airtable_client] = lambda: mock_at

    from fastapi.testclient import TestClient

    client = TestClient(api.main.app, raise_server_exceptions=True)
    resp = client.get(f"/api/mmingest/recent?since={since_str}")
    assert resp.status_code == 200
    http_media_ids = {r["media_id"] for r in resp.json()["results"]}

    assert mcp_media_ids == http_media_ids, (
        f"MCP and HTTP returned different media_ids:\n" f"  MCP:  {mcp_media_ids}\n" f"  HTTP: {http_media_ids}"
    )


# ---------------------------------------------------------------------------
# Gate 10 — search_mmingest empty-query guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_empty_query(migrated_engine):
    """Gate 10: Empty or whitespace-only query returns an error TextContent."""
    _, db_path = migrated_engine

    for bad_query in ["", "   "]:
        results = await _call_search(db_path, query=bad_query)
        assert len(results) == 1
        assert "Error" in results[0].text


# ---------------------------------------------------------------------------
# Gate 11 — search_mmingest FTS5 syntax error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_fts5_syntax_error(migrated_engine):
    """Gate 11: Malformed FTS5 query returns friendly error, does not propagate exception."""
    engine, db_path = migrated_engine

    async with engine.begin() as conn:
        fid = await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101.srt",
            filename="6POL0101.srt",
            media_id="6POL0101",
            prefix="6POL",
        )
        await _insert_sidecar(conn, file_id=fid, body_text="wisconsin politics")

    # Unbalanced quote — malformed FTS5 syntax
    results = await _call_search(db_path, query='"unclosed phrase')
    assert len(results) == 1
    text_out = results[0].text
    # Must be a friendly error, not a traceback
    assert "Error" in text_out or "error" in text_out
    assert "Traceback" not in text_out
    assert "Exception" not in text_out


# ---------------------------------------------------------------------------
# Gate 12 — list_recent_mmingest_assets empty result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_empty_result(migrated_engine):
    """Gate 12: No rows in window returns friendly 'no new arrivals' message."""
    _, db_path = migrated_engine

    # No seed data — nothing in the last 24h
    results = await _call_recent(db_path)
    assert len(results) == 1
    text_out = results[0].text
    assert "no new arrivals" in text_out.lower() or "No new arrivals" in text_out
