"""mmingest crawler scheduler — Sprint 2.

APScheduler wiring for the mmingest incremental delta crawler + indexer.
Follows the pattern established by api/services/ingest_scheduler.py.

Jobs registered here:
  * ``mmingest_delta_walk`` — hourly directory delta walk + full index pass
    via MmingestIndexer.run_once().

Design notes:
  * The scheduler is REGISTERED but NOT STARTED by this module.  The app
    startup path calls ``start_mmingest_scheduler()`` to activate it.
  * ``run_delta_walk()`` is the scheduler entry point; it instantiates
    MmingestIndexer, supplies the DB engine, and calls run_once().
  * The DB engine is resolved lazily from api.services.database at call
    time so that the scheduler module can be imported before the engine
    is created (e.g. in tests that patch the engine).
"""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global scheduler instance (module-level singleton, mirroring ingest_scheduler)
# ---------------------------------------------------------------------------

_scheduler: Optional[AsyncIOScheduler] = None


def get_mmingest_scheduler() -> AsyncIOScheduler:
    """Return (or create) the global mmingest scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


# ---------------------------------------------------------------------------
# Job functions
# ---------------------------------------------------------------------------


async def run_delta_walk() -> None:
    """Execute one incremental delta walk + index pass of the mmingest server.

    Called by APScheduler on the configured interval.  Instantiates
    MmingestIndexer with the application's async engine and calls run_once()
    so that discovered files are persisted to mmingest_files and sidecars are
    written to mmingest_sidecars (FTS5 synced via migration 016 triggers).

    The DB engine is imported lazily to avoid circular-import issues at
    module load time.
    """
    logger.info("mmingest delta walk + index starting")

    try:
        # Import lazily to avoid circular imports at module load time.
        # _engine is the module-level singleton created by init_db(); if the
        # scheduler fires before init_db() completes, the engine will be None
        # and we log an error rather than crashing.
        import api.services.database as _db_module
        from api.services.mmingest.indexer import MmingestIndexer

        engine = _db_module._engine
        if engine is None:
            logger.error(
                "mmingest indexer: DB engine not initialised (init_db() not yet called). " "Skipping this run."
            )
            return

        indexer = MmingestIndexer(
            engine=engine,
            base_url="https://mmingest.pbswi.wisc.edu/",
            directories=["/"],
            max_concurrent=4,
            rate_per_second=2.0,
        )

        run = await indexer.run_once()

        logger.info(
            "mmingest delta walk complete: files_seen=%d files_new=%d "
            "sidecars_fetched=%d sidecars_persisted=%d fts_delta=%s elapsed=%.1fs",
            run.files_seen,
            run.files_new,
            run.sidecars_fetched,
            run.sidecars_persisted,
            run.fts_parity_delta,
            run.elapsed_seconds,
        )

    except RuntimeError as exc:
        # Pause-window suppression is a normal operational event, not a failure.
        logger.info("mmingest delta walk suppressed: %s", exc)
    except Exception:
        logger.exception("mmingest delta walk failed")


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------


def configure_mmingest_jobs(
    delta_walk_interval_hours: int = 1,
) -> None:
    """Register mmingest jobs on the scheduler.

    Jobs are registered but the scheduler is NOT started here — call
    ``start_mmingest_scheduler()`` to activate.  Safe to call multiple times
    (uses ``replace_existing=True``).

    Args:
        delta_walk_interval_hours: How often to run the directory delta walk.
                                   Default: 1 hour.
    """
    scheduler = get_mmingest_scheduler()

    # --- Hourly delta walk ---
    scheduler.add_job(
        run_delta_walk,
        trigger=IntervalTrigger(hours=delta_walk_interval_hours),
        id="mmingest_delta_walk",
        name="mmingest Incremental Delta Walk",
        replace_existing=True,
    )
    logger.info(
        "mmingest delta walk job registered (interval: %dh)",
        delta_walk_interval_hours,
    )

    # --- Continuous sidecar enqueue (activated by S2) ---
    # The mmingest_delta_walk job already fetches sidecars inline during each
    # run_once() pass (MmingestIndexer._persist_sidecars).  The separate
    # continuous-enqueue job is not needed in this sprint; the inline path
    # handles the sidecar backfill correctly.  If future sprints need an
    # independent retry queue (e.g. for large sidecar backlogs), add a
    # separate job here following the same pattern.
    logger.debug("mmingest sidecar enqueue: handled inline by MmingestIndexer.run_once()")


async def start_mmingest_scheduler(
    delta_walk_interval_hours: int = 1,
) -> None:
    """Configure jobs and start the mmingest scheduler.

    Called by S2 during application startup.

    Args:
        delta_walk_interval_hours: Interval for the delta walk job.
    """
    scheduler = get_mmingest_scheduler()
    configure_mmingest_jobs(delta_walk_interval_hours=delta_walk_interval_hours)

    if not scheduler.running:
        scheduler.start()
        logger.info("mmingest scheduler started")
    else:
        logger.debug("mmingest scheduler already running; jobs reconfigured")


async def stop_mmingest_scheduler() -> None:
    """Stop the mmingest scheduler gracefully.

    Called during application shutdown.
    """
    scheduler = get_mmingest_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("mmingest scheduler stopped")
