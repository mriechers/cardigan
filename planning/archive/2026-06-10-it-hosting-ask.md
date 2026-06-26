# Cardigan Hosting Ask — Email to IT (Draft)

**Date:** 2026-06-10
**Purpose:** Draft email to PBS Wisconsin IT requesting a station-managed home for
Cardigan. Three hosting options laid out (Docker host, LXC container, dedicated
machine + local LLM). Tuned to Mark's voice.

**Architecture basis:** `docker-compose.prod.yml` (api + worker + web + optional
mcp/diarization/tunnel, images from `ghcr.io/mriechers/cardigan-*`); currently runs
Docker-in-LXC on the homelab Proxmox host behind Cloudflare Access. The local-LLM
option (#3) draws on `planning/2026-06-05-local-llm-tier-handoff.md` — the four-phase
pipeline can route individual stages to a local OpenAI-compatible endpoint with
automatic tier-escalation fallback to the cloud API.

---

**Subject: Could we host a little internal tool (Cardigan) somewhere on station hardware?**

Hi [name],

I've built a small internal tool called **Cardigan** that the editorial team's been using to turn finished transcripts into SEO metadata — titles, descriptions, keywords — for our streaming platforms. It's been humming along on my own hardware for a while now and working well, but I'd feel a lot better with it living somewhere station-managed, where it's backed up and supported and not dependent on me. Figured I'd ask what we've actually got before assuming anything.

The short version: it's a lightweight thing — a web dashboard plus a couple of background helpers and a small database. The hard part (the actual AI work) happens out on an external API, so the app itself barely breaks a sweat. It needs modest resources and outbound internet to a handful of services, and the only thing facing inward is the dashboard, which I'd want tucked behind SSO or the VPN.

A few ways it could run, depending on what's already lying around:

1. **On a Docker host, if we have one** — easiest by a mile, and it's exactly how it runs today. I hand you one config file, you give me a spot, done.
2. **In an LXC container** (if we're more of a Proxmox/LXD shop) — works just as well; that's actually my current setup at home, so it's well-trodden.
3. **On a dedicated machine with a real GPU or a lot of memory** — this one's more of a "do we happen to have that?" ask. If we do, I can run the AI model locally instead of paying per use, which would **knock down our ongoing costs and keep everything on our own hardware**. It's slower per job, but our workflow isn't a race, so I don't think we'd feel it. And it's something we could bolt on later — no need to start there.

My gut says #1 is the path of least resistance, but I wanted to put #2 and #3 on the table in case the infrastructure's already sitting there. What've we got that might fit?

Thanks,
Mark
