# Cost Data Versioning & Snapshots

How cost-bearing rows in the Cardigan database get attributed to an app
version, how historical data is preserved, and how to operate both.

## What `app_version` is

Three tables — `jobs`, `session_stats`, `chat_sessions` — carry an
`app_version` TEXT column populated at insert time from the
`CARDIGAN_VERSION` env var. When that env var is unset, the value is
**derived from the git tag** (e.g. `v4.2`) via `setuptools_scm` →
`_default_app_version()` in `api/services/database.py`. The compose files
set the env var explicitly (`:-v4.2` fallback) so a deployed container is
pinned to the current epoch regardless of how the image was built.

The point: when the prompt strategy, model routing, or pipeline shape
changes meaningfully, cost-per-job numbers from before and after are
not directly comparable. Tagging every row lets us slice analytics by
epoch instead of averaging across regimes.

## Bumping the version for a new epoch

1. Pick a tag. Convention: `v<major>.<minor>` matching the codebase
   sprint label (e.g., `v4.2`). Tag the release (`git tag -a v4.2.0`) —
   `_default_app_version()` derives the `v4.2` epoch from it automatically.
   **Do not hand-edit `api/services/database.py`** — there is no static
   default to change; it reads from the git tag.
2. Edit `docker-compose.yml` and `docker-compose.prod.yml`, both
   services, replacing `:-v4.1` with `:-v4.2` so the deployment fallback
   matches the tag (the env var is read *before* the tag-derived default,
   so this pin is what a running container actually reports).
3. `docker compose up -d` to restart.
4. Verify: `docker exec cardigan-v4-api-1 printenv CARDIGAN_VERSION`.

The env var also accepts non-default values without code edits — handy
for short-lived experiments: `CARDIGAN_VERSION=v4.2-rc1 docker compose up -d`.

## Schema-change ordering (any new column on jobs / session_stats / chat_sessions)

If a release adds a column the running code writes to, the deploy
sequence must be **migrate before restart**. The hazard: between
`docker compose up -d` (new code, new env, new INSERT shape) and
`alembic upgrade head` (new column exists), every write fails because
the column doesn't exist yet.

Correct order for any schema-touching deploy:

1. `scripts/snapshot_db.sh` — fall-back snapshot before touching prod data.
2. `docker exec cardigan-v4-api-1 alembic upgrade head` — apply pending
   migrations against the running stack. Adding a nullable column is
   metadata-only; the existing code keeps working because it doesn't
   yet write the new field.
3. Verify the new column landed:
   ```bash
   docker exec cardigan-v4-api-1 python3 -c "
   import sqlite3
   c = sqlite3.connect('/data/db/dashboard.db').cursor()
   print([r[1] for r in c.execute('PRAGMA table_info(jobs)')])
   "
   ```
4. **Now** restart with the new code: `docker compose up -d`.
5. Spot-check that fresh writes populate the new column.

Migrations that drop columns or rewrite data need additional care
(usually a two-step deploy). This doc covers the additive case.

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

### Note on cost rollups (per-job vs per-event)

The two queries above measure different things and can disagree for the same
epoch. The v2.1 archive is the canonical example:

- **`$1.70`** — `SUM(jobs.actual_cost)`, the per-job rollup.
- **`$2.26`** — `SUM(json_extract(session_stats.data,'$.cost'))` summed across
  `phase_completed` events, the per-event total.

They differ because in v2.1 only 33 of 152 jobs had `actual_cost` populated
(job-level cost tracking was less reliable then), whereas the per-event sum
captures phase costs even when the job-level rollup was missed. **The per-event
number is the more complete measurement;** prefer it when reconciling dashboards,
and don't expect the two totals to match for older epochs.

## What `app_version` does *not* capture

Quality. Cost is half the picture; whether the cost was worth it is
the other half. Quality logging is a separate, future addition — see
the conversation that produced this plan for the three options under
discussion (manual rating, automatic AI-vs-published diff, full rubric).
