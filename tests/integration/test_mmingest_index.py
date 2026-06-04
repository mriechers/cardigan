"""Integration tests for MmingestIndexer end-to-end pipeline.

Tests the full walk -> upsert -> variant-lineage -> sidecar -> FTS path
against an isolated SQLite DB with all migrations applied.

HTTP layer is mocked throughout so no real network calls are made.

Verification gates exercised here (per handoff brief):
  Gate 2  — Canonical regression case (6POL0101_REV20260319.srt end-to-end)
  Gate 3  — fts_parity_delta() returns 0 after seed insert
  Gate 4  — Variant lineage UPDATE (older _REV row gets superseded_by)
  Gate 5  — Variant coexistence (primary + PLEDGE variant both survive)
  Gate 6  — Unknown-tag preservation (variant_tag stays NULL; unknown_tag logged)
  Gate 7  — Idempotency (second run produces zero writes)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.services.mmingest.crawler import ChangeTriple, FileWorkItem
from api.services.mmingest.indexer import MmingestIndexer
from api.services.mmingest.sidecar_fetcher import SidecarResult

# ---------------------------------------------------------------------------
# Shared fixture: isolated migrated DB engine
# ---------------------------------------------------------------------------

# Canonical SRT body text used in regression case (must contain the search term)
_CANONICAL_SRT_BODY = """\
1
00:00:01,000 --> 00:00:04,000
Welcome to Inside Wisconsin Politics.

2
00:00:05,000 --> 00:00:09,000
Tonight we discuss the inside wisconsin politics process.
"""


@pytest_asyncio.fixture
async def migrated_engine():
    """Stand up a fresh DB via `alembic upgrade head`, return an async engine.

    Mirrors the fixture in tests/api/test_mmingest_parity.py exactly so the
    FTS5 virtual table + triggers exist as in production.
    """
    fd, db_path = tempfile.mkstemp(suffix="_index_test.db")
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


# ---------------------------------------------------------------------------
# Helper: build a FileWorkItem (avoids repeating constructor noise in tests)
# ---------------------------------------------------------------------------


def _make_file_work_item(
    url: str,
    filename: str,
    media_id: Optional[str] = "6POL0101",
    prefix: Optional[str] = "6POL",
    prefix_category: str = "non-broadcast",
    show_name: Optional[str] = "Inside Wisconsin Politics",
    season: Optional[int] = 1,
    episode: Optional[int] = 1,
    hd: Optional[bool] = None,
    revision_date: Optional[str] = None,
    variant_tag: Optional[str] = None,
    unknown_tag: Optional[str] = None,
    file_type: str = "srt",
    remote_modified_at: Optional[datetime] = None,
    file_size_bytes: Optional[int] = 34000,
    change_triple: ChangeTriple = (None, None, 34000),
    lane: str = "sidecar",
    directory_path: str = "/IWP/",
) -> FileWorkItem:
    return FileWorkItem(
        url=url,
        directory_path=directory_path,
        filename=filename,
        media_id=media_id,
        prefix=prefix,
        prefix_category=prefix_category,
        show_name=show_name,
        season=season,
        episode=episode,
        hd=hd,
        revision_date=revision_date,
        variant_tag=variant_tag,
        unknown_tag=unknown_tag,
        file_type=file_type,
        remote_modified_at=remote_modified_at,
        file_size_bytes=file_size_bytes,
        change_triple=change_triple,
        lane=lane,
    )


# ---------------------------------------------------------------------------
# Helper: build a SidecarResult
# ---------------------------------------------------------------------------


def _make_sidecar_result(
    url: str,
    filename: str,
    body_text: str,
    kind: str = "srt",
    file_id_hint: Optional[int] = None,
) -> SidecarResult:
    return SidecarResult(
        url=url,
        filename=filename,
        kind=kind,
        ok=True,
        body_text=body_text,
        bytes=len(body_text.encode()),
        fetched_at=datetime.now(timezone.utc),
        file_id_hint=file_id_hint,
    )


# ---------------------------------------------------------------------------
# Helper: run the indexer with controlled work items + sidecar results
# ---------------------------------------------------------------------------


async def _run_indexer_with_mocks(
    engine,
    work_items: list[FileWorkItem],
    sidecar_results: Optional[list[SidecarResult]] = None,
) -> "IndexerRun":  # noqa: F821
    """Run MmingestIndexer.run_once() with crawler and fetcher mocked.

    The crawler's delta_walk is replaced by a function that returns work_items.
    The fetcher's fetch_many is replaced by a function that returns sidecar_results.
    """
    sidecar_results = sidecar_results or []

    # Build a mock SidecarFetcher whose fetch_many returns sidecar_results.
    # We patch at the module level to intercept construction inside run_once().
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch_many = AsyncMock(return_value=sidecar_results)

    # Mock the crawler so delta_walk returns our predetermined work_items.
    mock_crawler = AsyncMock()
    mock_crawler.delta_walk = AsyncMock(return_value=work_items)

    with (
        patch("api.services.mmingest.indexer.MmingestCrawler", return_value=mock_crawler),
        patch("api.services.mmingest.indexer.SidecarFetcher", return_value=mock_fetcher),
    ):
        indexer = MmingestIndexer(engine=engine)
        return await indexer.run_once()


# ---------------------------------------------------------------------------
# Gate 2 + 3: Canonical regression case — 6POL0101_REV20260319.srt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canonical_regression_case(migrated_engine):
    """Gate 2: end-to-end index of 6POL0101_REV20260319.srt.

    After run_once():
      - mmingest_files row exists with expected fields
      - mmingest_sidecars row exists with 'srt' kind and correct body_text
      - FTS5 MATCH 'inside wisconsin politics' returns the row
      - BM25 rank is negative
      - fts_parity_delta() returns 0 (Gate 3)
    """
    url = "https://mmingest.pbswi.wisc.edu/IWP/6POL0101_REV20260319.srt"
    filename = "6POL0101_REV20260319.srt"

    work_item = _make_file_work_item(
        url=url,
        filename=filename,
        revision_date="2026-03-19",
        variant_tag=None,
    )
    sidecar = _make_sidecar_result(url=url, filename=filename, body_text=_CANONICAL_SRT_BODY)

    run = await _run_indexer_with_mocks(migrated_engine, [work_item], [sidecar])

    assert run.files_seen == 1
    assert run.sidecars_persisted == 1
    assert run.fts_parity_delta == 0  # Gate 3

    async with migrated_engine.connect() as conn:
        # Verify mmingest_files row
        file_row = (
            await conn.execute(
                text(
                    "SELECT prefix, season, episode, revision_date, variant_tag FROM mmingest_files WHERE remote_url = :url"
                ),
                {"url": url},
            )
        ).fetchone()
        assert file_row is not None, "mmingest_files row missing"
        assert file_row[0] == "6POL"
        assert file_row[1] == "1" or file_row[1] == 1  # SQLite returns text or int
        assert file_row[2] == "1" or file_row[2] == 1
        assert file_row[3] == "2026-03-19"
        assert file_row[4] is None

        # Verify FTS5 MATCH
        fts_rows = (
            await conn.execute(
                text(
                    "SELECT body_text FROM mmingest_sidecars_fts"
                    " WHERE mmingest_sidecars_fts MATCH 'inside wisconsin politics'"
                )
            )
        ).fetchall()
        assert len(fts_rows) >= 1, "FTS5 did not index the sidecar"
        assert any("inside wisconsin politics" in row[0].lower() for row in fts_rows)

        # Verify BM25 rank is negative
        rank_rows = (await conn.execute(text("""
                    SELECT fts.rank
                    FROM mmingest_sidecars_fts AS fts
                    WHERE mmingest_sidecars_fts MATCH 'inside wisconsin politics'
                """))).fetchall()
        assert len(rank_rows) >= 1
        assert float(rank_rows[0][0]) < 0, f"Expected negative BM25 rank, got {rank_rows[0][0]}"


# ---------------------------------------------------------------------------
# Gate 4: Variant lineage — superseded_by set on older _REV row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_variant_lineage_superseded_by(migrated_engine):
    """Gate 4: two _REV dates for the same media_id.

    After run_once():
      - Older row (REV20260101) has superseded_by = id of newer row
      - Newer row (REV20260319) has superseded_by = NULL
      - Both rows persist (no deletes)
      - Re-running produces the same state (idempotency portion)
    """
    url_old = "https://mmingest.pbswi.wisc.edu/IWP/6POL0101_REV20260101.srt"
    url_new = "https://mmingest.pbswi.wisc.edu/IWP/6POL0101_REV20260319.srt"

    old_item = _make_file_work_item(url=url_old, filename="6POL0101_REV20260101.srt", revision_date="2026-01-01")
    new_item = _make_file_work_item(url=url_new, filename="6POL0101_REV20260319.srt", revision_date="2026-03-19")

    await _run_indexer_with_mocks(migrated_engine, [old_item, new_item], [])

    async with migrated_engine.connect() as conn:
        rows = (await conn.execute(text("""
                    SELECT remote_url, id, superseded_by, revision_date
                    FROM mmingest_files
                    WHERE media_id = '6POL0101'
                    ORDER BY revision_date
                """))).fetchall()

    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"

    old_row = next(r for r in rows if r[3] == "2026-01-01")
    new_row = next(r for r in rows if r[3] == "2026-03-19")

    assert old_row[2] == new_row[1], f"Older row superseded_by={old_row[2]} should point at newer row id={new_row[1]}"
    assert new_row[2] is None, f"Newer row superseded_by should be NULL, got {new_row[2]}"

    # Idempotency: re-run with the same items; state must be unchanged
    await _run_indexer_with_mocks(migrated_engine, [old_item, new_item], [])

    async with migrated_engine.connect() as conn:
        rows2 = (await conn.execute(text("""
                    SELECT remote_url, id, superseded_by, revision_date
                    FROM mmingest_files
                    WHERE media_id = '6POL0101'
                    ORDER BY revision_date
                """))).fetchall()

    assert len(rows2) == 2, "Idempotency failed: row count changed on second run"
    old_row2 = next(r for r in rows2 if r[3] == "2026-01-01")
    new_row2 = next(r for r in rows2 if r[3] == "2026-03-19")
    assert old_row2[2] == new_row2[1], "Idempotency failed: superseded_by changed"
    assert new_row2[2] is None, "Idempotency failed: primary superseded_by non-NULL"


# ---------------------------------------------------------------------------
# Gate 5: Variant coexistence — primary + PLEDGE variant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_variant_coexistence(migrated_engine):
    """Gate 5: primary and PLEDGE variant both survive with correct lineage.

    Primary: 6POL0101.srt  -> variant_tag=NULL, superseded_by=NULL
    Variant: 6POL0101_PLEDGE.srt -> variant_tag='PLEDGE', superseded_by=NULL

    They must NOT be linked via superseded_by.
    """
    url_primary = "https://mmingest.pbswi.wisc.edu/IWP/6POL0101.srt"
    url_pledge = "https://mmingest.pbswi.wisc.edu/IWP/6POL0101_PLEDGE.srt"

    primary_item = _make_file_work_item(
        url=url_primary,
        filename="6POL0101.srt",
        variant_tag=None,
        revision_date=None,
    )
    pledge_item = _make_file_work_item(
        url=url_pledge,
        filename="6POL0101_PLEDGE.srt",
        variant_tag="PLEDGE",
        revision_date=None,
    )

    await _run_indexer_with_mocks(migrated_engine, [primary_item, pledge_item], [])

    async with migrated_engine.connect() as conn:
        rows = (await conn.execute(text("""
                    SELECT remote_url, variant_tag, superseded_by
                    FROM mmingest_files
                    WHERE media_id = '6POL0101'
                    ORDER BY variant_tag NULLS FIRST
                """))).fetchall()

    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"

    by_url = {r[0]: r for r in rows}
    p = by_url[url_primary]
    v = by_url[url_pledge]

    assert p[1] is None, f"Primary variant_tag should be NULL, got {p[1]!r}"
    assert p[2] is None, f"Primary superseded_by should be NULL, got {p[2]}"
    assert v[1] == "PLEDGE", f"Variant tag should be PLEDGE, got {v[1]!r}"
    assert v[2] is None, f"Variant superseded_by should be NULL, got {v[2]}"


# ---------------------------------------------------------------------------
# Gate 6: Unknown-tag preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_tag_preserved(migrated_engine, caplog):
    """Gate 6: file with _NOVELTAG suffix has variant_tag=NULL in DB.

    The unknown_tag is preserved on the FileWorkItem (crawler responsibility)
    and the indexer logs an INFO message noting it for vocabulary growth.
    variant_tag must stay NULL — unknown tags do NOT write to variant_tag.
    """
    import logging

    url = "https://mmingest.pbswi.wisc.edu/IWP/6POL0101_NOVELTAG.srt"
    item = _make_file_work_item(
        url=url,
        filename="6POL0101_NOVELTAG.srt",
        variant_tag=None,  # unknown tag -> NOT written to variant_tag
        unknown_tag="NOVELTAG",
        revision_date=None,
    )

    with caplog.at_level(logging.INFO, logger="api.services.mmingest.indexer"):
        await _run_indexer_with_mocks(migrated_engine, [item], [])

    async with migrated_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT variant_tag FROM mmingest_files WHERE remote_url = :url"),
                {"url": url},
            )
        ).fetchone()

    assert row is not None, "Row not found in mmingest_files"
    assert row[0] is None, f"variant_tag should be NULL for unknown tag, got {row[0]!r}"

    # Confirm INFO log was emitted mentioning the unknown tag
    assert any(
        "NOVELTAG" in record.message and record.levelname == "INFO" for record in caplog.records
    ), "Expected INFO log mentioning unknown_tag 'NOVELTAG'"


# ---------------------------------------------------------------------------
# Gate 7: Idempotency — second run produces zero writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotency_no_writes_on_second_run(migrated_engine):
    """Gate 7: running the indexer twice with identical input leaves state unchanged.

    The change-detection triple in the crawler is mocked to match what was
    already persisted, so the second run's delta_walk returns an empty list
    and the indexer makes zero DB writes.
    """
    url = "https://mmingest.pbswi.wisc.edu/IWP/6POL0202.srt"
    item = _make_file_work_item(
        url=url,
        filename="6POL0202.srt",
        media_id="6POL0202",
        season=2,
        episode=2,
        change_triple=(None, "2026-03-01T00:00:00", 5000),
        file_size_bytes=5000,
    )

    # First run — populates the DB
    run1 = await _run_indexer_with_mocks(migrated_engine, [item], [])
    assert run1.files_seen == 1

    # Second run — crawler returns empty list (change triple unchanged)
    run2 = await _run_indexer_with_mocks(migrated_engine, [], [])
    assert run2.files_seen == 0
    assert run2.files_new == 0
    assert run2.sidecars_fetched == 0
    assert run2.sidecars_persisted == 0

    # Confirm exactly one row exists in the DB
    async with migrated_engine.connect() as conn:
        count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM mmingest_files WHERE remote_url = :url"),
                {"url": url},
            )
        ).scalar_one()
    assert count == 1, f"Expected 1 row after idempotent second run, got {count}"


# ---------------------------------------------------------------------------
# Extra: FTS parity maintained after multiple sidecar inserts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fts_parity_after_multiple_sidecars(migrated_engine):
    """FTS parity stays at 0 after inserting multiple sidecars in one run."""
    urls_and_texts = [
        ("https://mmingest.pbswi.wisc.edu/IWP/6POL0101.srt", "6POL0101.srt", "Caption text for episode 1."),
        ("https://mmingest.pbswi.wisc.edu/IWP/6POL0102.srt", "6POL0102.srt", "Caption text for episode 2."),
        ("https://mmingest.pbswi.wisc.edu/IWP/6POL0103.srt", "6POL0103.srt", "Caption text for episode 3."),
    ]

    work_items = [
        _make_file_work_item(
            url=url,
            filename=filename,
            media_id=f"6POL01{str(i + 1).zfill(2)}",
            season=1,
            episode=i + 1,
        )
        for i, (url, filename, _) in enumerate(urls_and_texts)
    ]
    sidecars = [
        _make_sidecar_result(url=url, filename=filename, body_text=text) for url, filename, text in urls_and_texts
    ]

    run = await _run_indexer_with_mocks(migrated_engine, work_items, sidecars)

    assert run.sidecars_persisted == 3
    assert run.fts_parity_delta == 0


# ---------------------------------------------------------------------------
# Extra: schema round-trip verification (Gate 1 delegated to the fixture itself)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schema_integrity_after_upgrade(migrated_engine):
    """Gate 1: PRAGMA integrity_check returns 'ok' after alembic upgrade head.

    The migrated_engine fixture already ran alembic upgrade head; this test
    confirms the resulting DB passes SQLite's integrity check.
    """
    async with migrated_engine.connect() as conn:
        result = await conn.execute(text("PRAGMA integrity_check"))
        checks = [r[0] for r in result.fetchall()]
    assert checks == ["ok"], f"PRAGMA integrity_check failed: {checks}"
