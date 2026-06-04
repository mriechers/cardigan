"""Tests for api.services.mmingest._db.fts_parity_delta.

Uses an isolated SQLite DB with all migrations applied via alembic so the
FTS5 virtual table and its sync triggers exist exactly as they would in
production.
"""

import os
import subprocess
import sys
import tempfile

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest_asyncio.fixture
async def migrated_engine():
    """Stand up a fresh DB via `alembic upgrade head`, return an async engine.

    Runs alembic as a subprocess so the FTS5 virtual table + triggers are
    created exactly as in production (SQLAlchemy metadata.create_all does
    not model virtual tables).
    """
    fd, db_path = tempfile.mkstemp(suffix="_parity_test.db")
    os.close(fd)

    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    repo_root = os.path.abspath(repo_root)

    env = {**os.environ, "DATABASE_PATH": db_path}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"
    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    yield engine

    await engine.dispose()
    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_parity_delta_zero_on_empty_db(migrated_engine):
    """An empty DB has 0 sidecars and 0 FTS rows — delta must be 0."""
    from api.services.mmingest._db import fts_parity_delta

    async with migrated_engine.connect() as conn:
        delta = await fts_parity_delta(conn)
    assert delta == 0


@pytest.mark.asyncio
async def test_parity_delta_zero_after_insert(migrated_engine):
    """Inserting a sidecar via the normal path keeps delta at 0.

    The AFTER INSERT trigger on mmingest_sidecars should populate the FTS
    index immediately, so parity is maintained.
    """
    from api.services.mmingest._db import fts_parity_delta

    async with migrated_engine.begin() as conn:
        # Insert a parent mmingest_files row first (FK constraint)
        await conn.execute(
            text(
                """
                INSERT INTO mmingest_files
                    (remote_url, filename, file_type)
                VALUES
                    ('http://example.com/test.srt', 'test.srt', 'srt')
                """
            )
        )
        file_id_row = await conn.execute(text("SELECT last_insert_rowid()"))
        file_id = file_id_row.scalar_one()

        # Insert a sidecar — trigger should update FTS
        await conn.execute(
            text(
                """
                INSERT INTO mmingest_sidecars (file_id, kind, body_text)
                VALUES (:file_id, 'srt', 'This is a test caption body.')
                """
            ),
            {"file_id": file_id},
        )

    async with migrated_engine.connect() as conn:
        delta = await fts_parity_delta(conn)
    assert delta == 0


@pytest.mark.asyncio
async def test_mmingest_schema_tables_exist(migrated_engine):
    """Smoke-test: all four migration targets are present after upgrade head."""
    async with migrated_engine.connect() as conn:
        tables_row = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        )
        tables = {r[0] for r in tables_row.fetchall()}

    assert "mmingest_files" in tables
    assert "mmingest_sidecars" in tables
    assert "consumer_keys" in tables
    # available_files must still exist (back-compat)
    assert "available_files" in tables


@pytest.mark.asyncio
async def test_available_files_new_columns_exist(migrated_engine):
    """Migration 014 added four columns to available_files."""
    async with migrated_engine.connect() as conn:
        cols_row = await conn.execute(text("PRAGMA table_info(available_files)"))
        col_names = {r[1] for r in cols_row.fetchall()}

    assert "etag" in col_names
    assert "content_type" in col_names
    assert "last_head_at" in col_names
    assert "probe_status" in col_names


@pytest.mark.asyncio
async def test_consumer_keys_columns(migrated_engine):
    """Migration 017: consumer_keys has the expected columns."""
    async with migrated_engine.connect() as conn:
        cols_row = await conn.execute(text("PRAGMA table_info(consumer_keys)"))
        col_names = {r[1] for r in cols_row.fetchall()}

    for expected in ("id", "key_hash", "label", "scopes", "created_at", "last_used_at"):
        assert expected in col_names, f"Missing column: {expected}"


@pytest.mark.asyncio
async def test_fts_match_join_returns_display_fields(migrated_engine):
    """FTS5 MATCH query joined to mmingest_files returns media_id/prefix/show_name.

    This is the read path the search feature uses.  The bug this test guards
    against: declaring UNINDEXED columns in the FTS5 DDL that don't exist on
    the content table (mmingest_sidecars) causes OperationalError at read time
    even though writes succeed.  The fix is to declare only body_text in the
    FTS5 table and JOIN to mmingest_files for display fields.
    """
    async with migrated_engine.begin() as conn:
        # Insert a parent mmingest_files row with known display fields
        await conn.execute(
            text(
                """
                INSERT INTO mmingest_files
                    (remote_url, filename, file_type, media_id, prefix, show_name)
                VALUES
                    ('http://example.com/search_test.srt', 'search_test.srt',
                     'srt', 'WLIA1234', 'wlia', 'Nature Hour')
                """
            )
        )
        file_id_row = await conn.execute(text("SELECT last_insert_rowid()"))
        file_id = file_id_row.scalar_one()

        await conn.execute(
            text(
                """
                INSERT INTO mmingest_sidecars (file_id, kind, body_text)
                VALUES (:file_id, 'srt',
                        'The red fox jumped over the lazy brown dog.')
                """
            ),
            {"file_id": file_id},
        )

    # The actual search query shape the downstream consumer will use.
    # If the FTS5 table still has phantom UNINDEXED columns this raises
    # OperationalError: no such column: T.media_id
    async with migrated_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT s.id,
                           s.file_id,
                           mf.media_id,
                           mf.prefix,
                           mf.show_name,
                           fts.rank
                    FROM   mmingest_sidecars_fts AS fts
                    JOIN   mmingest_sidecars     AS s   ON s.id  = fts.rowid
                    JOIN   mmingest_files        AS mf  ON mf.id = s.file_id
                    WHERE  mmingest_sidecars_fts MATCH 'fox'
                    ORDER  BY fts.rank
                    """
                )
            )
        ).fetchall()

    assert len(rows) == 1, f"Expected 1 FTS hit, got {len(rows)}"
    row = rows[0]
    assert row._mapping["media_id"] == "WLIA1234"
    assert row._mapping["prefix"] == "wlia"
    assert row._mapping["show_name"] == "Nature Hour"
    # rank is a negative float (BM25); just assert it's present and numeric
    assert row._mapping["rank"] is not None
    assert float(row._mapping["rank"]) < 0

    # Discriminating assertion: read body_text DIRECTLY off the FTS virtual
    # table (no JOIN).  Under the old DDL — which declared phantom UNINDEXED
    # columns media_id/prefix/show that don't exist on the content table —
    # this query raises:
    #   OperationalError: no such column: T.media_id
    # even when only body_text is selected, because FTS5 resolves ALL declared
    # columns from the content table when it opens the cursor.
    # Under the fixed DDL (body_text only) it succeeds.
    async with migrated_engine.connect() as conn:
        direct_rows = (
            await conn.execute(
                text(
                    "SELECT body_text FROM mmingest_sidecars_fts"
                    " WHERE mmingest_sidecars_fts MATCH 'fox'"
                )
            )
        ).fetchall()

    assert len(direct_rows) == 1
    assert "fox" in direct_rows[0][0]
