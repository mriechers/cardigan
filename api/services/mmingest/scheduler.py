"""mmingest crawler scheduler — Sprint 1B.

APScheduler wiring for the mmingest incremental delta crawler.  Follows the
pattern established by api/services/ingest_scheduler.py.

Jobs registered here:
  * ``mmingest_delta_walk`` — hourly directory delta walk; emits FileWorkItem
    results to be consumed by S2's indexer.

Design notes:
  * The scheduler is REGISTERED but NOT STARTED by this module.  S2 calls
    ``start_mmingest_scheduler()`` to activate it during app startup.
  * No DB writes happen here — the crawler outputs in-memory work items.
    S2 is responsible for wiring those to the indexer.
  * The continuous sidecar/MP4 enqueue job is stubbed here; S2 fleshes it
    out once it can supply the list of pending files to fetch.
"""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from api.services.mmingest.crawler import MmingestCrawler

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
    """Execute one incremental delta walk of the mmingest server.

    Called by APScheduler on the configured interval.  Emits work items to
    the logger (placeholder) — S2 replaces this with real indexer dispatch.
    """
    logger.info("mmingest delta walk starting")

    try:
        crawler = MmingestCrawler(
            base_url="https://mmingest.pbswi.wisc.edu/",
            max_concurrent=4,
            rate_per_second=2.0,
        )

        # S2 supplies ``known`` from the database; for now walk returns all files.
        work_items = await crawler.delta_walk(directories=["/"])

        sidecar_count = sum(1 for wi in work_items if wi.lane == "sidecar")
        primary_count = sum(1 for wi in work_items if wi.lane == "primary")

        logger.info(
            "mmingest delta walk complete: %d work items " "(%d sidecar, %d primary)",
            len(work_items),
            sidecar_count,
            primary_count,
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

    # --- Continuous sidecar enqueue (stub — S2 activates) ---
    # S2 will add a job here that:
    #   1. Queries mmingest_files for srt/scc with status='new'
    #   2. Dispatches SidecarFetcher.fetch_many()
    #   3. Writes results to mmingest_sidecars
    # Placeholder logged so S2 knows the hook point.
    logger.debug("mmingest continuous sidecar enqueue: STUB — S2 activates this job")


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
