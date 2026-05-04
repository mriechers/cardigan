# Cost Data Preservation & Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve historical cost data across Cardigan iterations, tag every run with the app version that produced it, and back the live SQLite database up daily — so cost-vs-quality tradeoffs across epochs (cheap-first v2.1 → quality-first v4) can be analyzed honestly.

**Architecture:** Add an `app_version` TEXT column to `jobs`, `session_stats`, and `chat_sessions` via Alembic migration 011. Source the version from a `CARDIGAN_VERSION` env var (default `"v4.1"`) plumbed into the three database write paths (`create_job`, `log_event`, `create_chat_session`). Backfill the v2.1-era data preserved in an orphaned worktree DB into the live DB tagged `app_version='v2.1'`, translating job IDs to avoid collisions. Daily cron snapshots the live DB to a host directory outside the Docker volume using SQLite's online-backup API.

**Tech Stack:** SQLite + SQLAlchemy Core, Alembic, pytest-asyncio, bash + crontab, `sqlite3.Connection.backup()` for hot snapshots, Docker Compose env passthrough.

---

## Context for engineers new to this codebase

- Database is SQLite at `/data/db/dashboard.db` inside the `cardigan-v4-api-1` and `cardigan-v4-worker-1` containers (shared volume `cardigan-v4_db-data`). The local file `cardigan-v4/cardigan.db` at repo root is empty — ignore it.
- All schema changes go through Alembic in `alembic/versions/`. Migration 010 (`010_add_content_type.py`) is the template for a single-column add.
- Database writes go through three async functions in `api/services/database.py`: `create_job` (line 323), `log_event` (line 1054), `create_chat_session` (line 1332). All other writes go through these.
- Tests live in `tests/api/test_database.py`. Pattern: `@pytest_asyncio.fixture` named `test_db` provides an isolated tmp-file SQLite DB per test.
- The orphaned v2.1-era DB (152 jobs, 543 phase events, $2.26 tracked cost, dates 2025-12-30 → 2026-01-29) lives at:
  `/Users/mriechers/Developer/.agent-worktrees/ai-editorial-assistant-v3/list-the-top-level-files-and-directories-in-this-r-fce13399/dashboard.db`
  This worktree could be cleaned up at any moment — Task 1 is a safety copy.

---

## File Structure

| File | Responsibility | Status |
|---|---|---|
| `~/Developer/pbswi/cardigan-v4/.snapshots/dashboard-v2.1-archive.db` | Immutable copy of v2.1 worktree DB | Create |
| `alembic/versions/011_add_app_version.py` | Alembic migration adding the column | Create |
| `api/services/database.py` | Define `APP_VERSION` constant; populate column in `create_job`, `log_event`, `create_chat_session`; add column to all three SQLAlchemy `Table` definitions | Modify |
| `tests/api/test_database.py` | Tests for app_version on each write path | Modify |
| `tests/api/test_app_version_migration.py` | Test that migration 011 adds the column | Create |
| `docker-compose.yml` | Add `CARDIGAN_VERSION` to api + worker env | Modify |
| `docker-compose.prod.yml` | Same env var for prod compose | Modify |
| `scripts/backfill_v21_data.py` | One-shot ID-translating backfill of v2.1 jobs/events/sessions into live DB | Create |
| `tests/test_backfill_v21.py` | Backfill correctness against fixture DBs | Create |
| `scripts/snapshot_db.sh` | Daily online-backup snapshot of live DB | Create |
| `tests/test_snapshot_script.sh` | Smoke test that snapshot produces a valid restored DB | Create |
| `.snapshots/.gitkeep` | Keep snapshot dir in repo (contents gitignored) | Create |
| `.gitignore` | Ignore `.snapshots/*` except `.gitkeep` and v2.1 archive | Modify |
| `docs/COST_DATA_VERSIONING.md` | Operator docs: how to bump version, run backfill, restore snapshot | Create |
| `CLAUDE.md` | One-line pointer to the new doc | Modify |

---

## Task 1: Safety-copy the v2.1 archive DB

Goal: Get the v2.1 data out of an orphaned worktree (which could be cleaned up at any time) into a stable in-repo location before we touch anything else.

**Files:**
- Create: `.snapshots/dashboard-v2.1-archive.db`
- Create: `.snapshots/.gitkeep`
- Modify: `.gitignore`

- [ ] **Step 1: Create the snapshots directory and gitkeep**

```bash
cd /Users/mriechers/Developer/pbswi/cardigan-v4/.worktrees/v4.1-cost-data-preservation
mkdir -p .snapshots
touch .snapshots/.gitkeep
```

- [ ] **Step 2: Add .snapshots gitignore rule**

Append to `.gitignore`:

```gitignore
# Database snapshots — keep directory, ignore contents except gitkeep and the v2.1 archive
.snapshots/*
!.snapshots/.gitkeep
!.snapshots/dashboard-v2.1-archive.db
```

The v2.1 archive is intentionally checked in: it's small (~2.7 MB), immutable, historical data we want versioned with the repo so it can never get lost again.

- [ ] **Step 3: Copy the orphaned worktree DB into place**

```bash
cp "/Users/mriechers/Developer/.agent-worktrees/ai-editorial-assistant-v3/list-the-top-level-files-and-directories-in-this-r-fce13399/dashboard.db" \
   .snapshots/dashboard-v2.1-archive.db
chmod 0444 .snapshots/dashboard-v2.1-archive.db
```

The `chmod 0444` makes it read-only — a guard against accidental overwrite.

- [ ] **Step 4: Verify the copy is intact**

```bash
python3 - <<'PY'
import sqlite3
db = ".snapshots/dashboard-v2.1-archive.db"
c = sqlite3.connect(db).cursor()
print("jobs:", list(c.execute("SELECT COUNT(*) FROM jobs"))[0][0])
print("session_stats:", list(c.execute("SELECT COUNT(*) FROM session_stats"))[0][0])
print("phase_completed events:", list(c.execute("SELECT COUNT(*) FROM session_stats WHERE event_type='phase_completed'"))[0][0])
PY
```

Expected output:
```
jobs: 152
session_stats: 2124
phase_completed events: 543
```

- [ ] **Step 5: Commit**

```bash
git add .gitignore .snapshots/.gitkeep .snapshots/dashboard-v2.1-archive.db docs/superpowers/plans/2026-05-04-cost-data-preservation.md
git commit -m "chore: archive v2.1-era DB before app_version migration

Preserves 152 jobs / 543 phase events / \$2.26 in tracked cost from the
v2.1 sprint (Dec 2025 - Jan 2026), recovered from an orphaned worktree
that could have been cleaned up at any time. Read-only archive baseline
for cross-epoch cost analysis once migration 011 lands.

Also adds the implementation plan that drove this work.

[Agent: Claude Code]"
```

---

## Task 2: Migration 011 adds `app_version` column

Goal: Add a nullable TEXT column `app_version` to the three tables that hold cost-bearing rows. Nullable on purpose: existing v4.1 rows stay NULL until tagged, and v2.1 rows will be tagged at insert time by the backfill script.

**Files:**
- Create: `alembic/versions/011_add_app_version.py`
- Create: `tests/api/test_app_version_migration.py`
- Modify: `api/services/database.py` (add column to three `Table` definitions)

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_app_version_migration.py`:

```python
"""Migration 011: app_version column exists on jobs, session_stats, chat_sessions."""
import os
import tempfile

import pytest
import pytest_asyncio
from sqlalchemy import text

from api.services import database as db_mod


@pytest_asyncio.fixture
async def fresh_db():
    """Create a fresh DB with all migrations applied via init_db()."""
    orig_engine = db_mod._engine
    orig_factory = db_mod._async_session_factory
    db_mod._engine = None
    db_mod._async_session_factory = None

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    os.environ["DATABASE_PATH"] = db_path
    try:
        await db_mod.init_db()
        yield db_path
    finally:
        await db_mod.close_db()
        db_mod._engine = orig_engine
        db_mod._async_session_factory = orig_factory
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_app_version_column_exists_on_jobs(fresh_db):
    async with db_mod.get_session() as s:
        cols = [r[1] for r in (await s.execute(text("PRAGMA table_info(jobs)"))).fetchall()]
    assert "app_version" in cols


@pytest.mark.asyncio
async def test_app_version_column_exists_on_session_stats(fresh_db):
    async with db_mod.get_session() as s:
        cols = [r[1] for r in (await s.execute(text("PRAGMA table_info(session_stats)"))).fetchall()]
    assert "app_version" in cols


@pytest.mark.asyncio
async def test_app_version_column_exists_on_chat_sessions(fresh_db):
    async with db_mod.get_session() as s:
        cols = [r[1] for r in (await s.execute(text("PRAGMA table_info(chat_sessions)"))).fetchall()]
    assert "app_version" in cols
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
source venv/bin/activate
pytest tests/api/test_app_version_migration.py -v
```

Expected: 3 failures with `AssertionError: app_version not in [...columns...]`.

- [ ] **Step 3: Write the migration**

Create `alembic/versions/011_add_app_version.py`:

```python
"""Add app_version column to jobs, session_stats, chat_sessions

Revision ID: 011
Revises: 010
Create Date: 2026-05-04

Adds a nullable app_version TEXT column to the three cost-bearing tables so
that rows can be attributed to the Cardigan version (e.g., "v2.1", "v4.1")
that produced them. Existing rows remain NULL until backfilled.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '011'
down_revision: Union[str, None] = '010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('jobs',           sa.Column('app_version', sa.Text(), nullable=True))
    op.add_column('session_stats',  sa.Column('app_version', sa.Text(), nullable=True))
    op.add_column('chat_sessions',  sa.Column('app_version', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('chat_sessions', 'app_version')
    op.drop_column('session_stats', 'app_version')
    op.drop_column('jobs',          'app_version')
```

- [ ] **Step 4: Add the column to the SQLAlchemy `Table` definitions**

In `api/services/database.py`, find the `jobs` table (around line 50–95), the `session_stats` table (line 96–110), and the `chat_sessions` table (search for `Table("chat_sessions"`). Add this Column to each, near the bottom of the column list:

```python
    Column("app_version", Text, nullable=True),
```

This keeps the SQLAlchemy schema in sync with the migrated DB so `init_db()` (which calls `metadata.create_all` on a fresh DB) produces the same shape Alembic produces on an existing one.

- [ ] **Step 5: Run the test to verify it passes**

```bash
pytest tests/api/test_app_version_migration.py -v
```

Expected: 3 passes.

- [ ] **Step 6: Run the full database test suite to confirm no regression**

```bash
pytest tests/api/test_database.py -v
```

Expected: all pre-existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/011_add_app_version.py api/services/database.py tests/api/test_app_version_migration.py
git commit -m "feat(db): add app_version column for epoch tagging

Migration 011 adds a nullable app_version TEXT to jobs, session_stats,
and chat_sessions. Subsequent commits will populate it on write and
backfill historical rows. Nullable so existing v4.1 rows stay valid
until backfilled.

[Agent: Claude Code]"
```

Note: This task does NOT apply the migration to the live DB — that happens in Task 6 (along with the backfill).

---

## Task 3: Define APP_VERSION constant and plumb it into the three write paths

Goal: When `create_job`, `log_event`, or `create_chat_session` is called without an explicit `app_version`, default it to whatever `CARDIGAN_VERSION` env var says (with `"v4.1"` as the literal fallback). Allow callers — specifically the v2.1 backfill script in Task 5 — to pass an explicit override.

**Files:**
- Modify: `api/services/database.py` (add constant; update three write functions)
- Modify: `api/models/job.py` (add `app_version` to `Job` model)
- Modify: `api/models/chat.py` (add `app_version` to `ChatSession` model)
- Modify: `tests/api/test_database.py` (add tests proving default + override behavior)

- [ ] **Step 1: Write failing tests**

Append to `tests/api/test_database.py`:

```python
@pytest.mark.asyncio
async def test_create_job_sets_default_app_version(test_db, monkeypatch):
    """Newly created jobs are tagged with CARDIGAN_VERSION env (default v4.1)."""
    monkeypatch.setenv("CARDIGAN_VERSION", "v4.2-test")
    # APP_VERSION is read at module import, so we re-read from os.environ via a helper
    import importlib, api.services.database as db_mod
    importlib.reload(db_mod)  # picks up the patched env var

    job = await db_mod.create_job(
        JobCreate(
            project_name="ver-test",
            project_path="/projects/ver-test",
            transcript_file="/transcripts/ver-test.txt",
        )
    )
    assert job.app_version == "v4.2-test"


@pytest.mark.asyncio
async def test_log_event_sets_default_app_version(test_db, monkeypatch):
    monkeypatch.setenv("CARDIGAN_VERSION", "v4.2-test")
    import importlib, api.services.database as db_mod
    importlib.reload(db_mod)

    job = await db_mod.create_job(
        JobCreate(project_name="ev", project_path="/p/ev", transcript_file="/t/ev.txt")
    )
    event = await db_mod.log_event(
        EventCreate(job_id=job.id, event_type=EventType.job_started, data=None)
    )
    # Read raw row to inspect column
    from sqlalchemy import text
    async with db_mod.get_session() as s:
        row = (await s.execute(
            text("SELECT app_version FROM session_stats WHERE id = :id"),
            {"id": event.id},
        )).fetchone()
    assert row[0] == "v4.2-test"


@pytest.mark.asyncio
async def test_create_job_accepts_app_version_override(test_db):
    """Backfill scripts must be able to pass an explicit app_version."""
    job = await create_job(
        JobCreate(
            project_name="legacy",
            project_path="/p/legacy",
            transcript_file="/t/legacy.txt",
        ),
        app_version="v2.1",
    )
    assert job.app_version == "v2.1"
```

You will also need to add `app_version` to the `Job` Pydantic model in `api/models/job.py`:

```python
    app_version: Optional[str] = None
```

And to `ChatSession` in `api/models/chat.py`:

```python
    app_version: Optional[str] = None
```

The `SessionEvent` model doesn't need it for our purposes — the test for `log_event` reads it via raw SQL above.

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/api/test_database.py::test_create_job_sets_default_app_version \
       tests/api/test_database.py::test_log_event_sets_default_app_version \
       tests/api/test_database.py::test_create_job_accepts_app_version_override -v
```

Expected: 3 failures (TypeError on the override test, AttributeError on the others).

- [ ] **Step 3: Add the APP_VERSION constant**

Near the top of `api/services/database.py`, just after the existing `import os` line, add:

```python
# App version tag stamped on all rows produced by this process.
# Override via CARDIGAN_VERSION env var (set in docker-compose.yml).
# Bump the default literal each time the codebase changes meaningfully
# enough that cost/quality should not be averaged with the prior epoch.
APP_VERSION = os.getenv("CARDIGAN_VERSION", "v4.1")
```

- [ ] **Step 4: Plumb into `create_job`**

Change the `create_job` signature from:

```python
async def create_job(job: JobCreate) -> Job:
```

to:

```python
async def create_job(job: JobCreate, app_version: Optional[str] = None) -> Job:
```

In the `values = {...}` dict inside that function, add the line:

```python
            "app_version": app_version if app_version is not None else APP_VERSION,
```

(`Optional` is already imported at the top of the file.)

- [ ] **Step 5: Plumb into `log_event`**

Change the `log_event` signature from:

```python
async def log_event(event: EventCreate) -> SessionEvent:
```

to:

```python
async def log_event(event: EventCreate, app_version: Optional[str] = None) -> SessionEvent:
```

In its `values = {...}` dict, add:

```python
            "app_version": app_version if app_version is not None else APP_VERSION,
```

- [ ] **Step 6: Plumb into `create_chat_session`**

Change the signature from:

```python
async def create_chat_session(
    session_id: str,
    job_id: int,
    project_name: str,
) -> ChatSession:
```

to:

```python
async def create_chat_session(
    session_id: str,
    job_id: int,
    project_name: str,
    app_version: Optional[str] = None,
) -> ChatSession:
```

In its `values = {...}` dict, add:

```python
            "app_version": app_version if app_version is not None else APP_VERSION,
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
pytest tests/api/test_database.py -v
```

Expected: full suite passes including the three new tests.

- [ ] **Step 8: Commit**

```bash
git add api/services/database.py api/models/job.py api/models/chat.py tests/api/test_database.py
git commit -m "feat(db): tag new rows with APP_VERSION on insert

create_job, log_event, and create_chat_session now stamp app_version
from the CARDIGAN_VERSION env var (default 'v4.1'). Callers can pass
an explicit override — used by the upcoming v2.1 backfill script.

[Agent: Claude Code]"
```

---

## Task 5: Build the v2.1 backfill script

(Task 4 is operational and handled by the controller, not subagents.)

Goal: A one-shot script that reads from `.snapshots/dashboard-v2.1-archive.db` and inserts its `jobs`, `session_stats`, and `chat_sessions` rows into the live DB tagged `app_version='v2.1'`. Job IDs collide between the two DBs (v2.1 has IDs 1–152, v4 currently has 1–14), so the script builds an `old_id → new_id` map and rewrites `session_stats.job_id` and `chat_sessions.job_id` accordingly.

**Files:**
- Create: `scripts/backfill_v21_data.py`
- Create: `tests/test_backfill_v21.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_backfill_v21.py`:

```python
"""Backfill correctness: v2.1 rows land in live DB tagged 'v2.1' with new IDs."""
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from api.services import database as db_mod
from scripts.backfill_v21_data import backfill


def _make_v21_fixture(path: str) -> None:
    """Build a tiny v2.1-shaped source DB with overlapping IDs."""
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,
            project_path TEXT NOT NULL,
            transcript_file TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 0,
            queued_at DATETIME,
            actual_cost FLOAT DEFAULT 0.0,
            agent_phases TEXT DEFAULT '["analyst","formatter"]'
        );
        CREATE TABLE session_stats (
            id INTEGER PRIMARY KEY,
            job_id INTEGER,
            timestamp DATETIME,
            event_type TEXT NOT NULL,
            data TEXT
        );
        CREATE TABLE chat_sessions (
            id TEXT PRIMARY KEY,
            job_id INTEGER NOT NULL,
            project_name TEXT NOT NULL,
            created_at DATETIME,
            updated_at DATETIME,
            total_tokens INTEGER DEFAULT 0,
            total_cost FLOAT DEFAULT 0.0,
            message_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active'
        );
        INSERT INTO jobs (id, project_path, transcript_file, status, queued_at, actual_cost)
            VALUES (1, '/p/legacy-1', '/t/1.txt', 'completed', '2026-01-01', 0.05),
                   (2, '/p/legacy-2', '/t/2.txt', 'completed', '2026-01-02', 0.07);
        INSERT INTO session_stats (id, job_id, timestamp, event_type, data)
            VALUES (10, 1, '2026-01-01', 'phase_completed', '{"cost":0.05}'),
                   (11, 2, '2026-01-02', 'phase_completed', '{"cost":0.07}');
    """)
    c.commit()
    c.close()


@pytest_asyncio.fixture
async def live_and_source_dbs():
    orig_engine = db_mod._engine
    orig_factory = db_mod._async_session_factory
    db_mod._engine = None
    db_mod._async_session_factory = None

    with tempfile.TemporaryDirectory() as tmp:
        live = os.path.join(tmp, "live.db")
        src = os.path.join(tmp, "v21.db")
        os.environ["DATABASE_PATH"] = live
        await db_mod.init_db()
        # Insert one collision-id v4 job so we can prove translation works
        from api.models.job import JobCreate
        existing = await db_mod.create_job(JobCreate(
            project_name="current", project_path="/p/current",
            transcript_file="/t/current.txt"))
        assert existing.id == 1  # collision target

        _make_v21_fixture(src)
        yield {"live": live, "source": src, "existing_v4_id": existing.id}

        await db_mod.close_db()
        db_mod._engine = orig_engine
        db_mod._async_session_factory = orig_factory


@pytest.mark.asyncio
async def test_backfill_inserts_v21_jobs_with_new_ids(live_and_source_dbs):
    paths = live_and_source_dbs
    summary = await backfill(source_db=paths["source"], app_version="v2.1")

    assert summary["jobs_inserted"] == 2
    assert summary["session_stats_inserted"] == 2

    c = sqlite3.connect(paths["live"]).cursor()
    rows = list(c.execute(
        "SELECT app_version, COUNT(*) FROM jobs GROUP BY app_version ORDER BY 1"
    ))
    # One v4.1 row (existing), two v2.1 rows (backfilled)
    assert ("v2.1", 2) in rows
    assert ("v4.1", 1) in rows


@pytest.mark.asyncio
async def test_backfill_translates_session_stats_job_id(live_and_source_dbs):
    paths = live_and_source_dbs
    await backfill(source_db=paths["source"], app_version="v2.1")

    c = sqlite3.connect(paths["live"]).cursor()
    # Every v2.1 session_stats row's job_id must point at a v2.1 job, never the existing v4 job
    rows = list(c.execute("""
        SELECT s.job_id, j.app_version
        FROM session_stats s JOIN jobs j ON j.id = s.job_id
        WHERE s.app_version = 'v2.1'
    """))
    assert len(rows) == 2
    for job_id, ver in rows:
        assert ver == "v2.1"
        assert job_id != paths["existing_v4_id"]


@pytest.mark.asyncio
async def test_backfill_is_idempotent(live_and_source_dbs):
    """Running backfill twice should not duplicate data."""
    paths = live_and_source_dbs
    s1 = await backfill(source_db=paths["source"], app_version="v2.1")
    s2 = await backfill(source_db=paths["source"], app_version="v2.1")
    assert s1["jobs_inserted"] == 2
    assert s2["jobs_inserted"] == 0  # second run sees nothing new
    assert s2["skipped_duplicate_jobs"] == 2
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_backfill_v21.py -v
```

Expected: `ImportError` from `scripts.backfill_v21_data` (script doesn't exist yet).

- [ ] **Step 3: Write the backfill script**

Create `scripts/backfill_v21_data.py`:

```python
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
from typing import Dict

from sqlalchemy import text

from api.services import database as db_mod


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

    await db_mod.init_db()

    async with db_mod.get_session() as live:
        # Build set of (project_path, transcript_file, queued_at) keys already in live DB
        existing_result = await live.execute(text(
            "SELECT project_path, transcript_file, queued_at FROM jobs WHERE app_version = :v"
        ), {"v": app_version})
        seen = {(r[0], r[1], str(r[2])) for r in existing_result.fetchall()}

        id_map: Dict[int, int] = {}

        # Phase 1: jobs
        for row in src.execute("SELECT * FROM jobs").fetchall():
            key = (row["project_path"], row["transcript_file"], str(row["queued_at"]))
            if key in seen:
                summary["skipped_duplicate_jobs"] += 1
                continue

            # Build values dict from source row, overriding app_version and dropping id
            values = {k: row[k] for k in row.keys() if k != "id"}
            values["app_version"] = app_version

            # Some columns may not exist in source DB (newer columns) — fill defaults
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
            src.close()
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
                "timestamp": row["timestamp"],
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
            values = {k: row[k] for k in row.keys() if k != "job_id"}
            values["job_id"] = new_job_id
            values["app_version"] = app_version
            await live.execute(db_mod.chat_sessions_table.insert().values(**values))
            summary["chat_sessions_inserted"] += 1

        await live.commit()

    src.close()
    return summary


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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_backfill_v21.py -v
```

Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_v21_data.py tests/test_backfill_v21.py
git commit -m "feat(scripts): backfill v2.1-era data with ID translation

Idempotent script that copies jobs/session_stats/chat_sessions from
a source SQLite DB into the live DB with --app-version tagging.
Translates job_id references so v2.1 events point at the new
v2.1 job rows, never collide with existing v4.1 IDs.

[Agent: Claude Code]"
```

---

## Task 7: Daily snapshot script + crontab

Goal: A bash script that produces a gzip'd snapshot of the live DB outside the Docker volume, plus a crontab entry to run it daily at 3am. Snapshots are restorable: gunzip + open with sqlite3.

**Files:**
- Create: `scripts/snapshot_db.sh`
- Create: `tests/test_snapshot_script.sh`

- [ ] **Step 1: Write the snapshot script**

Create `scripts/snapshot_db.sh`:

```bash
#!/usr/bin/env bash
# Daily snapshot of the cardigan-v4 dashboard DB.
# Uses SQLite's online-backup API (via Python, since sqlite3 CLI is not
# installed in the container) so it's safe while the app is writing.
#
# Usage:
#   scripts/snapshot_db.sh                # snapshot to default dir
#   CARDIGAN_SNAP_DIR=/elsewhere scripts/snapshot_db.sh

set -euo pipefail

CONTAINER="${CARDIGAN_API_CONTAINER:-cardigan-v4-api-1}"
SNAP_DIR="${CARDIGAN_SNAP_DIR:-$HOME/Developer/pbswi/cardigan-v4/.snapshots}"
DATE_TAG="$(date +%Y%m%d-%H%M%S)"
DEST="$SNAP_DIR/dashboard-$DATE_TAG.db"

mkdir -p "$SNAP_DIR"

docker exec "$CONTAINER" python3 -c "
import sqlite3
src = sqlite3.connect('/data/db/dashboard.db')
dst = sqlite3.connect('/tmp/snapshot.db')
src.backup(dst)
src.close(); dst.close()
"

docker cp "$CONTAINER:/tmp/snapshot.db" "$DEST"
docker exec "$CONTAINER" rm -f /tmp/snapshot.db

gzip -9 "$DEST"

SIZE="$(du -h "${DEST}.gz" | cut -f1)"
echo "[$(date -Iseconds)] snapshot ${DEST}.gz ($SIZE)"
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/snapshot_db.sh
```

- [ ] **Step 3: Write the smoke test**

Create `tests/test_snapshot_script.sh`:

```bash
#!/usr/bin/env bash
# Smoke test: snapshot script produces a gzip file that decompresses to a
# valid SQLite DB containing the expected tables.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "[test] running snapshot_db.sh with CARDIGAN_SNAP_DIR=$TMP_DIR"
CARDIGAN_SNAP_DIR="$TMP_DIR" "$REPO_ROOT/scripts/snapshot_db.sh"

SNAP_GZ="$(ls -t "$TMP_DIR"/dashboard-*.db.gz | head -1)"
[ -f "$SNAP_GZ" ] || { echo "FAIL: no snapshot file produced"; exit 1; }

gunzip -k "$SNAP_GZ"
SNAP_DB="${SNAP_GZ%.gz}"

# Must contain expected tables
TABLES="$(python3 -c "
import sqlite3
c = sqlite3.connect('$SNAP_DB').cursor()
print(' '.join(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")))
")"

for t in jobs session_stats chat_sessions; do
    echo "$TABLES" | grep -qw "$t" || { echo "FAIL: table $t missing"; exit 1; }
done

echo "[test] OK: snapshot produced and contains expected tables"
```

- [ ] **Step 4: Make it executable and run it**

```bash
chmod +x tests/test_snapshot_script.sh
./tests/test_snapshot_script.sh
```

Expected output: `[test] OK: snapshot produced and contains expected tables`

- [ ] **Step 5: Commit**

```bash
git add scripts/snapshot_db.sh tests/test_snapshot_script.sh
git commit -m "feat(ops): daily SQLite snapshot script with smoke test

scripts/snapshot_db.sh uses SQLite's online-backup API (via Python in
the api container) to produce a gzip'd snapshot under .snapshots/
without locking the live DB. Cron entry handled by controller.

[Agent: Claude Code]"
```

Note: The crontab entry installation is operational (handled by the controller, not a subagent).

---

## Task 8: Operator documentation

Goal: A single doc that explains the lifecycle to whoever has to operate this next quarter — bumping the version, restoring a snapshot, running another backfill.

**Files:**
- Create: `docs/COST_DATA_VERSIONING.md`
- Modify: `CLAUDE.md` (one-line pointer)

- [ ] **Step 1: Write the operator doc**

Create `docs/COST_DATA_VERSIONING.md`:

```markdown
# Cost Data Versioning & Snapshots

How cost-bearing rows in the Cardigan database get attributed to an app
version, how historical data is preserved, and how to operate both.

## What `app_version` is

Three tables — `jobs`, `session_stats`, `chat_sessions` — carry an
`app_version` TEXT column populated at insert time from the
`CARDIGAN_VERSION` env var (default `"v4.1"`, set in `docker-compose.yml`).

The point: when the prompt strategy, model routing, or pipeline shape
changes meaningfully, cost-per-job numbers from before and after are
not directly comparable. Tagging every row lets us slice analytics by
epoch instead of averaging across regimes.

## Bumping the version for a new epoch

1. Pick a tag. Convention: `v<major>.<minor>` matching the codebase
   sprint label (e.g., `v4.2`).
2. Edit the default in `api/services/database.py`:
   ```python
   APP_VERSION = os.getenv("CARDIGAN_VERSION", "v4.2")
   ```
3. Edit `docker-compose.yml` and `docker-compose.prod.yml`, both
   services, replacing `:-v4.1` with `:-v4.2`.
4. `docker compose up -d` to restart.
5. Verify: `docker exec cardigan-v4-api-1 printenv CARDIGAN_VERSION`.

The env var also accepts non-default values without code edits — handy
for short-lived experiments: `CARDIGAN_VERSION=v4.2-rc1 docker compose up -d`.

## Daily snapshots

`scripts/snapshot_db.sh` runs from cron at 03:00 daily. Output:
`.snapshots/dashboard-YYYYMMDD-HHMMSS.db.gz` (~2 MB compressed at
current scale).

- View: `crontab -l | grep snapshot_db`
- Disable: `crontab -e` and delete the line
- Trigger manually: `scripts/snapshot_db.sh`
- Test: `tests/test_snapshot_script.sh`

Snapshots are gitignored. Mac Time Machine / iCloud picks them up via
the normal `~/Developer` backup path.

### Restoring a snapshot

```bash
# Pick the snapshot you want
SNAP=.snapshots/dashboard-20260601-030001.db.gz

# Decompress
gunzip -k "$SNAP"

# Stop the stack so nothing writes mid-restore
docker compose stop api worker

# Replace the live DB inside the volume
docker cp "${SNAP%.gz}" cardigan-v4-api-1:/data/db/dashboard.db.restored
docker exec cardigan-v4-api-1 sh -c 'mv /data/db/dashboard.db /data/db/dashboard.db.bak.$(date +%s) && mv /data/db/dashboard.db.restored /data/db/dashboard.db'

# Restart
docker compose start api worker
```

## Backfilling historical data from another DB

`scripts/backfill_v21_data.py` is generic — its name notwithstanding,
the `--app-version` flag accepts any tag. To absorb data from a future
archive:

```bash
docker cp /path/to/archive.db cardigan-v4-api-1:/tmp/archive.db
docker exec cardigan-v4-api-1 python3 -m scripts.backfill_v21_data \
    --source /tmp/archive.db --app-version v3.0 [--dry-run]
docker exec cardigan-v4-api-1 rm /tmp/archive.db
```

The script is idempotent (deduplicates on
`(project_path, transcript_file, queued_at)`) and translates job IDs to
avoid collisions with the live DB. Always take a snapshot first.

## Querying across epochs

```sql
-- Per-job cost by epoch
SELECT app_version, COUNT(*), ROUND(AVG(actual_cost), 4) AS avg_cost
FROM jobs WHERE actual_cost > 0 GROUP BY app_version;

-- Per-phase cost by epoch
SELECT app_version, json_extract(data,'$.phase') AS phase,
       COUNT(*), ROUND(SUM(CAST(json_extract(data,'$.cost') AS REAL)), 4) AS cost
FROM session_stats
WHERE event_type='phase_completed'
GROUP BY app_version, phase
ORDER BY app_version, cost DESC;
```

## What `app_version` does *not* capture

Quality. Cost is half the picture; whether the cost was worth it is
the other half. Quality logging is a separate, future addition — see
the conversation that produced this plan for the three options under
discussion (manual rating, automatic AI-vs-published diff, full rubric).
```

- [ ] **Step 2: Add a pointer in CLAUDE.md**

In `CLAUDE.md`, find the section header `## Notes for Claude Code` near the bottom. Add a new section just before it:

```markdown
## Cost Data Versioning

Every row in `jobs`, `session_stats`, and `chat_sessions` is tagged with
an `app_version` (default `"v4.1"`, configurable via `CARDIGAN_VERSION`
env var). See `docs/COST_DATA_VERSIONING.md` for how to bump the
version, restore snapshots, and run backfills.
```

- [ ] **Step 3: Commit**

```bash
git add docs/COST_DATA_VERSIONING.md CLAUDE.md
git commit -m "docs: cost data versioning and snapshot operator guide

[Agent: Claude Code]"
```

---

## Operational tasks (handled by controller, not subagents)

- **Task 4:** Add `CARDIGAN_VERSION=${CARDIGAN_VERSION:-v4.1}` env to api + worker services in `docker-compose.yml` and `docker-compose.prod.yml`. Restart stack and verify with `printenv`.
- **Task 6:** After Tasks 1–5 are merged: pre-snapshot live DB, `docker exec ... alembic upgrade head` to apply migration 011, copy the v2.1 archive into the api container, run dry-run + real backfill, verify epoch counts.
- **Task 9:** Install daily cron entry, refresh OpenRouter→Langfuse credentials, run final code review.
