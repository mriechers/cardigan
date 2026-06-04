"""Database helpers for the mmingest service.

Parity check
------------
``fts_parity_delta`` returns the difference between the row count in the
``mmingest_sidecars`` base table and the row count stored in the FTS5
shadow table ``mmingest_sidecars_fts_content``.

  * Returns 0   → FTS index is in sync with the base table.
  * Returns N   → N base rows are missing from the FTS index.
  * Returns -N  → FTS has N phantom rows not present in the base table
                  (e.g. after a manual DELETE that bypassed triggers).

Usage::

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncConnection

    delta = await fts_parity_delta(conn)
    if delta != 0:
        logger.warning("FTS out of sync by %d rows", delta)

The function is intentionally read-only and side-effect free; it is safe
to call from health-check endpoints or ops scripts.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def fts_parity_delta(conn: AsyncConnection) -> int:
    """Return the row-count delta between mmingest_sidecars and its FTS5 index.

    Args:
        conn: An active async SQLAlchemy connection.

    Returns:
        int: ``len(mmingest_sidecars) - len(mmingest_sidecars_fts)``.
             0 means the FTS index is fully in sync.
    """
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
