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
   APP_VERSION = os.getenv("CARDIGAN_VERSION") or "v4.2"
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
