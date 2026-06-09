# Getting Access to Cardigan — Producer Onboarding

Cardigan (a.k.a. *The Metadata Neighborhood*) turns a well-edited transcript into
SEO-ready metadata — Release Title, Short/Long Description, and Keywords — so you
don't have to write all of that by hand. It runs as a web dashboard at
**`https://cardigan.bymarkriechers.com`**.

This guide has two parts: **admin steps** (done once per new producer by the
person who runs the server) and a **producer guide** you can hand straight to a
colleague.

---

## Part A — Admin steps (one-time per producer)

Access is gated by **Cloudflare Access** (an email allowlist with magic-link
login) sitting in front of the Cloudflare Tunnel. No API keys, passwords, or
config touch the producer's machine — you just add their email.

1. **Add the producer's email to the allowlist.**
   In the [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com/) →
   **Access → Applications →** the `cardigan.bymarkriechers.com` application →
   the "Email allowlist" policy → add their PBS Wisconsin email under the
   **Emails** include rule. (Same policy described in `docs/REMOTE_ACCESS.md` §5.)

2. **Confirm the tunnel is up.** In production the connector runs as the
   `tunnel` service in the compose stack:
   ```bash
   docker compose -f docker-compose.prod.yml --profile tunnel ps tunnel
   ```
   It should be `Up`. (If you run the tunnel via `./scripts/start.sh` on a laptop
   instead, `./scripts/status.sh` shows it.)

3. **Send the producer** the dashboard URL and Part B below.

> **Heads-up on identity:** today every dashboard user shares one backend API
> key (injected by the web container's nginx). Cloudflare Access controls *who
> can get in*, but the app itself doesn't yet attribute actions to individual
> producers. Per-user identity/audit inside the app is a future enhancement. If
> you need to revoke someone, remove their email from the Access policy.

---

## Part B — Producer guide (hand this to the new producer)

### 1. Log in
Open **`https://cardigan.bymarkriechers.com`**. You'll see a Cloudflare login
page — enter your PBS Wisconsin email, then click the magic link sent to your
inbox. You're in for 24 hours before it asks again. Nothing to install.

### 2. What Cardigan does for you
You feed it a finished transcript; it runs a four-phase AI pipeline and hands
back polished, SEO-optimized metadata for our streaming platforms. Think of it
as a calm, reliable workstation that does the tedious duplicative writing so you
can review instead of draft from scratch.

### 3. Ready for Work
The **Ready for Work** page lists transcripts that have shown up on the ingest
server and are waiting to be processed. Browse the list, pick the episode(s) you
want, and **queue** them. (The system scans for new arrivals automatically, so
this list refreshes on its own — but there's a manual "Check for New Files"
button if you want to force a fresh look.)

### 4. Run & monitor a job
Once queued, a job runs through four phases. You'll see live status as it
progresses. If a phase fails, you can **retry** just that phase — you don't have
to start over. A finished job shows as **completed**.

### 5. Review the output
Open a completed job to read its generated metadata: Release Title, a short
description, a long description, and keywords. Read it the way you'd read a
colleague's first draft — it's good, but you have final say.

### 6. Push to AirTable
When you're happy, Cardigan can write the approved fields back to our AirTable
Single Source of Truth. It always shows you a **preview of the exact changes**
first (old value → new value) and waits for your confirmation before writing.
Only the editorial fields are writable (Release Title, Short/Long Description,
Keywords, social copy), and every write leaves an audit comment on the record —
so nothing happens behind your back.

### 7. Getting help
Something looks off, or you can't get in? Ping **Mark Riechers** — include the
episode/Media ID and a screenshot if you can.

---

> **Programmatic / MCP access** (consumer API keys with scopes, via
> `scripts/create_consumer_key.py`) is a separate path for tools and Claude
> Desktop integrations — not needed for dashboard producers. A future
> `docs/API_ONBOARDING.md` will cover it.
