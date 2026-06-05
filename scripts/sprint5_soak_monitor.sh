#!/usr/bin/env bash
# Sprint 5 Staging Soak — Background Monitor
#
# Drives one MmingestIndexer pass against /wisconsinlife/ at depth 1,
# then repeats the parity check every 6 hours for 24 hours.
#
# Telemetry is appended to /tmp/sprint5-soak-log.jsonl as JSONL.
#
# Usage:
#   bash scripts/sprint5_soak_monitor.sh
#   # OR with nohup:
#   nohup bash scripts/sprint5_soak_monitor.sh > /tmp/sprint5-monitor.log 2>&1 &
#   echo $! > /tmp/sprint5-monitor.pid
#
# Configuration (override via environment):
#   CARDIGAN_DIR      Path to cardigan-v4 checkout (default: directory containing this script's parent)
#   DATABASE_PATH     SQLite DB path (default: $CARDIGAN_DIR/dashboard.db)
#   SOAK_LOG          Output JSONL path (default: /tmp/sprint5-soak-log.jsonl)
#   SOAK_HOURS        Total soak duration in hours (default: 24)
#   PARITY_INTERVAL   Parity check interval in hours (default: 6)
#   MMINGEST_BASE_URL mmingest server URL (default: https://mmingest.pbswi.wisc.edu/)
#
# Requirements:
#   - Python 3.13+ with venv at $CARDIGAN_DIR/venv/ or $CARDIGAN_DIR/.venv/
#   - Cardigan dependencies installed (httpx, sqlalchemy[asyncio], aiosqlite)
#   - VPN/campus tunnel active (mmingest is campus-only)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CARDIGAN_DIR="${CARDIGAN_DIR:-$(dirname "$SCRIPT_DIR")}"
DATABASE_PATH="${DATABASE_PATH:-$CARDIGAN_DIR/dashboard.db}"
SOAK_LOG="${SOAK_LOG:-/tmp/sprint5-soak-log.jsonl}"
SOAK_HOURS="${SOAK_HOURS:-24}"
PARITY_INTERVAL="${PARITY_INTERVAL:-6}"
MMINGEST_BASE_URL="${MMINGEST_BASE_URL:-https://mmingest.pbswi.wisc.edu/}"

# ---------------------------------------------------------------------------
# Activate virtual environment
# ---------------------------------------------------------------------------

VENV_PATH=""
if [ -d "$CARDIGAN_DIR/venv" ]; then
    VENV_PATH="$CARDIGAN_DIR/venv"
elif [ -d "$CARDIGAN_DIR/.venv" ]; then
    VENV_PATH="$CARDIGAN_DIR/.venv"
else
    echo "ERROR: No virtual environment found at $CARDIGAN_DIR/venv or $CARDIGAN_DIR/.venv" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "$VENV_PATH/bin/activate"

PYTHON="$VENV_PATH/bin/python3"
echo "Using Python: $($PYTHON --version)"
echo "Soak log: $SOAK_LOG"
echo "Cardigan dir: $CARDIGAN_DIR"
echo "Database: $DATABASE_PATH"
echo "Duration: ${SOAK_HOURS}h, parity check every ${PARITY_INTERVAL}h"

# ---------------------------------------------------------------------------
# Inline Python helper — run one indexer pass and emit JSONL telemetry
# ---------------------------------------------------------------------------
# The Python script below is written to a temp file and executed directly
# so it can import Cardigan's own modules from CARDIGAN_DIR.
# ---------------------------------------------------------------------------

RUNNER_PY="$(mktemp /tmp/sprint5_runner_XXXXXX.py)"
trap 'rm -f "$RUNNER_PY"' EXIT

cat > "$RUNNER_PY" << 'PYEOF'
"""Sprint 5 soak monitor — Python runner.

Called by sprint5_soak_monitor.sh with these environment variables:
  CARDIGAN_DIR, DATABASE_PATH, SOAK_LOG, MMINGEST_BASE_URL,
  RUNNER_MODE (indexer_run | parity_check)

Appends one or more JSONL records to SOAK_LOG.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Prepend CARDIGAN_DIR to sys.path so Cardigan modules are importable.
cardigan_dir = os.environ["CARDIGAN_DIR"]
sys.path.insert(0, cardigan_dir)
os.chdir(cardigan_dir)

# Override DATABASE_PATH for Cardigan's database module.
os.environ.setdefault("DATABASE_PATH", os.environ.get("DATABASE_PATH", "dashboard.db"))

soak_log = Path(os.environ["SOAK_LOG"])
base_url = os.environ.get("MMINGEST_BASE_URL", "https://mmingest.pbswi.wisc.edu/")
mode = os.environ.get("RUNNER_MODE", "indexer_run")
db_path = Path(os.environ.get("DATABASE_PATH", "dashboard.db"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(record: dict) -> None:
    record.setdefault("ts", now_iso())
    line = json.dumps(record)
    with soak_log.open("a") as f:
        f.write(line + "\n")
    print(line, flush=True)


# ---------------------------------------------------------------------------
# Instrumented MmingestCrawler wrapper
# ---------------------------------------------------------------------------

class _TelemetryCrawler:
    """Wraps MmingestCrawler to collect HTTP telemetry during delta_walk."""

    def __init__(self, **kwargs) -> None:
        from api.services.mmingest.crawler import MmingestCrawler, TokenBucket
        from datetime import time as dtime

        # Pause window: 11:00–15:00 UTC = 06:00–10:00 CDT
        pause_start = dtime(11, 0)
        pause_end   = dtime(15, 0)

        self._crawler = MmingestCrawler(
            base_url=kwargs.get("base_url", base_url),
            max_concurrent=kwargs.get("max_concurrent", 4),
            rate_per_second=kwargs.get("rate_per_second", 1.0),
            max_depth=kwargs.get("max_depth", 1),
            pause_window=(pause_start, pause_end),
        )

        # Telemetry accumulators (monkey-patched into the crawler's HTTP layer)
        self.req_total   = 0
        self.req_2xx     = 0
        self.req_5xx     = 0
        self.req_errors  = 0
        self.latencies_ms: list[float] = []
        self.peak_inflight = 0
        self._inflight  = 0
        self._resp_sizes_bytes: list[int] = []

        # Patch _fetch_with_backoff to record telemetry
        original_fetch = self._crawler._fetch_with_backoff.__func__

        crawler_ref = self._crawler
        telem = self

        async def _instrumented_fetch(self_inner, client, semaphore, url, max_retries=4):
            telem.req_total += 1
            telem._inflight += 1
            if telem._inflight > telem.peak_inflight:
                telem.peak_inflight = telem._inflight
            t0 = time.monotonic()
            try:
                result = await original_fetch(self_inner, client, semaphore, url, max_retries)
                latency_ms = (time.monotonic() - t0) * 1000
                telem.latencies_ms.append(latency_ms)
                if result is not None:
                    telem.req_2xx += 1
                    telem._resp_sizes_bytes.append(len(result.encode("utf-8", errors="replace")))
                return result
            except Exception:
                telem.req_errors += 1
                raise
            finally:
                telem._inflight -= 1

        import types
        self._crawler._fetch_with_backoff = types.MethodType(_instrumented_fetch, self._crawler)

    async def delta_walk(self, directories, known=None) -> list:
        return await self._crawler.delta_walk(directories=directories, known=known)


# ---------------------------------------------------------------------------
# Parity check only (no crawl)
# ---------------------------------------------------------------------------

async def run_parity_check() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from api.services.mmingest._db import fts_parity_delta

    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(db_url, echo=False)

    async with engine.connect() as conn:
        delta = await fts_parity_delta(conn)

        # Count mmingest_files rows for prefix='2WLI'
        from sqlalchemy import text
        files_row = await conn.execute(text(
            "SELECT COUNT(*) FROM mmingest_files WHERE prefix LIKE '2WLI%'"
        ))
        files_count: int = files_row.scalar_one()

        sidecars_row = await conn.execute(text(
            "SELECT COUNT(*) FROM mmingest_sidecars AS s "
            "JOIN mmingest_files AS mf ON mf.id = s.file_id "
            "WHERE mf.prefix LIKE '2WLI%'"
        ))
        sidecars_count: int = sidecars_row.scalar_one()

    await engine.dispose()

    emit({
        "event": "parity_check",
        "fts_delta": delta,
        "mmingest_files_count_2wli": files_count,
        "mmingest_sidecars_count_2wli": sidecars_count,
        "msg": f"FTS delta={delta}; 2WLI files={files_count}; 2WLI sidecars={sidecars_count}",
    })


# ---------------------------------------------------------------------------
# Full indexer run
# ---------------------------------------------------------------------------

async def run_indexer() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from api.services.mmingest._db import fts_parity_delta
    from api.services.mmingest.indexer import MmingestIndexer
    from api.services.mmingest.crawler import ChangeTriple
    from sqlalchemy import text

    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(db_url, echo=False)

    t0 = time.monotonic()

    try:
        # Check pause window before starting
        from datetime import time as dtime, datetime as dt, timezone
        pause_start = dtime(11, 0)
        pause_end   = dtime(15, 0)
        now_utc = dt.now(timezone.utc).time()
        in_window = pause_start <= now_utc <= pause_end
        if in_window:
            emit({
                "event": "pause_window",
                "window_start": "11:00 UTC",
                "window_end": "15:00 UTC",
                "now_utc": now_utc.strftime("%H:%M"),
                "msg": "Crawl paused — inside broadcast peak window (06:00–10:00 CDT)",
            })
            return

        # Load known state for change detection
        async with engine.connect() as conn:
            known_rows = (await conn.execute(text(
                "SELECT remote_url, etag, remote_modified_at, file_size_bytes "
                "FROM mmingest_files WHERE directory_path LIKE '/wisconsinlife/%'"
            ))).fetchall()

        known: dict[str, ChangeTriple] = {
            row[0]: (row[1], row[2], row[3]) for row in known_rows
        }

        # Instrumented crawl
        telem_crawler = _TelemetryCrawler(
            base_url=base_url,
            max_concurrent=4,
            rate_per_second=1.0,
            max_depth=1,
        )
        work_items = await telem_crawler.delta_walk(
            directories=["/wisconsinlife/"],
            known=known,
        )

        files_seen = len(work_items)
        elapsed_crawl = time.monotonic() - t0

        # Persist via indexer (using internal methods for soak isolation)
        indexer = MmingestIndexer(
            engine=engine,
            base_url=base_url,
            directories=["/wisconsinlife/"],
            max_concurrent=4,
            rate_per_second=1.0,
        )

        # Upsert and fetch sidecars
        from api.services.mmingest.indexer import _SIDECAR_FILE_TYPES
        from api.services.mmingest.sidecar_fetcher import SidecarFetcher

        files_new = 0
        sidecars_fetched = 0
        sidecars_persisted = 0
        url_to_id: dict[str, int] = {}

        if work_items:
            async with engine.begin() as conn:
                url_to_id = await indexer._upsert_files(conn, work_items)
            files_new = len(work_items)

            async with engine.begin() as conn:
                await indexer._apply_variant_lineage(conn, work_items, url_to_id)

            sidecar_items = [wi for wi in work_items if wi.file_type in _SIDECAR_FILE_TYPES]
            if sidecar_items:
                fetcher = SidecarFetcher()
                fetch_inputs = [(wi.url, url_to_id.get(wi.url)) for wi in sidecar_items]
                results = await fetcher.fetch_many(
                    urls=fetch_inputs,
                    max_concurrent=4,
                )
                sidecars_fetched = len(results)
                ok_results = [r for r in results if r.ok]
                sidecars_persisted = len(ok_results)

                if ok_results:
                    async with engine.begin() as conn:
                        await indexer._persist_sidecars(conn, ok_results)

        # Parity check
        async with engine.connect() as conn:
            fts_delta = await fts_parity_delta(conn)

        # Latency percentiles
        lats = sorted(telem_crawler.latencies_ms)
        def pct(p: float) -> float:
            if not lats:
                return 0.0
            idx = int(len(lats) * p / 100)
            return round(lats[min(idx, len(lats)-1)], 1)

        elapsed_total = round(time.monotonic() - t0, 1)

        emit({
            "event": "indexer_run",
            "files_seen": files_seen,
            "files_new": files_new,
            "sidecars_fetched": sidecars_fetched,
            "sidecars_persisted": sidecars_persisted,
            "fts_parity_delta": fts_delta,
            "elapsed_s": elapsed_total,
            # HTTP telemetry
            "req_total": telem_crawler.req_total,
            "req_2xx": telem_crawler.req_2xx,
            "req_5xx": telem_crawler.req_5xx,
            "req_errors": telem_crawler.req_errors,
            "p50_latency_ms": pct(50),
            "p95_latency_ms": pct(95),
            "p99_latency_ms": pct(99),
            "peak_inflight": telem_crawler.peak_inflight,
            # Queue depth: captured at end of run (items remaining in queue = 0 if complete)
            "queue_final_size": 0,  # crawl is synchronous; queue drains before return
            "msg": (
                f"files_seen={files_seen} files_new={files_new} "
                f"sidecars={sidecars_persisted} fts_delta={fts_delta} "
                f"req_total={telem_crawler.req_total} "
                f"peak_inflight={telem_crawler.peak_inflight} "
                f"p95={pct(95)}ms elapsed={elapsed_total}s"
            ),
        })

    except RuntimeError as exc:
        # Pause window raised by crawler
        emit({
            "event": "pause_window",
            "msg": str(exc),
        })
    except Exception as exc:
        emit({
            "event": "error",
            "exc_type": type(exc).__name__,
            "msg": str(exc),
        })
        raise
    finally:
        await engine.dispose()


if mode == "parity_check":
    asyncio.run(run_parity_check())
else:
    asyncio.run(run_indexer())
PYEOF

# ---------------------------------------------------------------------------
# Soak loop
# ---------------------------------------------------------------------------

SOAK_END=$(( $(date +%s) + SOAK_HOURS * 3600 ))
PARITY_INTERVAL_SECS=$(( PARITY_INTERVAL * 3600 ))
NEXT_PARITY=$(( $(date +%s) + PARITY_INTERVAL_SECS ))

echo "Soak started at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "Soak ends at $(date -u -r "$SOAK_END" '+%Y-%m-%dT%H:%M:%SZ')"
echo "First parity check at $(date -u -r "$NEXT_PARITY" '+%Y-%m-%dT%H:%M:%SZ')"

# Emit soak-start event
python3 - << STARTEOF
import json, os
from datetime import datetime, timezone
soak_log = os.environ.get("SOAK_LOG", "/tmp/sprint5-soak-log.jsonl")
with open(soak_log, "a") as f:
    f.write(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "soak_start",
        "config": {
            "directory": "/wisconsinlife/",
            "depth": 1,
            "prefix": "2WLI",
            "max_concurrent": 4,
            "rate_per_second": 1.0,
            "pause_window_utc": "11:00-15:00",
            "soak_hours": int(os.environ.get("SOAK_HOURS", 24)),
            "parity_interval_hours": int(os.environ.get("PARITY_INTERVAL", 6)),
        },
        "msg": "Sprint 5 soak started",
    }) + "\n")
STARTEOF

# Initial indexer run (T+0h)
echo "Running initial indexer pass..."
CARDIGAN_DIR="$CARDIGAN_DIR" \
DATABASE_PATH="$DATABASE_PATH" \
SOAK_LOG="$SOAK_LOG" \
MMINGEST_BASE_URL="$MMINGEST_BASE_URL" \
RUNNER_MODE="indexer_run" \
PYTHONPATH="$CARDIGAN_DIR:${PYTHONPATH:-}" \
"$PYTHON" "$RUNNER_PY"

# Soak loop: sleep 1h, check if parity due, repeat
while [ "$(date +%s)" -lt "$SOAK_END" ]; do
    echo "Sleeping 1h... (next wakeup at $(date -u -r "$(( $(date +%s) + 3600 ))" '+%H:%M UTC'))"
    sleep 3600

    NOW=$(date +%s)
    [ "$NOW" -ge "$SOAK_END" ] && break

    # Parity check
    if [ "$NOW" -ge "$NEXT_PARITY" ]; then
        echo "Running parity check at $(date -u '+%Y-%m-%dT%H:%M:%SZ')..."
        CARDIGAN_DIR="$CARDIGAN_DIR" \
        DATABASE_PATH="$DATABASE_PATH" \
        SOAK_LOG="$SOAK_LOG" \
        MMINGEST_BASE_URL="$MMINGEST_BASE_URL" \
        RUNNER_MODE="parity_check" \
        PYTHONPATH="$CARDIGAN_DIR:${PYTHONPATH:-}" \
        "$PYTHON" "$RUNNER_PY"
        NEXT_PARITY=$(( NOW + PARITY_INTERVAL_SECS ))
    fi

    # Also run a delta indexer pass each hour
    echo "Running hourly indexer pass at $(date -u '+%Y-%m-%dT%H:%M:%SZ')..."
    CARDIGAN_DIR="$CARDIGAN_DIR" \
    DATABASE_PATH="$DATABASE_PATH" \
    SOAK_LOG="$SOAK_LOG" \
    MMINGEST_BASE_URL="$MMINGEST_BASE_URL" \
    RUNNER_MODE="indexer_run" \
    PYTHONPATH="$CARDIGAN_DIR:${PYTHONPATH:-}" \
    "$PYTHON" "$RUNNER_PY"
done

# Final parity check at T+24h
echo "Final parity check at $(date -u '+%Y-%m-%dT%H:%M:%SZ')..."
CARDIGAN_DIR="$CARDIGAN_DIR" \
DATABASE_PATH="$DATABASE_PATH" \
SOAK_LOG="$SOAK_LOG" \
MMINGEST_BASE_URL="$MMINGEST_BASE_URL" \
RUNNER_MODE="parity_check" \
PYTHONPATH="$CARDIGAN_DIR:${PYTHONPATH:-}" \
"$PYTHON" "$RUNNER_PY"

# Emit soak-end event
python3 - << ENDEOF
import json, os
from datetime import datetime, timezone
soak_log = os.environ.get("SOAK_LOG", "/tmp/sprint5-soak-log.jsonl")
with open(soak_log, "a") as f:
    f.write(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "soak_end",
        "msg": "Sprint 5 soak complete — run sprint5_soak_report.py to generate GO/NO-GO report",
    }) + "\n")
ENDEOF

echo "Soak complete. Run: python3 scripts/sprint5_soak_report.py to generate the report."
