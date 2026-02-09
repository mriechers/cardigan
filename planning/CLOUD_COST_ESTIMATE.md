# Cloud Hosting Cost Estimate

This document provides cost estimates for running the PBS Wisconsin Editorial Assistant v3.0 in cloud infrastructure versus locally.

## Architecture Summary

- **FastAPI backend** (Python 3.13, async)
- **React web dashboard** (Vite/TypeScript)
- **SQLite database** (~2.7 MB currently)
- **Background job worker** (single-worker polling loop)
- **MCP server** for Claude Desktop integration

---

## Infrastructure Cost Options

| Option | Monthly Cost | Best For |
|--------|-------------|----------|
| **VPS (DigitalOcean/Linode)** | $8-15 | Getting started, low volume |
| **AWS EC2 (t3.small)** | $25-35 | Scalability, managed backups |
| **Home VM via Proxmox** | $15-30 | Already have hardware |

### What's included in infrastructure

- 1-2 vCPU, 1-2 GB RAM (sufficient for single-worker)
- 50 GB storage for outputs/transcripts
- Basic backup/snapshot service

---

## Variable Costs (LLM API)

The **LLM API costs dominate** (75-90% of operational costs):

| Model Tier | Cost per 1M tokens | Typical Job Cost |
|------------|-------------------|------------------|
| Gemini 2.5 Flash (default) | $0.15 in / $0.60 out | $0.02-0.10 |
| Gemini 1.5 Pro (big-brain) | $1.25 in / $5.00 out | $0.10-0.50 |
| Claude 3.5 Sonnet (premium) | $3.00 in / $15.00 out | $0.25-1.00 |

### Built-in Cost Controls

The system has built-in cost controls configured in `config/llm-config.json`:

- `DEFAULT_RUN_COST_CAP = $1.0` per job
- `DEFAULT_MAX_COST_PER_1K_TOKENS = $0.05`
- Tiered model selection (cheap models first, escalate on failure)

---

## Total Monthly Cost Estimates

| Scenario | Jobs/Month | Infrastructure | LLM API | **Total** |
|----------|-----------|----------------|---------|-----------|
| Light use | 50 | $12 (VPS) | $5 | **~$17/mo** |
| Moderate | 200 | $12 (VPS) | $20 | **~$32/mo** |
| Heavy | 500 | $30 (AWS) | $50 | **~$80/mo** |
| Production | 1000 | $35 (AWS) | $150 | **~$185/mo** |

---

## Additional Costs

| Item | Cost | Notes |
|------|------|-------|
| Langfuse (LLM observability) | $0-20/mo | Optional, useful for debugging |
| Cloudflare Tunnel | $0 | Free tier sufficient |
| Automated backups | $1-5/mo | Essential for production |
| Domain/SSL | $0-5/mo | Cloudflare free, or Route 53 |

---

## Deployment Options

### Option A: Home VM (Proxmox)

**Monthly Cost: $15-30 (+ LLM API)**

- Electricity: $5-10
- Hardware depreciation: $10-20
- Maintenance: 2 hrs/month

Best for: Initial development and testing

### Option B: Cloud VPS (DigitalOcean/Linode/Vultr)

**Monthly Cost: $8-15/month (+ LLM API + backups)**

- VPS (1-2 vCPU, 1-2 GB RAM): $6-12
- Backups: $1-2
- Maintenance: 1 hr/month

Best for: Low-to-moderate volume production use

### Option C: AWS EC2/ECS

**Monthly Cost: $25-35/month (+ LLM API)**

- t3.small instance: $15-20
- EBS storage (50 GB): $5
- Data transfer: $5-10
- DNS (Route 53): $1

Best for: Scalability, enterprise requirements

### Option D: Station Infrastructure (Future)

**Monthly Cost: $0 (IT handles)**

- VM allocation, power, network included
- IT support included
- Requires: 1-2 months stable operation first
- Prerequisite: Complete runbooks, cost projections, handoff docs

---

## Scaling Considerations

### Current Design Limits

- **Single-worker architecture:** Processes 1 job at a time (by default)
- **SQLite concurrency:** Limited to 5 concurrent database sessions
- **Memory per job:** ~100-500 MB for large transcripts
- **API server capacity:** ~50-100 concurrent WebSocket connections

### When to Scale

| Trigger | Action | Additional Cost |
|---------|--------|-----------------|
| Queue depth > 20 regularly | Add workers | +$5-10/mo per worker |
| >500 jobs/month | Consider PostgreSQL | +$15-25/mo (RDS) |
| >1000 jobs/month | Multi-region/HA | +$50-100/mo |

---

## Recommendations

### For Minimal Cost

- Start with a **$8-12/mo VPS** (Linode, Vultr, or DigitalOcean)
- Keep SQLite (good for <500 jobs/month)
- Use Cloudflare Tunnel for secure access (free)
- Stick to Gemini Flash tier for most jobs

### For Production Reliability

- AWS EC2 t3.small (~$25/mo) with daily EBS snapshots
- Migrate to PostgreSQL if exceeding 1000 jobs/month
- Set up monitoring/alerting for disk space and error rates
- Implement centralized logging

---

## Production Readiness Checklist

For cloud deployment, ensure the following are in place:

- [ ] Database backup strategy (daily automated snapshots)
- [ ] Monitoring & alerting (disk space, error rates, LLM API errors)
- [ ] Log aggregation (centralized logging service)
- [ ] Rate limiting (per-user, per-IP to prevent abuse)
- [ ] HTTPS/TLS (SSL termination, certificate management)
- [ ] Authentication (Cloudflare Access or OAuth)
- [ ] Cost caps & alerts (OpenRouter API limits, budget notifications)
- [ ] Disaster recovery (backup + restore procedures)

---

## Summary

**Bottom line:** Expect **$15-40/month** for light-to-moderate use, with LLM API costs scaling linearly based on job volume and transcript length. Infrastructure costs are relatively fixed; the main variable is how many jobs you process and which model tiers they require.

| Cost Component | % of Total | Controllable? |
|----------------|-----------|---------------|
| LLM API | 75-90% | Yes (model selection, failure reduction) |
| Compute | 5-15% | Somewhat (right-size instances) |
| Storage | 2-5% | Yes (archival policies) |
| Network | 1-3% | Limited |

---

*Last updated: 2026-02-09*
