# Sprint 5 — Staging Soak Runbook

**Sprint:** 5 (final Phase 1 gate)
**Directory under crawl:** `/wisconsinlife/` at depth 1, prefix `2WLI`
**Duration:** 24 hours
**Environment:** local dev (Studio) against production mmingest
**Gate output:** GO/NO-GO + tunable-defaults snapshot for production rollout

---

## Prerequisites

- Cardigan main at `ff3a3be` or newer (S4A merged — `search_mmingest`, `get_mmingest_asset`,
  `list_recent_mmingest_assets` MCP tools live)
- VPN/campus tunnel active (mmingest is campus-only)
- `tmux` available for background sessions
- Python 3.13 + virtual environment at `venv/` (or `.venv/`)
- `cron` or `launchd` for smoke-test scheduling (see Section C)

---

## Section A — Setup (one-time, ~10 minutes)

### A1. Pull latest cardigan main

```bash
cd /Users/mriechers/Developer/pbswi/cardigan-v4
git fetch origin
git checkout main
git pull --ff-only
```

Confirm you're at `ff3a3be` or newer:
```bash
git log --oneline | head -3
```

Expected: `ff3a3be feat(mcp): Sprint 4A — add 3 mmingest search/asset/recent MCP tools` or a later commit.

### A2. Activate virtual environment and install dependencies

```bash
source venv/bin/activate
pip install -r requirements.txt
```

Verify `httpx`, `sqlalchemy[asyncio]`, `aiosqlite`, and `apscheduler` are present:
```bash
pip show httpx sqlalchemy apscheduler aiosqlite 2>&1 | grep -E "^(Name|Version)"
```

### A3. Migrate dev DB to head

```bash
alembic upgrade head
```

Expected output ends with `Running upgrade ... -> 018, add_mmingest_audit_log` (or the latest migration).
Verify the key tables exist:
```bash
python3 - <<'EOF'
import sqlite3, os
db = os.environ.get("DATABASE_PATH", "dashboard.db")
conn = sqlite3.connect(db)
tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
required = {"mmingest_files", "mmingest_sidecars", "consumer_keys", "mmingest_audit_log"}
missing = required - tables
if missing:
    print(f"MISSING tables: {missing}")
else:
    print("All required tables present")
# Verify FTS5 virtual table
fts = conn.execute("SELECT name FROM sqlite_master WHERE name='mmingest_sidecars_fts'").fetchone()
print(f"FTS5 virtual table: {'present' if fts else 'MISSING'}")
conn.close()
EOF
```

### A4. Configure indexer scope to `/wisconsinlife/` at depth 1

The scheduler instantiates `MmingestIndexer` with `directories=["/"]` by default (see
`api/services/mmingest/scheduler.py`). For the soak, you want **only** `/wisconsinlife/` at
depth 1 so crawl load is bounded and results are scoped.

**Option A — environment override (recommended for the soak):**

Add a `MMINGEST_DIRECTORIES` environment variable handling. The scheduler currently hardcodes
`directories=["/"]`; the cleanest short-term approach is to patch the scheduler's
`run_delta_walk()` temporarily:

```python
# In api/services/mmingest/scheduler.py, replace the MmingestIndexer call temporarily:
indexer = MmingestIndexer(
    engine=engine,
    base_url="https://mmingest.pbswi.wisc.edu/",
    directories=["/wisconsinlife/"],   # <-- soak scope
    max_concurrent=4,
    rate_per_second=1.0,               # <-- 1 req/s for the soak
)
```

**Note:** Do NOT commit this change to main; it's soak-only. Revert after Section F.
The production scheduler will walk `/` with the production tunables once GO is confirmed.

**Option B — run the indexer directly (avoids modifying scheduler.py):**

Use the one-shot indexer script at `scripts/sprint5_soak_monitor.sh` (Section B) — it
invokes `MmingestIndexer` directly with the soak parameters, bypassing the scheduler entirely.
The scheduler can stay at its defaults during the soak since the monitor script controls timing.

> **Recommendation: Use Option B.** It leaves the scheduler untouched, runs outside the
> APScheduler machinery, and produces JSONL telemetry. The 24h soak doesn't need APScheduler
> cadence — the monitor script handles timing.

### A5. Create a consumer key for smoke tests

```bash
python3 scripts/create_consumer_key.py \
  --label "sprint5-smoke" \
  --scopes "mmingest:read"
```

Save the output key — you'll need it in Section C's smoke script. The key is stored hashed;
you cannot retrieve it afterward.

### A6. Record baseline file count for coverage criterion

Before starting the soak, capture the ground-truth count from mmingest directly:

```bash
BASELINE=$(curl -s "https://mmingest.pbswi.wisc.edu/wisconsinlife/" \
  | grep -oi 'href="[^"]*\.mp4"' | wc -l | tr -d ' ')
echo "Baseline MP4 count for /wisconsinlife/: $BASELINE"
echo "$BASELINE" > /tmp/sprint5-baseline-count.txt
```

This number is your coverage target for Section D criterion 4.

### A7. Bring up Cardigan dev

```bash
./init.sh
uvicorn api.main:app --reload --port 8100
```

Wait for the startup log lines:
```
INFO:     Application startup complete.
```

Verify the health endpoint returns 200 (do not trust the `active_backend` / `active_model` /
`last_run` fields in the body — these are known-unreliable; see project memory):

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8100/api/system/health
# Expected: 200
```

Verify the mmingest search endpoint is live:

```bash
curl -s "http://localhost:8100/api/mmingest/search?q=wisconsin" \
  -H "X-API-Key: <your-consumer-key>" | python3 -m json.tool | head -10
```

Expected: JSON with `total` field (may be 0 before indexing, but the endpoint must return 200).

### A8. Start monitor and smoke in tmux

```bash
tmux new-session -d -s soak-monitor
tmux send-keys -t soak-monitor \
  'bash /Users/mriechers/Developer/pbswi/cardigan-v4/scripts/sprint5_soak_monitor.sh 2>&1 | tee /tmp/sprint5-monitor.log' \
  Enter

# Smoke script runs via cron (see Section C) — no tmux pane needed for the scheduler itself,
# but keep a pane for manual test runs:
tmux new-window -t soak-monitor
tmux send-keys -t soak-monitor \
  'tail -f /tmp/sprint5-smoke-log.jsonl' \
  Enter
```

---

## Section B — Background monitor (`scripts/sprint5_soak_monitor.sh`)

The script at `scripts/sprint5_soak_monitor.sh` runs for 24 hours and logs to
`/tmp/sprint5-soak-log.jsonl`. It:

1. Runs one full `MmingestIndexer` pass against `/wisconsinlife/` at depth 1
2. Checks `fts_parity_delta()` every 6 hours
3. Records HTTP client telemetry: total requests, status codes, latency, in-flight peak,
   response sizes
4. Captures sidecar-first vs MP4 queue depth snapshots
5. Appends structured JSONL to `/tmp/sprint5-soak-log.jsonl`

**To start:**
```bash
# In a tmux pane or nohup:
bash scripts/sprint5_soak_monitor.sh
# OR with nohup:
nohup bash scripts/sprint5_soak_monitor.sh > /tmp/sprint5-monitor.log 2>&1 &
echo $! > /tmp/sprint5-monitor.pid
```

**To watch progress:**
```bash
tail -f /tmp/sprint5-soak-log.jsonl | python3 -c "
import sys, json
for line in sys.stdin:
    d = json.loads(line.strip())
    print(f\"{d.get('ts','')} [{d.get('event','?')}] {d.get('msg','')}\")
"
```

**Telemetry fields logged per event:**

| Event | Key fields |
|-------|-----------|
| `indexer_run` | `files_seen`, `files_new`, `sidecars_fetched`, `fts_parity_delta`, `elapsed_s`, `req_total`, `req_5xx`, `req_errors`, `p50_latency_ms`, `p95_latency_ms`, `peak_inflight`, `sidecar_qsize`, `mp4_qsize` |
| `parity_check` | `fts_delta`, `mmingest_files_count` (prefix=2WLI), `mmingest_sidecars_count` |
| `pause_window` | `window_start`, `window_end`, `now_utc` |
| `error` | `exc_type`, `msg` |

**Pause window:** The soak configures `pause_window = (11:00, 15:00) UTC` which maps to
`06:00–10:00 America/Chicago` (CDT = UTC-5 in summer). No requests will be made during this
window. The monitor script respects the same window.

**Queue depth instrumentation note:** `TwoLaneWorkQueue.qsize()` tracks total items; there
is no public `sidecar_qsize()` / `mp4_qsize()` accessor on `TwoLaneWorkQueue` directly.
The monitor script instruments queue depth at the `MmingestIndexer` level by patching
`_sidecar.qsize()` and `_primary.qsize()` on the internal queue object post-instantiation.
See the script comments for the approach.

---

## Section C — Smoke test (`scripts/sprint5_smoke_search.sh`)

The smoke script runs every 30 minutes across the 24h soak. It tests three queries:

| Query | Expected behaviour |
|-------|-------------------|
| `wisconsin` | Common term — should return hits once `/wisconsinlife/` sidecars are indexed |
| `"Wisconsin Life"` | Known phrase from WLI episode transcripts — should match once indexed |
| `xyzzy_no_match_expected_404` | Unlikely term — should return empty results (not a 404/500) |

Each call's status, latency, and result count are appended to `/tmp/sprint5-smoke-log.jsonl`.

**Schedule via cron:**
```bash
# Add to crontab:
crontab -e
```
Insert line:
```
*/30 * * * * CONSUMER_KEY=<your-key> bash /Users/mriechers/Developer/pbswi/cardigan-v4/scripts/sprint5_smoke_search.sh >> /tmp/sprint5-cron.log 2>&1
```

**OR run in a loop in a tmux pane:**
```bash
# In tmux pane:
export CONSUMER_KEY=<your-key>
while true; do
  bash /Users/mriechers/Developer/pbswi/cardigan-v4/scripts/sprint5_smoke_search.sh
  sleep 1800
done
```

**Manual one-shot run (useful for verifying setup):**
```bash
CONSUMER_KEY=<your-key> bash scripts/sprint5_smoke_search.sh
```

Expected first output (before `/wisconsinlife/` is indexed):
```json
{"ts": "2026-...", "event": "smoke", "query": "wisconsin", "status": 200, "hits": 0, "latency_ms": 12}
```
After indexing completes, `hits` should be > 0 for the first two queries.

---

## Section D — Acceptance envelope (GO criteria)

All five categories must pass for a GO decision.

### D1. Politeness

| Criterion | Threshold | How measured |
|-----------|-----------|--------------|
| Average request rate to mmingest | ≤ 1.0 req/s over any 5-min window | `sprint5_soak_report.py` bins requests by 5-min window |
| Peak in-flight concurrency | ≤ 4 simultaneous requests | `peak_inflight` field in `indexer_run` events |
| Error rate (5xx + connect errors) | < 1% of all requests | `(req_5xx + req_errors) / req_total` |
| Zero requests during pause window | 06:00–10:00 America/Chicago (CDT) | `pause_window` events logged; zero `indexer_run` events with `ts` in that window |

**GO:** all four sub-criteria met.
**NO-GO trigger:** any window where avg rate > 1 req/s, OR any `peak_inflight` > 4, OR error rate ≥ 1%, OR any HTTP request timestamped inside the pause window.

### D2. FTS parity

| Criterion | Threshold |
|-----------|-----------|
| `fts_parity_delta()` at each 6h check | Must return 0 |

Checks occur at approximately: T+0h (initial), T+6h, T+12h, T+18h, T+24h.

**GO:** all five checks return 0 (or None at T+0h before migration, treated as "not yet populated" not "fail").
**NO-GO trigger:** any non-zero delta at any check.

### D3. Latency

| Metric | Threshold | Measured over |
|--------|-----------|---------------|
| `/api/mmingest/search` p95 | < 50ms | All 48 smoke calls (2/h × 24h) |
| `/api/mmingest/search` p99 | < 200ms | Same 48 calls |

Latency is measured from the smoke script (wall-clock curl time), not server-side.
Dev mode includes some overhead; the threshold is deliberately conservative.

**GO:** p95 < 50ms AND p99 < 200ms.
**NO-GO trigger:** p95 ≥ 50ms OR p99 ≥ 200ms.

### D4. Coverage

| Criterion | Threshold |
|-----------|-----------|
| `mmingest_files` row count where `prefix LIKE '2WLI%'` at hour 24 | Within ±5% of baseline MP4 count |

Baseline was captured in step A6 (`/tmp/sprint5-baseline-count.txt`).

Note: `mmingest_files` tracks ALL file types (MP4 + SRT + SCC + images), so the raw row count
will be higher than the baseline MP4 count. The report script compares MP4-only rows
(`file_type = 'mp4'`).

**GO:** `|actual_mp4_count - baseline| / baseline ≤ 0.05`
**NO-GO trigger:** difference > 5%.

### D5. No regression

| Criterion | How verified |
|-----------|-------------|
| `/api/jobs` returns 200 | Smoke script pings this endpoint alongside the mmingest search |
| `/api/system/health` returns 200 | Same |
| No unhandled exceptions in API log during soak | Review `api.log` for ERROR-level entries |

**GO:** both legacy endpoints 200 throughout; no unhandled exceptions in logs.
**NO-GO trigger:** any non-200 response on `/api/jobs` or `/api/system/health`; any
`ERROR` or `CRITICAL` in `api.log` not present pre-soak.

### D6. Tunable-defaults snapshot (always captured, not a pass/fail gate)

Regardless of GO/NO-GO, the report records the empirical values observed during the soak
for use as production defaults:

- Observed average request rate (req/s)
- Observed peak concurrency
- Actual pause window effectiveness (% of schedule covered)
- Sidecar vs MP4 queue drain ratio
- Recommended `max_concurrent` setting (≤ observed peak, round down to nearest power of 2)
- Recommended `rate_per_second` setting (≤ 80% of observed max burst)

---

## Section E — End-of-soak report

After the 24h window:

```bash
cd /Users/mriechers/Developer/pbswi/cardigan-v4
source venv/bin/activate
python3 scripts/sprint5_soak_report.py \
  --soak-log /tmp/sprint5-soak-log.jsonl \
  --smoke-log /tmp/sprint5-smoke-log.jsonl \
  --baseline-count /tmp/sprint5-baseline-count.txt \
  --output planning/sprint-5-soak-report.md
```

The report covers each Section D criterion with concrete numbers. If `matplotlib` is available
in the virtual environment, it also writes PNG plots to `planning/sprint-5-plots/`.

Review the report, then update the plan file's `[ ] Sprint 5 soak execution` and
`[ ] Sprint 5 GO/NO-GO report` checkboxes with the result.

---

## Section F — Rollback if soak fails

Sprint 5 is a soak against local dev — there is nothing to roll back at the code level since no
production deployment has occurred. The appropriate response to each failure mode:

### Polite-crawl violation (rate > 1 req/s or peak concurrency > 4)

1. Stop the monitor script: `kill $(cat /tmp/sprint5-monitor.pid)` or Ctrl-C in tmux.
2. File an issue on `mriechers/cardigan` with the specific 5-min window data from the report.
3. Patch `MmingestIndexer` parameters (reduce `rate_per_second` or `max_concurrent`) on a fix branch.
4. Re-soak from scratch (reset `/tmp/sprint5-soak-log.jsonl`) once the fix is verified in
   unit tests.

### FTS parity drift (non-zero delta)

1. Stop the monitor, stop Cardigan.
2. Run the parity check manually:
   ```bash
   python3 - <<'EOF'
   import asyncio
   from sqlalchemy.ext.asyncio import create_async_engine
   from api.services.mmingest._db import fts_parity_delta
   engine = create_async_engine("sqlite+aiosqlite:///dashboard.db")
   async def check():
       async with engine.connect() as conn:
           d = await fts_parity_delta(conn)
           print(f"delta={d}")
   asyncio.run(check())
   EOF
   ```
3. If delta persists: check for missing FTS5 triggers (migration 016 trigger on
   `mmingest_sidecars` INSERT/DELETE/UPDATE) with `SELECT * FROM sqlite_master WHERE name LIKE '%fts%'`.
4. File issue on `mriechers/cardigan` with the delta value and any trigger anomalies found.
5. Do NOT rebuild/replace FTS5 index manually — fix the root cause and re-soak.

### Latency regression (p95 > 50ms)

p95 > 50ms on a local SQLite DB is unexpected and likely indicates:
- Too many rows without a covering index (check `EXPLAIN QUERY PLAN` for the FTS5 JOIN query)
- FTS5 index corruption (run `INSERT INTO mmingest_sidecars_fts(mmingest_sidecars_fts) VALUES('integrity-check')`)
- Cardigan dev mode overhead (run a quick benchmark with `uvicorn --no-reload` to isolate)

File issue if root cause is an index or query problem. If it's purely dev-mode overhead,
re-run with `--no-reload` and document the baseline diff in the report.

### Back-compat break (legacy endpoint returns non-200)

1. Check `api.log` for the error.
2. If it's an unrelated error (DB lock, scheduler conflict), stop and restart Cardigan.
3. If it's a regression in the mmingest code path leaking to other endpoints, bisect with
   `git bisect` between S3A (`5e0f1c6`) and S4A (`ff3a3be`).
4. File issue with the specific endpoint + error + commit range.

### Coverage miss (>5% gap in file count)

Most likely explanation: the crawler's change-detection triple is skipping files on
re-runs (false "unchanged" matches). Check:
- `files_seen` vs `files_new` ratios across `indexer_run` events in the soak log.
- If `files_new` drops to 0 after the first run but `files_seen` is also 0, the crawler is
  not walking — check the `max_depth` parameter (must be ≥ 1 for `/wisconsinlife/` contents).

File issue if coverage is consistently below 95%.

---

## Quick reference: tunables

| Parameter | Soak value | Notes |
|-----------|-----------|-------|
| `directories` | `["/wisconsinlife/"]` | Depth 1; files live directly in this dir |
| `max_concurrent` | 4 | Hard cap enforced by semaphore |
| `rate_per_second` | 1.0 | Token bucket refill (one token = one request) |
| `pause_window` | `(11:00, 15:00) UTC` | Maps to 06:00–10:00 CDT |
| `max_depth` | 1 | Depth 0 = `/wisconsinlife/` listing; depth 1 = files within |
| Smoke interval | 30 min | 48 calls over 24h |
| Parity check interval | 6h | 5 checks over 24h |

---

## Expected sequence of events

| Time | Expected |
|------|----------|
| T+0h | First indexer run begins; 2WLI files start appearing in `mmingest_files` |
| T+0h to T+4h | Sidecars fetched and FTS5 populated; first smoke call returns hits |
| T+6h | First 6h parity check (should be 0) |
| T+11:00–15:00 UTC | Pause window — zero crawl activity; monitor logs `pause_window` events |
| T+12h | Second 6h parity check |
| T+18h | Third 6h parity check |
| T+24h | Final parity check; run `sprint5_soak_report.py` |

---

## Files produced by this soak

| File | Contents |
|------|----------|
| `/tmp/sprint5-soak-log.jsonl` | Indexer run telemetry + parity checks (JSONL) |
| `/tmp/sprint5-smoke-log.jsonl` | Smoke search results (JSONL) |
| `/tmp/sprint5-baseline-count.txt` | Ground-truth MP4 count from mmingest |
| `/tmp/sprint5-monitor.log` | Monitor script stdout/stderr |
| `/tmp/sprint5-cron.log` | Cron-scheduled smoke script log |
| `planning/sprint-5-soak-report.md` | Final GO/NO-GO report (generated by report script) |
| `planning/sprint-5-plots/*.png` | Optional latency/rate plots (if matplotlib available) |
