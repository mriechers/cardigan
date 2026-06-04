"""Database helpers for the mmingest service.

Parity check
------------
``fts_parity_delta`` returns the difference between the row count in the
``mmingest_sidecars`` base table and the row count stored in the FTS5
``_docsize`` shadow table (one row per indexed document).

  * Returns 0    → FTS index is in sync with the base table.
  * Returns N    → N base rows are missing from the FTS index.
  * Returns -N   → FTS has N phantom rows not present in the base table
                   (e.g. after a manual DELETE that bypassed triggers).
  * Returns None → migration 016 has not yet been applied; the tables do
                   not exist.  Callers should treat None as "not checkable"
                   rather than "in sync".  This can occur during the deploy
                   window between app restart and migration completion.

Usage::

    from sqlalchemy.ext.asyncio import AsyncConnection

    delta = await fts_parity_delta(conn)
    if delta is None:
        logger.info("FTS tables not yet migrated — skipping parity check")
    elif delta != 0:
        logger.warning("FTS out of sync by %d rows", delta)

The function is intentionally read-only and side-effect free; it is safe
to call from health-check endpoints or ops scripts.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def fts_parity_delta(conn: AsyncConnection) -> int | None:
    """Return the row-count delta between mmingest_sidecars and its FTS5 index.

    Preflights sqlite_master to detect the pre-migration state and returns
    None rather than raising OperationalError when the tables don't exist
    (e.g. deploy window between app restart and migration 016 completing).

    Args:
        conn: An active async SQLAlchemy connection.

    Returns:
        int:  ``len(mmingest_sidecars) - len(mmingest_sidecars_fts_docsize)``.
              0 means the FTS index is fully in sync.
        None: migration 016 has not been applied; tables are absent.
    """
    # Preflight: check both tables exist in sqlite_master before querying them.
    # Using sqlite_master avoids wrapping OperationalError (which could mask
    # genuine errors such as a corrupt FTS shadow table).
    # Note: SQLite registers FTS5 shadow tables (e.g. _docsize) with
    # type='table' — there is no distinct 'shadow' type in sqlite_master.
    exists_row = await conn.execute(
        text(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table'
              AND name IN ('mmingest_sidecars', 'mmingest_sidecars_fts_docsize')
            """
        )
    )
    if exists_row.scalar_one() < 2:
        return None

    base_count_row = await conn.execute(
        text("SELECT COUNT(*) FROM mmingest_sidecars")
    )
    base_count: int = base_count_row.scalar_one()

    # In FTS5 external-content mode the virtual table does NOT create a
    # _content shadow table (the content lives in mmingest_sidecars itself).
    # The _docsize shadow table has exactly one row per document present in
    # the index, making it the right counter for parity checks.
    fts_count_row = await conn.execute(
        text("SELECT COUNT(*) FROM mmingest_sidecars_fts_docsize")
    )
    fts_count: int = fts_count_row.scalar_one()

    return base_count - fts_count
