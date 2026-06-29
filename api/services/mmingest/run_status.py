"""Persistence helpers for mmingest crawl-run telemetry.

One row per delta-walk pass is written to ``mmingest_crawl_runs`` (migration
021).  The scheduler records a ``running`` row when a pass starts and updates it
to a terminal status when the pass finishes — so ``GET /api/mmingest/status``
can report the last run's outcome and counts.

Design notes
------------
  * These helpers own the telemetry table so the indexer's ``run_once()`` stays
    free of telemetry concerns (and its tests don't need the new table).
  * ``record_run_start`` / ``record_run_finish`` are best-effort: telemetry must
    never crash a crawl.  Callers may wrap them, but the functions themselves
    only touch a single small table and re-raise nothing the caller can't see.
  * ``read_status`` preflights ``sqlite_master`` so it returns ``None`` rather
    than raising during the deploy window before migration 021 applies.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

if TYPE_CHECKING:
    from api.services.mmingest.indexer import IndexerRun

logger = logging.getLogger(__name__)


async def record_run_start(engine: AsyncEngine) -> Optional[int]:
    """Insert a ``running`` row and return its id (or None on failure).

    Failure to record telemetry is logged but never propagated — a crawl must
    not die because its bookkeeping table is missing or locked.
    """
    started = datetime.now(timezone.utc).isoformat()
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    INSERT INTO mmingest_crawl_runs (started_at, status)
                    VALUES (:started_at, 'running')
                """),
                {"started_at": started},
            )
            # SQLite: lastrowid is populated on the CursorResult.
            run_id = result.lastrowid
        return int(run_id) if run_id is not None else None
    except Exception:
        logger.exception("mmingest telemetry: failed to record run start")
        return None


async def record_run_finish(
    engine: AsyncEngine,
    run_id: Optional[int],
    *,
    status: str,
    run: Optional["IndexerRun"] = None,
    error: Optional[str] = None,
) -> None:
    """Update a run row to a terminal ``status`` with counts from ``run``.

    No-op when ``run_id`` is None (start was never recorded).  Best-effort:
    telemetry failures are logged, never raised.

    Args:
        run_id:  id returned by record_run_start (None disables the update).
        status:  'completed' | 'suppressed' | 'failed'.
        run:     IndexerRun summary; its counts are persisted when present.
        error:   Error string for failed/suppressed runs.
    """
    if run_id is None:
        return

    finished = datetime.now(timezone.utc).isoformat()
    params: dict[str, Any] = {
        "id": run_id,
        "finished_at": finished,
        "status": status,
        "files_seen": run.files_seen if run else None,
        "files_new": run.files_new if run else None,
        "sidecars_fetched": run.sidecars_fetched if run else None,
        "sidecars_persisted": run.sidecars_persisted if run else None,
        "fts_parity_delta": run.fts_parity_delta if run else None,
        "elapsed_seconds": run.elapsed_seconds if run else None,
        "error": error,
    }
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    UPDATE mmingest_crawl_runs SET
                        finished_at        = :finished_at,
                        status             = :status,
                        files_seen         = :files_seen,
                        files_new          = :files_new,
                        sidecars_fetched   = :sidecars_fetched,
                        sidecars_persisted = :sidecars_persisted,
                        fts_parity_delta   = :fts_parity_delta,
                        elapsed_seconds    = :elapsed_seconds,
                        error              = :error
                    WHERE id = :id
                """),
                params,
            )
    except Exception:
        logger.exception("mmingest telemetry: failed to record run finish for run_id=%s", run_id)


async def read_status(engine: AsyncEngine) -> Optional[dict[str, Any]]:
    """Return the most recent crawl run as a dict, plus a ``running`` flag.

    Returns None if the telemetry table does not yet exist (pre-migration 021)
    or no run has been recorded.  The shape is:

        {
            "last_run": { ... row fields ... } | None,
            "running": bool,
        }
    """
    async with engine.connect() as conn:
        # Preflight: table may not exist during the deploy window before 021.
        exists = await conn.execute(text("""
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table' AND name = 'mmingest_crawl_runs'
        """))
        if exists.scalar_one() < 1:
            return None

        row = (await conn.execute(text("""
            SELECT id, started_at, finished_at, status,
                   files_seen, files_new, sidecars_fetched, sidecars_persisted,
                   fts_parity_delta, elapsed_seconds, error
            FROM   mmingest_crawl_runs
            ORDER  BY id DESC
            LIMIT  1
        """))).fetchone()

        running = (await conn.execute(text("""
            SELECT COUNT(*) FROM mmingest_crawl_runs
            WHERE status = 'running' AND finished_at IS NULL
        """))).scalar_one()

    last_run = dict(row._mapping) if row is not None else None
    return {"last_run": last_run, "running": bool(running)}
