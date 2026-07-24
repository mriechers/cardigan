# Getting Access to Cardigan — Producer Onboarding

Cardigan (a.k.a. *The Metadata Neighborhood*) turns a finished transcript into
SEO-ready metadata — Release Title, Short/Long Description, Keywords — so you
don't have to write all of it by hand.

> **Temporary preview — retires when the WPM VPN lands.** Right now a couple of
> trusted editors can reach Cardigan through a password-protected URL. This is a
> stopgap: once Cardigan moves behind WPM's internal VPN, this per-password door
> goes away and access is handled at the network. Nothing below is permanent.

---

## Part A — Admin steps (one-time per producer)

The full box-side setup — turning on the Tailscale Funnel and the hardened
password gate — lives in **`docs/REMOTE_ACCESS.md`**. To add one producer:

1. **Set them a password.** Run `scripts/set_preview_password.sh` (once per
   person), land the updated `secrets/cardigan_web_htpasswd` on the box, and
   `docker compose up -d web`.
2. **Send them** the URL + their password over a secure channel (Signal, or a
   1Password shared item) — plus Part B below. **Use a strong, unique password:**
   the URL is public (it shows up in Certificate Transparency logs), so the
   password is the whole wall.

That's it — the dashboard is the whole job. Only the hardened web vhost is exposed
through the Funnel; the raw API (`:8100`) and the SST-write MCP tools stay private.
Remotely, **Settings is unavailable** (config changes happen on the box over
Tailscale), so there are no keys or tokens for the producer to handle.

To remove someone later: `scripts/set_preview_password.sh --revoke <user>` +
`docker compose up -d web`.

---

## Part B — Producer guide (hand this to the new producer)

### 1. Log in
Open the Cardigan URL your admin sent you. Your browser will ask for a **username
and password** — enter the ones you were given. That's it: **nothing to install**,
no account to create, no app to download. (If you ever land on a **Settings** page
that reads *"Configuration unavailable in remote application."* — that's expected;
Settings is off in the remote preview. Everything you need is on the other pages.)

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
