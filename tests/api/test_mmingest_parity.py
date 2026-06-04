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
async def test_mmingest_files_columns(migrated_engine):
    """Migration 015: mmingest_files has all expected columns including variant lineage."""
    async with migrated_engine.connect() as conn:
        cols_row = await conn.execute(text("PRAGMA table_info(mmingest_files)"))
        col_names = {r[1] for r in cols_row.fetchall()}

    for expected in (
        "id", "remote_url", "directory_path", "filename",
        "media_id", "prefix", "prefix_category", "show_name",
        "season", "episode", "hd", "revision_date",
        "file_type", "file_size_bytes",
        "etag", "content_type", "remote_modified_at",
        "first_seen_at", "last_seen_at", "status",
        "variant_tag", "superseded_by",
        "airtable_record_id",
    ):
        assert expected in col_names, f"mmingest_files missing column: {expected}"


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


# ---------------------------------------------------------------------------
# Downgrade round-trip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_downgrade_round_trip():
    """upgrade head → downgrade 013 removes mmingest tables; available_files
    loses the four 014 columns; re-upgrade restores everything.
    """
    fd, db_path = tempfile.mkstemp(suffix="_downgrade_test.db")
    os.close(fd)

    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    env = {**os.environ, "DATABASE_PATH": db_path}

    def alembic(*args: str) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
        )

    try:
        alembic("upgrade", "head")

        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
        try:
            # Confirm mmingest tables exist at head
            async with engine.connect() as conn:
                tables_row = await conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
                tables_at_head = {r[0] for r in tables_row.fetchall()}
            assert "mmingest_files" in tables_at_head
            assert "mmingest_sidecars" in tables_at_head
            assert "consumer_keys" in tables_at_head
        finally:
            await engine.dispose()

        # Downgrade to 013
        alembic("downgrade", "013")

        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
        try:
            async with engine.connect() as conn:
                tables_row = await conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
                tables_at_013 = {r[0] for r in tables_row.fetchall()}

                # mmingest tables must be gone
                assert "mmingest_files" not in tables_at_013
                assert "mmingest_sidecars" not in tables_at_013
                assert "consumer_keys" not in tables_at_013

                # available_files must still exist (it predates 014)
                assert "available_files" in tables_at_013

                # The four columns added by 014 must be gone
                cols_row = await conn.execute(
                    text("PRAGMA table_info(available_files)")
                )
                col_names_at_013 = {r[1] for r in cols_row.fetchall()}
            for removed_col in ("etag", "content_type", "last_head_at", "probe_status"):
                assert removed_col not in col_names_at_013, (
                    f"Column {removed_col!r} should be absent after downgrade to 013"
                )
        finally:
            await engine.dispose()

        # Re-upgrade: everything must come back
        alembic("upgrade", "head")

        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
        try:
            async with engine.connect() as conn:
                tables_row = await conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
                tables_final = {r[0] for r in tables_row.fetchall()}
            assert "mmingest_files" in tables_final
            assert "mmingest_sidecars" in tables_final
            assert "consumer_keys" in tables_final
        finally:
            await engine.dispose()

    finally:
        try:
            os.unlink(db_path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Delete-trigger parity coverage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parity_delta_zero_after_delete(migrated_engine):
    """INSERT then DELETE via the normal table path keeps delta at 0.

    The AFTER DELETE trigger removes the FTS entry, so the index stays
    in sync with the base table.
    """
    from api.services.mmingest._db import fts_parity_delta

    async with migrated_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO mmingest_files (remote_url, filename, file_type)
                VALUES ('http://example.com/delete_test.srt',
                        'delete_test.srt', 'srt')
                """
            )
        )
        file_id = (
            await conn.execute(text("SELECT last_insert_rowid()"))
        ).scalar_one()

        await conn.execute(
            text(
                """
                INSERT INTO mmingest_sidecars (file_id, kind, body_text)
                VALUES (:fid, 'srt', 'Content that will be deleted.')
                """
            ),
            {"fid": file_id},
        )
        sidecar_id = (
            await conn.execute(text("SELECT last_insert_rowid()"))
        ).scalar_one()

    # Confirm delta is 0 after insert
    async with migrated_engine.connect() as conn:
        assert await fts_parity_delta(conn) == 0

    # Delete via normal path — AFTER DELETE trigger should clean FTS
    async with migrated_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM mmingest_sidecars WHERE id = :sid"),
            {"sid": sidecar_id},
        )

    async with migrated_engine.connect() as conn:
        assert await fts_parity_delta(conn) == 0


@pytest.mark.asyncio
async def test_parity_delta_detects_divergence(migrated_engine):
    """Direct DELETE on the base table that bypasses triggers leaves FTS
    with a phantom row: delta == -1 (FTS has more rows than base).
    """
    from api.services.mmingest._db import fts_parity_delta

    async with migrated_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO mmingest_files (remote_url, filename, file_type)
                VALUES ('http://example.com/phantom_test.srt',
                        'phantom_test.srt', 'srt')
                """
            )
        )
        file_id = (
            await conn.execute(text("SELECT last_insert_rowid()"))
        ).scalar_one()

        await conn.execute(
            text(
                """
                INSERT INTO mmingest_sidecars (file_id, kind, body_text)
                VALUES (:fid, 'srt', 'Phantom row body text.')
                """
            ),
            {"fid": file_id},
        )
        sidecar_id = (
            await conn.execute(text("SELECT last_insert_rowid()"))
        ).scalar_one()

    async with migrated_engine.connect() as conn:
        assert await fts_parity_delta(conn) == 0

    # Bypass triggers: delete from the _docsize shadow table directly to
    # simulate the base table being pruned without an FTS delete command.
    # (Mutating the shadow table is the cleanest way to create the
    # phantom-row scenario without disabling triggers.)
    async with migrated_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM mmingest_sidecars WHERE id = :sid"),
            {"sid": sidecar_id},
        )
        # Re-insert the _docsize row to simulate a phantom FTS entry that
        # the trigger somehow missed (trigger disabled / direct DB edit).
        await conn.execute(
            text(
                "INSERT INTO mmingest_sidecars_fts_docsize(id, sz) VALUES (:sid, 4)"
            ),
            {"sid": sidecar_id},
        )

    async with migrated_engine.connect() as conn:
        delta = await fts_parity_delta(conn)
    # base=0, fts=1 → delta = 0 - 1 = -1
    assert delta == -1, f"Expected -1 (phantom FTS row), got {delta}"


@pytest.mark.asyncio
async def test_parity_delta_returns_none_before_migration_016():
    """fts_parity_delta returns None when called against a DB at 013.

    Simulates the deploy-window scenario: app restarted before migration
    016 has been applied.
    """
    from api.services.mmingest._db import fts_parity_delta

    fd, db_path = tempfile.mkstemp(suffix="_pre016_test.db")
    os.close(fd)

    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    env = {**os.environ, "DATABASE_PATH": db_path}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "013"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic upgrade 013 failed:\n{result.stdout}\n{result.stderr}"
    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    try:
        async with engine.connect() as conn:
            result = await fts_parity_delta(conn)
        assert result is None, (
            f"Expected None for pre-016 DB, got {result!r}"
        )
    finally:
        await engine.dispose()
        try:
            os.unlink(db_path)
        except Exception:
            pass
