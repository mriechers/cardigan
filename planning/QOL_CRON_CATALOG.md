# Cardigan — Quality-of-Life & Cron Catalog (post-LXC)

**Status:** decision menu — nothing here is built yet. Pick items and we'll
schedule them into a sprint.

**Context:** Cardigan now runs as an always-on server (LXC + `docker-compose.prod.yml`)
instead of a laptop dev tool. That unlocks scheduled background work to keep it
*fresher* (data updates itself) and *more reliable* (it looks after itself). This
catalog ranks the candidates.

### Where a scheduled job should live

| Home | Use for | Survives if `api` container is down? |
|------|---------|--------------------------------------|
| **In-process APScheduler** (already running inside the `api` container — see `api/services/ingest_scheduler.py`) | Jobs that need app/DB context: scanning, attaching, digests | ❌ no — dies with the container |
| **LXC host cron** (or a small compose sidecar) | Infra jobs that must run *even when the app is sick*: backups, health alerts, log rotation | ✅ yes |

Rule of thumb: **data-pipeline freshness → APScheduler; safety nets → host cron.**

---

## Ranked candidates

| # | Item | Home | Effort | Recommendation |
|---|------|------|--------|----------------|
| 1 | Make the auto-scan actually periodic | APScheduler | **S** | **Do first** |
| 2 | Notify on new Ready-for-Work | APScheduler + Slack | S–M | High value |
| 3 | Nightly DB backup with retention + offsite | Host cron | **S** | **Strongly recommend** |
| 4 | Health-down alerting | Host cron | S | Recommend |
| 5 | Auto-attach screengrabs | APScheduler | M | Nice-to-have |
| 6 | Scheduled search-smoke canary | Host cron | S | Optional |
| 7 | Weekly cost/usage digest | APScheduler | M | Optional |
| 8 | Log rotation / disk hygiene | Host cron | S | Hygiene |

---

### 1. Make the auto-scan actually periodic  *(the headline)*  — **S, do first**

**Today it's half-built — and not in the way you'd expect.** The config default
*looks* like it scans every 2 hours:

```python
# api/services/ingest_config.py
scan_interval_hours=2
```

…but the scheduler never reads that field. `configure_scheduler()` only ever
builds a daily cron from `scan_time`:

```python
# api/services/ingest_scheduler.py — configure_scheduler()
hour, minute = parse_scan_time(config.scan_time)   # "07:00"
trigger = CronTrigger(hour=hour, minute=minute)    # fires ONCE a day, 07:00
```

So **Ready-for-Work currently refreshes once a day at 07:00**, despite PR #176's
title ("drop scan cadence to 2h"). `scan_interval_hours` is dead config.

**Fix:** swap the `CronTrigger` for an `IntervalTrigger(hours=config.scan_interval_hours)`
(or fan out N daily cron fires). ~5 lines in `configure_scheduler()` + a test
asserting the trigger interval matches config. This is exactly the
"scan the Ready-for-Work part regularly so manual re-scans are less necessary"
ask — and it's nearly free.

*Politeness note:* mmingest crawl already has dedup + politeness (PR #186), so a
2h cadence is well within budget. If you want it fresher than 2h, drop to 1h.

---

### 2. Notify on new Ready-for-Work  — S–M, high value

When a scheduled scan finds *new* transcripts, push a heads-up instead of making
producers poll the page. Two flavors (can do either or both):
- **Slack message** to an editorial channel ("3 new transcripts ready: …").
- **Dashboard badge / unread count** on the Ready-for-Work nav item.

Hooks cleanly into `run_scheduled_scan()` (which already returns
`new_files_found` / `new_transcripts`). Pairs naturally with #1 — once scans are
frequent, a nudge is what makes "frequent" *useful* rather than just busy.

---

### 3. Nightly DB backup with retention + offsite  — **S, strongly recommend**

`scripts/snapshot_db.sh` already exists and does the hard part right: it uses
SQLite's online-backup API (safe while the app writes) and gzips the result.
**What's missing for a server:** it's never *scheduled*, has **no retention**
(snapshots pile up forever), and keeps everything **on the same box** as the DB.

**Fix:** a host cron entry (nightly) that runs `snapshot_db.sh`, prunes copies
older than N days, and `rclone`-copies the latest offsite (the workspace already
uses rclone for Drive). `dashboard.db` is now the only home for jobs, cost data,
and consumer keys — losing the LXC without this loses everything.

```cron
# example — 02:30 nightly
30 2 * * * cd /path/to/cardigan-v4 && scripts/snapshot_db.sh && \
  find .snapshots -name 'dashboard-*.db.gz' -mtime +14 -delete && \
  rclone copy "$(ls -t .snapshots/*.db.gz | head -1)" gdrive-work:cardigan-backups/
```

---

### 4. Health-down alerting  — S, recommend

`watchtower` + the compose healthcheck will *restart* a sick `api` container, but
**nothing tells you it happened** — a crash-loop could run for days unnoticed.

**Fix:** a host cron (every few minutes) that curls `/api/system/health` and
alerts (Slack/email) on non-`ok` or non-200, with a flap guard so a single blip
doesn't page you. Lives on the host deliberately — it must work *when the app
doesn't*.

---

### 5. Auto-attach screengrabs  — M, nice-to-have

`api/services/screengrab_attacher.py` + the `/api/ingest/screengrabs/attach-all`
endpoint already exist but are manual. A scheduled pass would let jobs pick up
newly-arrived stills without anyone clicking "attach." Lower urgency than #1–#4;
mostly removes a manual step. Home: APScheduler, right after the scan job.

---

### 6. Scheduled search-smoke canary  — S, optional

Run `scripts/sprint5_smoke_search.sh` on a cron (e.g. hourly) as an ongoing
mmingest health signal — it already logs JSONL and pings legacy endpoints for
regression. Cheap early warning that search/index broke. Needs a long-lived
`mmingest:read` consumer key in the crontab env.

---

### 7. Weekly cost/usage digest  — M, optional

Roll up Langfuse cost + per-model usage into a weekly Slack/email summary so LLM
spend stays visible now that jobs run unattended. Home: APScheduler. Builds on
the existing `api/services/langfuse_client.py` REST integration.

---

### 8. Log rotation / disk hygiene  — S, hygiene

A long-lived box accumulates container logs and SQLite WAL growth. Set Docker log
rotation (`max-size`/`max-file` in the compose `logging` block) and a periodic
`VACUUM`/WAL-checkpoint if the DB grows. Prevents the "disk full at 3am" class of
outage. Home: compose config + optional host cron.

---

## Suggested first batch

If you want a tight, high-leverage first sprint: **#1 + #3 + #4** — frequent
fresh scans, real backups, and a heartbeat alert. That converts the three things
that change most when a tool stops being a laptop script and becomes a server:
freshness, durability, and observability. **#2** is the obvious fast-follow once
#1 makes scans frequent enough to be worth announcing.
