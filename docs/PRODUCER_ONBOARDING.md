# Getting Access to Cardigan — Producer Onboarding

Cardigan (a.k.a. *The Metadata Neighborhood*) turns a finished transcript into
SEO-ready metadata — Release Title, Short/Long Description, Keywords — so you
don't have to write all of it by hand.

> **Access status (2026-06-10):** Cardigan runs on the `cardigan01` homelab
> container. Today it's reachable only on the home LAN / Tailscale and has **no
> login**. The producer-facing front door — a **Cloudflare Tunnel + Cloudflare
> Access** (browser login, nothing to install) — is being stood up; see the
> infra handoff in
> `homelab/proxmox-config/containers/cardigan01/planning/2026-06-10-secure-access-handoff.md`.
> A longer-term option (Cardigan behind **WPM's official VPN**) is under
> discussion with IT. This doc describes the **Cloudflare Access** experience the
> near-term setup delivers.

---

## Part A — Admin steps (one-time per producer)

Prerequisite (one-time, infra): the Cloudflare Tunnel + Access application for
`cardigan.<domain>` exist, and the origin API key is set (see "Defense in depth"
below). Those are tracked in the handoff note above.

1. **Add the producer to the Access policy.** Cloudflare Zero Trust → **Access →
   Applications →** the Cardigan app → the "Email allowlist" policy → add their
   PBS Wisconsin email. They authenticate at Cloudflare's edge (magic-link /
   one-time PIN) — unauthenticated traffic never reaches the homelab.
2. **Send them** the URL + Part B.
3. **(If they run a local editing agent)** issue them a Cloudflare Access
   **service token** *and* a scoped Cardigan **consumer key** — see "Agent
   access" below.

### Defense in depth — the origin API key

Cloudflare Access is the *edge* gate (who reaches the box). Cardigan also
enforces an *origin* gate: when `CARDIGAN_API_KEY` is set, every `/api/*` call
(and the live-updates WebSocket) must carry the key. The web container's nginx
injects it automatically, so **producers never see or handle it** — the
dashboard just works. This also closes the current LAN/Tailscale "no auth" hole.
(Enabling it requires the API image that includes the WS header-auth fix — see
the handoff note.)

---

## Part B — Producer guide (hand this to the new producer)

### 1. Log in
Open the Cardigan URL your admin sent you. You'll see a Cloudflare login page —
enter your PBS Wisconsin email, then click the magic link (or enter the one-time
code) sent to your inbox. You're in for the session. **Nothing to install.**

### 2. What Cardigan does for you
You feed it a finished transcript; it runs a four-phase AI pipeline and returns
polished, SEO-optimized metadata for our streaming platforms. Think of it as a
calm, reliable workstation that does the tedious drafting so you can review.

### 3. Ready for Work
The **Ready for Work** page lists transcripts that have arrived on the ingest
server and are waiting. Pick the episode(s) you want and **queue** them. The list
refreshes on its own; there's also a manual "Check for New Files" button.

### 4. Run & monitor a job
A queued job runs through four phases with live status. If a phase fails, you can
**retry just that phase** — no need to start over. A finished job shows as
**completed**.

### 5. Review the output
Open a completed job to read its metadata: Release Title, short description, long
description, keywords. Read it like a colleague's first draft — you have final
say.

### 6. Push to AirTable
When you're happy, Cardigan can write the approved fields back to our AirTable
Single Source of Truth. It always shows a **preview of the exact changes** (old →
new) and waits for your confirmation. Only the editorial fields are writable
(Release Title, Short/Long Description, Keywords, social), and every write leaves
an audit comment on the record.

### 7. Getting help
Can't get in, or something looks off? Ping **Mark Riechers** — include the
episode / Media ID and a screenshot if you can.

---

## Agent access (for the AI editing workflow)

The agent-driven editing workflow calls the same API the dashboard uses, so a
local agent on your machine may need API access alongside the GUI. Because that
traffic isn't a browser, it authenticates differently:

- **Through the Cloudflare front door:** a Cloudflare Access **service token**
  (a `CF-Access-Client-Id` + `CF-Access-Client-Secret` header pair) gets the
  agent past the edge without a human login, **plus** a Cardigan **consumer key**
  (`X-API-Key`, scoped — e.g. `mmingest:read`) for the origin gate. Your admin
  issues both; treat them as secrets.
- **On the homelab LAN / Tailscale (operator machines):** no service token needed
  — reach the API directly (`cardigan01:8100`) with just the consumer key.

> Mint a consumer key with
> `python scripts/create_consumer_key.py --label <who> --scopes mmingest:read`
> (prints the key once; `--revoke <id>` to disable). Cloudflare service tokens
> are created in Zero Trust → Access → Service Auth.
