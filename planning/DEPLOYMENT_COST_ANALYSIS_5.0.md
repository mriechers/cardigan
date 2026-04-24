# Cardigan 5.0 — Deployment Cost Analysis

**Date:** 2026-04-11
**Audience:** PBS Wisconsin leadership (decision doc for "what do we need to share this tool more widely?")
**Scope:** Single-tenant deployment for ~5–20 named PBS WI staff, behind Cloudflare Access. Expected volume ~50–100 transcripts/month.

---

## Executive Summary

Cardigan is already 80% deployment-ready. Dockerfiles, a production Compose file, and a GitHub Actions pipeline that publishes images to GHCR all exist today. Moving from "local dev tool" to "shared internal tool for 5–20 staff" is primarily a hosting + access-control decision, not an engineering rebuild.

**Bottom-line cost for the realistic 75 transcripts/month target:**

| Deployment        | Infra/month | LLM/month | **Total/month** | **Total/year** |
|-------------------|-------------|-----------|-----------------|----------------|
| Proxmox (self-hosted) | ~$0      | $15–25    | **$15–25**      | **$180–300**   |
| Fly.io            | $10–15      | $15–25    | **$25–40**      | **$300–480**   |
| Railway           | $15–30      | $15–25    | **$30–55**      | **$360–660**   |

**Recommendation:** Fly.io. Best balance of durability, low ops burden, predictable billing, and native support for Cardigan's SQLite + persistent volume architecture. Proxmox is the right choice if existing home/office VM hardware is available and zero incremental cost outweighs the operational burden.

The LLM spend dominates only at much higher volumes; at 75 transcripts/month LLM and infra costs are comparable, so hosting choice matters more than prompt optimization in the first year.

---

## 1. Workload Assumptions

These assumptions drive every number in this doc. Adjust them and the math scales linearly.

| Input                        | Value                         | Source                          |
|------------------------------|-------------------------------|---------------------------------|
| Transcripts / month          | 75 (midpoint of 50–100)       | Confirmed with user             |
| Named users                  | 5–20 PBS WI staff             | Confirmed with user             |
| Concurrent jobs              | ≤ 3 (single worker)           | `config/llm-config.json`        |
| Mix: clips (< 30 min)        | 60%                           | Estimate — majority of PBS content is short-form |
| Mix: episodes (30–45 min)    | 30%                           | Estimate                        |
| Mix: long-form (> 45 min)    | 10%                           | Estimate                        |
| Tier escalation / retry rate | ~5%                           | `planning/claude-progress.txt` Sprint 14 data (7% observed failure rate, ~5% recoverable via escalation) |
| Prompt caching               | **Not implemented**           | Confirmed via code review — ~5–30% future savings opportunity |

---

## 2. LLM Cost Breakdown

Cardigan's 4-phase pipeline (`api/services/worker.py:134-153`) routes each phase to a model tier based on transcript duration. Pricing comes from the table in `api/services/llm.py:50-78`, in USD per 1M tokens.

| Phase     | Tier         | Model                  | Input $/1M | Output $/1M |
|-----------|--------------|------------------------|------------|-------------|
| analyst   | cheapskate   | claude-haiku-4-5       | $0.80      | $4.00       |
| formatter | default      | claude-sonnet-4-5      | $3.00      | $15.00      |
| seo       | cheapskate   | claude-haiku-4-5       | $0.80      | $4.00       |
| manager (QA) | big-brain | claude-opus-4-5        | $15.00     | $75.00      |

### Per-transcript cost by size tier

| Transcript type    | Duration    | Tier override                   | Est. total cost |
|--------------------|-------------|----------------------------------|-----------------|
| Clip               | < 30 min    | All phases stay at cheapskate   | **$0.05–0.15**  |
| Episode            | 30–45 min   | Formatter → Sonnet              | **$0.18–0.30**  |
| Long-form          | > 45 min    | Formatter → Opus + chunking     | **$0.50–0.80**  |

### Weighted average at the assumed mix

```
(0.60 × $0.10) + (0.30 × $0.25) + (0.10 × $0.65) = ~$0.20 per transcript
```

With ~5% escalation overhead → **~$0.21 per transcript average.**

### Monthly LLM spend sensitivity

| Volume         | Avg cost/transcript | **Monthly LLM** | **Annual LLM** |
|----------------|---------------------|-----------------|----------------|
| 50 / month     | $0.21               | **$10.50**      | **$126**       |
| 75 / month     | $0.21               | **$15.75**      | **$189**       |
| 100 / month    | $0.21               | **$21.00**      | **$252**       |
| 150 / month    | $0.21               | **$31.50**      | **$378**       |

**Safety net:** Cardigan already enforces a $1.00/run cost cap by default (`LLM_RUN_COST_CAP` in `api/services/llm.py`), so runaway bills from a bad prompt or recursive retries are already bounded.

---

## 3. Hosting Platform Comparison

All three options are single-tenant deployments fronted by Cloudflare Access for the email allowlist. The existing `docker-compose.prod.yml` pulls images from `ghcr.io/mriechers/cardigan-{api,worker,web}:latest` — no platform-specific image rebuild needed.

### Option A — Self-hosted Proxmox VM

Matches the "Phase A: Home VM" plan already described in `planning/DESIGN_4.0.md`.

**Architecture:**
- One VM running `docker compose -f docker-compose.prod.yml up`
- Cloudflare Tunnel (already scaffolded as optional profile in compose file) for public access
- Watchtower auto-updates from GHCR on new main-branch pushes
- Local disk for SQLite + OUTPUT/transcripts volumes

**Monthly cost:**
- Infrastructure: **~$0** incremental (existing hardware)
- Power/internet: already paid for other reasons

**Pros:**
- Zero recurring infra cost
- Full data sovereignty — transcripts never leave your network
- Plays nicely with PBS WI's ingest server (`mmingest.pbswi.wisc.edu`) on the same network

**Cons:**
- Backup/monitoring/recovery is 100% on the user
- Single point of failure if VM host goes down
- SSL termination / DNS / firewall are manual setup
- Laptop shutdown = tool unavailable for everyone

**Good fit if:** There's an existing reliable Proxmox host with 2GB RAM and 20GB disk to spare, and someone is comfortable being on-call for it.

---

### Option B — Fly.io **(Recommended)**

Cardigan's 3-container + persistent-volume architecture maps cleanly onto Fly's machine model.

**Architecture:**
- `api` machine: shared-cpu-1x, 512MB RAM
- `worker` machine: shared-cpu-1x, 1GB RAM
- `web`: can run as a third machine, OR deploy the static Vite build to Cloudflare Pages (free) and skip the web container entirely
- One 10GB Fly Volume mounted at `/data` for SQLite + OUTPUT/transcripts
- Litestream sidecar for continuous SQLite replication to S3-compatible backup

**Monthly cost breakdown** (USD, Fly.io 2026 pricing):

| Line item                           | Cost      |
|-------------------------------------|-----------|
| API machine (shared-cpu-1x, 512MB)  | ~$2–4     |
| Worker machine (shared-cpu-1x, 1GB) | ~$5–8     |
| Web machine (or Cloudflare Pages)   | ~$2 or $0 |
| 10GB persistent volume              | ~$1.50    |
| Outbound bandwidth (negligible at this volume) | <$1 |
| **Infrastructure subtotal**         | **$10–15**|

**Pros:**
- Actually-managed infra (auto-restart, health checks, SSL, DNS)
- Native SQLite story via Litestream; no forced Postgres migration
- Scale-to-zero on the worker when idle (further cost savings)
- Predictable per-machine billing — unlike Railway, no surprise bills
- Fast cold starts (~1 sec) so scale-to-zero is viable

**Cons:**
- Requires writing a `fly.toml` (~2–3 hours of work; not blocking this decision)
- Need to set up Litestream for SQLite backups
- Slightly more DevOps literacy required than Railway

**Good fit if:** We want a "real" production deployment with low operational burden but don't need multi-tenant scale.

---

### Option C — Railway

**Architecture:**
- Three Railway services (api, worker, web) deployed from GHCR or GitHub
- Railway persistent volume for SQLite (or optionally migrate to managed Postgres — but this is a bigger change)
- Railway's built-in proxy handles SSL/DNS

**Monthly cost:**
- Usage-based pricing is harder to forecast precisely
- Three always-on services + small volume: **~$15–30/month**
- If we migrate SQLite → managed Postgres to fit Railway's model better: add another ~$10–15/month

**Pros:**
- Simplest UI of the three cloud options
- Single GitHub-integrated deploy flow
- Least DevOps literacy required

**Cons:**
- Usage-based billing creates forecast uncertainty (harder to commit to a monthly number for finance)
- More expensive than Fly.io for equivalent resources
- Less natural fit for SQLite-with-volume architecture; encourages a Postgres migration we don't actually need yet

**Good fit if:** The operator's time savings outweigh the ~$10–15/month premium over Fly.io.

---

## 4. Total Cost Summary (at 75 transcripts/month)

| Deployment        | Infra/month | LLM/month | **Total/month** | **Total/year** |
|-------------------|-------------|-----------|-----------------|----------------|
| Proxmox           | ~$0         | ~$16      | **~$16**        | **~$192**      |
| Fly.io            | ~$12        | ~$16      | **~$28**        | **~$336**      |
| Railway           | ~$22        | ~$16      | **~$38**        | **~$456**      |

**For context:** at 75 transcripts/month, even the most expensive option is **less than $40/month** — well below typical SaaS budgets for an editorial productivity tool and easily justified against the staff hours saved per episode.

---

## 5. What 5.0 Still Needs (Non-Cost Checklist)

These are the actual work items beyond the hosting decision. None are blockers for the cost estimate; they're listed so the boss sees the full picture.

- [ ] Write `fly.toml` (if Fly.io chosen) — ~2–3 hours
- [ ] Configure Cloudflare Access email allowlist for 5–20 PBS WI staff
- [ ] Rotate production secrets: `OPENROUTER_API_KEY`, `AIRTABLE_API_KEY`, set strong `CARDIGAN_API_KEY`
- [ ] Configure cost caps as env vars: `LLM_RUN_COST_CAP=1.0`, `LLM_MAX_COST_PER_1K_TOKENS=0.05`, `LLM_ENFORCE_GUARDS=true`
- [ ] Daily SQLite backup job (Litestream on Fly.io; cron-based `sqlite3 .backup` on Proxmox)
- [ ] Remove `mcp` service from prod compose profile — the MCP server assumes local filesystem paths and cannot run on cloud PaaS
- [ ] Basic failure alerting (email-on-job-failure; Cardigan already exposes job status via `/api/jobs`)
- [ ] Document a runbook: restart procedure, backup restore, secret rotation
- [ ] First-month Langfuse review to validate these LLM cost estimates against actual spend

---

## 6. Assumptions & Caveats

- **LLM prices** are from `api/services/llm.py:50-78` as of the repo state on 2026-04-11. OpenRouter and Anthropic pricing can change; re-check before committing to a fiscal-year budget.
- **Volume of 75/month** is a midpoint estimate and should be re-validated with Langfuse data after the first month of production use.
- **Prompt caching is not currently implemented.** Anthropic's prompt caching would save an estimated 5–30% on input token costs by caching the ~6KB agent instructions that repeat across every phase call. This is an easy future win but not baked into the numbers here.
- **Airtable read-only access** is already covered by PBS WI's existing Airtable subscription — zero incremental cost.
- **Escalation overhead (~5%)** is based on Sprint 14 analysis of 159 historical jobs; failures are overwhelmingly infrastructure (missing files, logging), not model failures, so this should be a stable assumption.
- **Multi-tenant deployment** (separate orgs/user accounts) is explicitly **not** in scope for 5.0 and would require a Postgres migration + auth refactor — a different conversation.
- **Embedded chat feature** token usage is not modeled here. If chat becomes heavily used it will add load, but the $1/run cost cap still bounds the damage.

---

## 7. Key Files Referenced

| File                                       | Relevance                                        |
|--------------------------------------------|--------------------------------------------------|
| `api/services/llm.py` (lines 50-78)        | Model pricing table driving the LLM estimates    |
| `api/services/llm.py` (lines 269-356)      | Cost cap safety guards already enforced          |
| `api/services/worker.py` (lines 134-153)   | 4-phase processing pipeline                      |
| `config/llm-config.json`                   | Tier routing + duration thresholds               |
| `Dockerfile.{api,worker,web}`              | Production container definitions (already exist)|
| `docker-compose.prod.yml`                  | Production Compose with GHCR images + Watchtower |
| `.github/workflows/deploy.yml`             | CI pipeline publishing images to GHCR            |
| `planning/DESIGN_4.0.md`                   | "Phase A: Home VM" deployment vision             |
| `planning/claude-progress.txt` (Sprint 14) | Escalation/failure rate data                     |

---

## 8. Recommended Next Step

If the boss approves this direction, the smallest useful next step is: **spin up Cardigan on Fly.io in a staging configuration for one month, with Langfuse tracking enabled.** That gives us real data to replace the estimates in Section 2 before committing to a permanent budget line, and costs less than $40 to run the experiment.
