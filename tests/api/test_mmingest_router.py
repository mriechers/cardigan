"""Tests for the mmingest API router (Sprint 3B).

Verification gates covered here (per sprint-3b-handoff.md):
  Gate 1  — /search happy path: FTS5 hit with snippet + BM25 ordering
  Gate 2  — /search filters: ?prefix=, ?since=, ?limit= / ?offset= pagination
  Gate 3  — /assets/{id} primary-only
  Gate 4  — /assets/{id} with variant
  Gate 5  — /assets/{id} with superseded REV
  Gate 6  — /assets/{id} 404 for unknown media_id
  Gate 7  — /assets/{id}/url happy path
  Gate 8  — /assets/{id}/url?variant=PLEDGE
  Gate 9  — /assets/{id}/url?variant=PLEDGE 404 (no such variant)
  Gate 10 — /assets/{id}/captions happy path (no mmingest network call)
  Gate 11 — /assets/{id}/captions 503 (body_text NULL/empty)
  Gate 12 — /recent ordering + filtering

Uses fastapi.testclient.TestClient against an isolated migrated SQLite DB.
AirtableClient calls are mocked via app.dependency_overrides so FastAPI's DI
system doesn't try to introspect the mock's *args/**kwargs signature.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# ---------------------------------------------------------------------------
# Shared fixture: isolated migrated DB engine + DB-path override
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def migrated_engine():
    """Stand up a fresh DB via `alembic upgrade head`, return (engine, db_path).

    Mirrors the pattern in tests/api/test_mmingest_parity.py exactly.
    """
    fd, db_path = tempfile.mkstemp(suffix="_mmingest_router_test.db")
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
# TestClient / mock helpers
# ---------------------------------------------------------------------------


def _make_mock_airtable(media_id: str = "6POL0101", at_record_id: str = "recTEST000001") -> MagicMock:
    """Return a mock AirtableClient whose batch_search_sst_by_media_ids returns a known record."""
    mock = MagicMock(spec=["batch_search_sst_by_media_ids"])
    mock.batch_search_sst_by_media_ids = AsyncMock(
        return_value={
            media_id: {
                "id": at_record_id,
                "fields": {"Media ID": media_id, "Title": "Test Episode"},
            }
        }
    )
    return mock


def _make_client(db_path: str, mock_airtable: MagicMock) -> TestClient:
    """Return a FastAPI TestClient with DATABASE_PATH patched and Airtable mocked.

    Uses app.dependency_overrides so FastAPI's DI system receives a callable
    with a clean signature (not the *args/**kwargs of a raw MagicMock).
    """
    import importlib

    # Reload the router and app so DATABASE_PATH env var is picked up fresh.
    os.environ["DATABASE_PATH"] = db_path

    import api.routers.mmingest

    importlib.reload(api.routers.mmingest)

    import api.main

    importlib.reload(api.main)

    from api.services.airtable import get_airtable_client

    # dependency_overrides: override with a plain callable that returns the mock.
    # This avoids FastAPI introspecting the mock's *args/**kwargs call signature.
    api.main.app.dependency_overrides[get_airtable_client] = lambda: mock_airtable

    client = TestClient(api.main.app, raise_server_exceptions=True)
    return client


# ---------------------------------------------------------------------------
# DB seed helpers
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
# Gate 1 — /search happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_happy_path(migrated_engine):
    """Gate 1: FTS5 hit returns snippet + correct display fields."""
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

    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)
    resp = client.get("/api/mmingest/search?q=politics")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] >= 1
    hit = data["results"][0]
    assert hit["media_id"] == "6POL0101"
    assert hit["prefix"] == "6POL"
    assert hit["revision_date"] == "2026-03-19"
    assert "politics" in hit["snippet"].lower() or "wisconsin" in hit["snippet"].lower()
    assert hit["sidecar_kind"] == "srt"


@pytest.mark.asyncio
async def test_search_bm25_ordering(migrated_engine):
    """Gate 1 (BM25): Higher-relevance document ranks first."""
    engine, db_path = migrated_engine

    async with engine.begin() as conn:
        # Row A: mentions "politics" once
        fid_a = await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101.srt",
            filename="6POL0101.srt",
            media_id="6POL0101",
            prefix="6POL",
        )
        await _insert_sidecar(conn, file_id=fid_a, body_text="just one mention of politics here")

        # Row B: mentions "politics" many times — should rank higher
        fid_b = await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0102.srt",
            filename="6POL0102.srt",
            media_id="6POL0102",
            prefix="6POL",
        )
        await _insert_sidecar(
            conn,
            file_id=fid_b,
            body_text="politics politics politics politics is the topic of this episode about politics",
        )

    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)
    resp = client.get("/api/mmingest/search?q=politics&limit=10")

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 2
    # Row B has higher TF so it should rank first (BM25 ORDER BY rank — lower rank value = better)
    assert results[0]["media_id"] == "6POL0102"


# ---------------------------------------------------------------------------
# Gate 2 — /search filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_prefix_filter(migrated_engine):
    """Gate 2: ?prefix=6POL filters correctly."""
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

    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)
    resp = client.get("/api/mmingest/search?q=politics&prefix=6POL")

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert all(r["prefix"] == "6POL" for r in results)
    assert any(r["media_id"] == "6POL0101" for r in results)
    assert not any(r["media_id"] == "WLIA0101" for r in results)


@pytest.mark.asyncio
async def test_search_since_filter(migrated_engine):
    """Gate 2: ?since= filters by remote_modified_at."""
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

    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)
    resp = client.get("/api/mmingest/search?q=politics&since=2026-03-01T00:00:00")

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert all(r["media_id"] != "6POL0101" for r in results), "Old row should be filtered out"
    assert any(r["media_id"] == "6POL0102" for r in results)


@pytest.mark.asyncio
async def test_search_pagination(migrated_engine):
    """Gate 2: limit=1 returns 1 result; offset=1 returns the next."""
    engine, db_path = migrated_engine

    async with engine.begin() as conn:
        for i in range(3):
            fid = await _insert_file(
                conn,
                remote_url=f"http://mmingest.example/6POL010{i}.srt",
                filename=f"6POL010{i}.srt",
                media_id=f"6POL010{i}",
                prefix="6POL",
            )
            await _insert_sidecar(conn, file_id=fid, body_text=f"wisconsin politics episode {i}")

    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)

    resp0 = client.get("/api/mmingest/search?q=politics&limit=1&offset=0")
    resp1 = client.get("/api/mmingest/search?q=politics&limit=1&offset=1")

    assert resp0.status_code == 200
    assert resp1.status_code == 200
    r0 = resp0.json()
    r1 = resp1.json()
    assert len(r0["results"]) == 1
    assert len(r1["results"]) == 1
    assert r0["total"] == 3
    # The two pages return different items
    assert r0["results"][0]["media_id"] != r1["results"][0]["media_id"]


# ---------------------------------------------------------------------------
# Gate 3 — /assets/{id} primary-only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assets_primary_only(migrated_engine):
    """Gate 3: Primary-only asset returns {primary, variants:[], superseded:[]}."""
    engine, db_path = migrated_engine
    at_record_id = "recTEST123"

    async with engine.begin() as conn:
        await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101.mp4",
            filename="6POL0101.mp4",
            file_type="mp4",
            media_id="6POL0101",
            prefix="6POL",
            variant_tag=None,
            superseded_by=None,
        )

    mock_at = _make_mock_airtable(media_id="6POL0101", at_record_id=at_record_id)
    client = _make_client(db_path, mock_at)
    resp = client.get("/api/mmingest/assets/6POL0101")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["primary"] is not None
    assert data["primary"]["media_id"] == "6POL0101"
    assert data["primary"]["airtable_record_id"] == at_record_id
    assert data["variants"] == []
    assert data["superseded"] == []
    # Verify Airtable was called with the right media_id
    mock_at.batch_search_sst_by_media_ids.assert_called_once_with(["6POL0101"])


# ---------------------------------------------------------------------------
# Gate 4 — /assets/{id} with variant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assets_with_variant(migrated_engine):
    """Gate 4: Primary + PLEDGE variant returns both in correct slots."""
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

    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)
    resp = client.get("/api/mmingest/assets/6POL0101")

    assert resp.status_code == 200
    data = resp.json()
    assert data["primary"] is not None
    assert data["primary"]["variant_tag"] is None
    assert len(data["variants"]) == 1
    assert data["variants"][0]["variant_tag"] == "PLEDGE"
    assert data["superseded"] == []


# ---------------------------------------------------------------------------
# Gate 5 — /assets/{id} with superseded REV
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assets_with_superseded_rev(migrated_engine):
    """Gate 5: Older REV row is in superseded[], newer is primary."""
    engine, db_path = migrated_engine

    async with engine.begin() as conn:
        # Newer REV (current primary — no superseded_by)
        new_id = await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101_REV20260319.mp4",
            filename="6POL0101_REV20260319.mp4",
            file_type="mp4",
            media_id="6POL0101",
            revision_date="2026-03-19",
            variant_tag=None,
            superseded_by=None,
        )
        # Older REV (points at new_id as superseded_by)
        await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101_REV20260101.mp4",
            filename="6POL0101_REV20260101.mp4",
            file_type="mp4",
            media_id="6POL0101",
            revision_date="2026-01-01",
            variant_tag=None,
            superseded_by=new_id,
        )

    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)
    resp = client.get("/api/mmingest/assets/6POL0101")

    assert resp.status_code == 200
    data = resp.json()
    assert data["primary"] is not None
    assert data["primary"]["revision_date"] == "2026-03-19"
    assert data["variants"] == []
    assert len(data["superseded"]) == 1
    assert data["superseded"][0]["revision_date"] == "2026-01-01"


# ---------------------------------------------------------------------------
# Gate 6 — /assets/{id} 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assets_404(migrated_engine):
    """Gate 6: Unknown media_id returns clean 404."""
    _, db_path = migrated_engine
    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)
    resp = client.get("/api/mmingest/assets/NONEXISTENT9999")

    assert resp.status_code == 404
    data = resp.json()
    assert "detail" in data
    assert "NONEXISTENT9999" in data["detail"]


# ---------------------------------------------------------------------------
# Gate 7 — /assets/{id}/url happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asset_url_happy_path(migrated_engine):
    """Gate 7: Returns {url: ...} matching the primary's URL."""
    engine, db_path = migrated_engine
    expected_url = "http://mmingest.example/6POL0101.mp4"

    async with engine.begin() as conn:
        await _insert_file(
            conn,
            remote_url=expected_url,
            filename="6POL0101.mp4",
            file_type="mp4",
            media_id="6POL0101",
            variant_tag=None,
            superseded_by=None,
        )

    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)
    resp = client.get("/api/mmingest/assets/6POL0101/url")

    assert resp.status_code == 200
    assert resp.json() == {"url": expected_url}


# ---------------------------------------------------------------------------
# Gate 8 — /assets/{id}/url?variant=PLEDGE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asset_url_variant(migrated_engine):
    """Gate 8: ?variant=PLEDGE returns the variant's URL."""
    engine, db_path = migrated_engine
    pledge_url = "http://mmingest.example/6POL0101_PLEDGE.mp4"

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
            remote_url=pledge_url,
            filename="6POL0101_PLEDGE.mp4",
            file_type="mp4",
            media_id="6POL0101",
            variant_tag="PLEDGE",
            superseded_by=None,
        )

    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)
    resp = client.get("/api/mmingest/assets/6POL0101/url?variant=PLEDGE")

    assert resp.status_code == 200
    assert resp.json() == {"url": pledge_url}


# ---------------------------------------------------------------------------
# Gate 9 — /assets/{id}/url?variant=PLEDGE 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asset_url_variant_404(migrated_engine):
    """Gate 9: No PLEDGE variant for this media_id returns 404."""
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
        # No PLEDGE variant seeded

    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)
    resp = client.get("/api/mmingest/assets/6POL0101/url?variant=PLEDGE")

    assert resp.status_code == 404
    assert "PLEDGE" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Gate 10 — /assets/{id}/captions happy path (no mmingest network call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_captions_happy_path(migrated_engine):
    """Gate 10: Returns cached SRT body; no mmingest network call."""
    engine, db_path = migrated_engine
    srt_body = "1\n00:00:01,000 --> 00:00:04,000\nWisconsin politics.\n"

    async with engine.begin() as conn:
        fid = await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101.mp4",
            filename="6POL0101.mp4",
            file_type="mp4",
            media_id="6POL0101",
            variant_tag=None,
            superseded_by=None,
        )
        await _insert_sidecar(conn, file_id=fid, kind="srt", body_text=srt_body)

    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)
    resp = client.get("/api/mmingest/assets/6POL0101/captions?format=srt")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["media_id"] == "6POL0101"
    assert data["kind"] == "srt"
    assert data["body_text"] == srt_body


# ---------------------------------------------------------------------------
# Gate 11 — /assets/{id}/captions 503 (no sidecar / empty body_text)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_captions_missing_sidecar_404(migrated_engine):
    """Gate 11a: No sidecar row of that format returns 404."""
    engine, db_path = migrated_engine

    async with engine.begin() as conn:
        fid = await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101.mp4",
            filename="6POL0101.mp4",
            file_type="mp4",
            media_id="6POL0101",
            variant_tag=None,
            superseded_by=None,
        )
        # Only SRT sidecar; requesting SCC should 404
        await _insert_sidecar(conn, file_id=fid, kind="srt", body_text="some caption text")

    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)
    resp = client.get("/api/mmingest/assets/6POL0101/captions?format=scc")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_captions_empty_body_503(migrated_engine):
    """Gate 11b: Sidecar row exists but body_text is NULL/empty returns 503."""
    engine, db_path = migrated_engine

    async with engine.begin() as conn:
        fid = await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101.mp4",
            filename="6POL0101.mp4",
            file_type="mp4",
            media_id="6POL0101",
            variant_tag=None,
            superseded_by=None,
        )
        # body_text is NULL — indexer hasn't run yet
        await _insert_sidecar(conn, file_id=fid, kind="srt", body_text=None)

    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)
    resp = client.get("/api/mmingest/assets/6POL0101/captions?format=srt")

    assert resp.status_code == 503
    assert "Retry-After" in resp.headers


# ---------------------------------------------------------------------------
# Gate 12 — /recent ordering + filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_ordering_and_filtering(migrated_engine):
    """Gate 12: Results are in reverse-chronological order; since and prefix filters work."""
    engine, db_path = migrated_engine

    t_old = "2026-01-01T00:00:00"
    t_mid = "2026-03-01T00:00:00"
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
            first_seen_at=t_mid,
        )
        await _insert_file(
            conn,
            remote_url="http://mmingest.example/WLIA0101.mp4",
            filename="WLIA0101.mp4",
            file_type="mp4",
            media_id="WLIA0101",
            prefix="WLIA",
            first_seen_at=t_new,
        )

    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)

    # All three — ordered newest first
    resp_all = client.get("/api/mmingest/recent?since=2025-01-01T00:00:00")
    assert resp_all.status_code == 200
    all_results = resp_all.json()["results"]
    assert len(all_results) == 3
    # Verify reverse-chronological ordering
    assert all_results[0]["media_id"] == "WLIA0101"
    assert all_results[1]["media_id"] == "6POL0102"
    assert all_results[2]["media_id"] == "6POL0101"

    # since filter: only after t_mid
    resp_since = client.get("/api/mmingest/recent?since=2026-02-01T00:00:00")
    since_results = resp_since.json()["results"]
    assert not any(r["media_id"] == "6POL0101" for r in since_results)
    assert any(r["media_id"] == "6POL0102" for r in since_results)
    assert any(r["media_id"] == "WLIA0101" for r in since_results)

    # prefix filter
    resp_prefix = client.get("/api/mmingest/recent?since=2025-01-01T00:00:00&prefix=6POL")
    prefix_results = resp_prefix.json()["results"]
    assert all(r["prefix"] == "6POL" for r in prefix_results)
    assert not any(r["media_id"] == "WLIA0101" for r in prefix_results)


@pytest.mark.asyncio
async def test_recent_default_24h_window(migrated_engine):
    """Gate 12: Without ?since=, defaults to last 24h."""
    engine, db_path = migrated_engine

    old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")
    new_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")

    async with engine.begin() as conn:
        await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0101_old.mp4",
            filename="6POL0101_old.mp4",
            file_type="mp4",
            media_id="6POL0101",
            prefix="6POL",
            first_seen_at=old_ts,
        )
        await _insert_file(
            conn,
            remote_url="http://mmingest.example/6POL0102_new.mp4",
            filename="6POL0102_new.mp4",
            file_type="mp4",
            media_id="6POL0102",
            prefix="6POL",
            first_seen_at=new_ts,
        )

    mock_at = _make_mock_airtable()
    client = _make_client(db_path, mock_at)
    resp = client.get("/api/mmingest/recent")

    assert resp.status_code == 200
    results = resp.json()["results"]
    # Old (48h ago) should NOT appear; new (1h ago) should appear
    assert not any(r["media_id"] == "6POL0101" for r in results)
    assert any(r["media_id"] == "6POL0102" for r in results)
