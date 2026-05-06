"""One-shot backfill of v2.1-era data into the live DB.

Reads from a source SQLite DB (typically .snapshots/dashboard-v2.1-archive.db),
inserts its jobs/session_stats/chat_sessions rows into the live DB tagged
with the given app_version, and rewrites session_stats.job_id +
chat_sessions.job_id to the new IDs assigned by the live DB.

Idempotent: refuses to insert a job whose (app_version, project_path,
transcript_file, queued_at) tuple already exists in the live DB.

Usage:
    python -m scripts.backfill_v21_data \\
        --source .snapshots/dashboard-v2.1-archive.db \\
        --app-version v2.1 \\
        [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy import text

from api.services import database as db_mod

_DATETIME_COLS_JOBS = {"queued_at", "started_at", "completed_at", "error_timestamp", "last_heartbeat"}
_DATETIME_COLS_SESSION_STATS = {"timestamp"}
_DATETIME_COLS_CHAT_SESSIONS = {"created_at", "updated_at"}


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse a datetime string from sqlite3 into a Python datetime, or return None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _norm_queued_at(val) -> str:
    """Normalize a datetime-or-string to 'YYYY-MM-DD HH:MM:SS' for dedup keys.

    Handles three observed formats:
      - sqlite raw string with microseconds: '2025-12-30 02:16:47.617491'
      - sqlite raw string, date only: '2026-01-01'
      - aiosqlite-returned datetime: datetime(2025, 12, 30, 2, 16, 47, 617491)
      - ISO 8601 with T separator (future archives): '2027-05-01T14:00:00'
    """
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    s = str(val).replace("T", " ")
    if len(s) == 10:
        return f"{s} 00:00:00"
    return s[:19]


async def backfill(
    source_db: str,
    app_version: str,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Copy jobs/session_stats/chat_sessions from source DB to live DB.

    Returns a summary dict with insert counts.
    """
    summary = {
        "jobs_inserted": 0,
        "session_stats_inserted": 0,
        "chat_sessions_inserted": 0,
        "skipped_duplicate_jobs": 0,
    }

    src = sqlite3.connect(source_db)
    src.row_factory = sqlite3.Row

    try:
        await db_mod.init_db()

        async with db_mod.get_session() as live:
            # Build set of (project_path, transcript_file, queued_at) keys already in live DB.
            # _norm_queued_at handles both raw sqlite strings and aiosqlite datetimes.
            existing_result = await live.execute(text(
                "SELECT project_path, transcript_file, queued_at FROM jobs WHERE app_version = :v"
            ), {"v": app_version})
            seen = {(r[0], r[1], _norm_queued_at(r[2])) for r in existing_result.fetchall()}

            id_map: Dict[int, int] = {}

            # Phase 1: jobs
            for row in src.execute("SELECT * FROM jobs").fetchall():
                key = (row["project_path"], row["transcript_file"], _norm_queued_at(row["queued_at"]))
                if key in seen:
                    summary["skipped_duplicate_jobs"] += 1
                    continue

                # Build values dict from source row, overriding app_version and dropping id.
                # Filter by live schema so a source DB with extra columns (e.g. from a
                # later migration that the live DB has since dropped) doesn't crash the
                # insert with "Unconsumed column names".
                # Parse datetime strings into Python datetime objects for SQLAlchemy.
                allowed = set(db_mod.jobs_table.c.keys())
                values = {}
                for k in row.keys():
                    if k == "id" or k not in allowed:
                        continue
                    val = row[k]
                    if k in _DATETIME_COLS_JOBS:
                        val = _parse_dt(val)
                    values[k] = val
                values["app_version"] = app_version

                # Some columns may not exist in source DB (newer columns) — fill defaults.
                # Note: content_type and app_version are nullable, so they are intentionally
                # absent from this defaults block.
                for col_name, default in [
                    ("retry_count", 0), ("max_retries", 3), ("estimated_cost", 0.0),
                    ("phases", None), ("agent_phases", '["analyst","formatter"]'),
                    ("manifest_path", None), ("logs_path", None),
                ]:
                    values.setdefault(col_name, default)

                summary["jobs_inserted"] += 1
                if dry_run:
                    # Map old id to a sentinel so phase-2 dry-run counts are honest
                    id_map[row["id"]] = -row["id"]
                    continue

                stmt = db_mod.jobs_table.insert().values(**values)
                result = await live.execute(stmt)
                new_id = result.inserted_primary_key[0]
                id_map[row["id"]] = new_id

            if dry_run:
                # Phase-2 dry-run: count events that would map to a job we'd insert
                for row in src.execute("SELECT job_id FROM session_stats").fetchall():
                    if row["job_id"] is None or row["job_id"] in id_map:
                        summary["session_stats_inserted"] += 1
                try:
                    for row in src.execute("SELECT job_id FROM chat_sessions").fetchall():
                        if row["job_id"] in id_map:
                            summary["chat_sessions_inserted"] += 1
                except sqlite3.OperationalError:
                    pass
                return summary

            # Phase 2: session_stats — translate job_id
            for row in src.execute("SELECT * FROM session_stats").fetchall():
                old_job_id = row["job_id"]
                new_job_id = id_map.get(old_job_id) if old_job_id is not None else None
                if old_job_id is not None and new_job_id is None:
                    # Source row referred to a job we skipped (duplicate) — skip event too
                    continue

                values = {
                    "job_id": new_job_id,
                    "timestamp": _parse_dt(row["timestamp"]),
                    "event_type": row["event_type"],
                    "data": row["data"],
                    "app_version": app_version,
                }
                await live.execute(db_mod.session_stats_table.insert().values(**values))
                summary["session_stats_inserted"] += 1

            # Phase 3: chat_sessions — translate job_id
            try:
                chat_rows = src.execute("SELECT * FROM chat_sessions").fetchall()
            except sqlite3.OperationalError:
                chat_rows = []  # Source DB pre-dates chat_sessions table

            for row in chat_rows:
                new_job_id = id_map.get(row["job_id"])
                if new_job_id is None:
                    continue
                values = {}
                for k in row.keys():
                    if k == "job_id":
                        continue
                    val = row[k]
                    if k in _DATETIME_COLS_CHAT_SESSIONS:
                        val = _parse_dt(val)
                    values[k] = val
                values["job_id"] = new_job_id
                values["app_version"] = app_version
                await live.execute(db_mod.chat_sessions_table.insert().values(**values))
                summary["chat_sessions_inserted"] += 1

        return summary
    finally:
        src.close()


def _cli() -> None:
    p = argparse.ArgumentParser(description="Backfill historical Cardigan data into the live DB.")
    p.add_argument("--source", required=True, help="Path to source SQLite DB")
    p.add_argument("--app-version", required=True, help="Tag to apply (e.g., v2.1)")
    p.add_argument("--dry-run", action="store_true", help="Report counts without inserting")
    args = p.parse_args()

    summary = asyncio.run(backfill(
        source_db=args.source,
        app_version=args.app_version,
        dry_run=args.dry_run,
    ))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    _cli()
