# Remote Access — Temporary Preview via Tailscale Funnel + Hardened Password Gate

**Date:** 2026-07-16
**Status:** Design approved, pending implementation plan
**Scope:** Small feature. Give 1–2 remote editors a password-protected URL to
the production `cardigan01` dashboard, with **nothing to install on their end** —
hardened after a security review (see "Security posture").

---

## Context & goal

Cardigan runs on the `cardigan01` homelab LXC (CTID 103), reachable today only
on the home LAN / Tailscale with **no login**. We want 1–2 trusted PBS Wisconsin
editors to preview the web dashboard remotely.

This is explicitly **temporary**. Cardigan's eventual home is a container at WPM,
where access will be controlled by WPM's internal VPN and no app-level gating is
needed. So this design optimizes for **fewest new pieces to stand up and fewest
to tear down later** — not durability.

A prior plan (PR #208) chose Cloudflare Tunnel + Cloudflare Access (per-email
magic-link allowlist) + an origin API key. It was documented but never executed.
Re-evaluating from scratch under the "temporary preview" framing, that plan is
too heavy to stand up and unwind. We replace it.

### Success criteria

- An editor receives **a URL + their own password** and nothing else; no account,
  no app, no client install.
- Standing it up adds **no new long-running daemon or service** beyond what
  already runs on the box.
- The remotely-reachable surface is **shrunk to the producer workflow** — the
  app-admin / config surface is not exposed.
- Access is **attributable per person** and **individually revocable**.
- Tearing it down when WPM's VPN lands is: remove one secret + one Funnel command.
  App behavior then returns byte-identical to today.

---

## Chosen approach

**Tailscale Funnel (exposure) + a hardened nginx vhost (per-person HTTP Basic
Auth over a reduced endpoint surface).**

Rationale: both dependencies are *already installed and running* on `cardigan01`
— Tailscale (the node) and nginx (inside the `web` container). Tailscale Funnel
exposes a service to the **public internet** over HTTPS with a valid cert;
public users need **no Tailscale account and no client** (this distinguishes
Funnel from Tailscale node-sharing). Funnel has no built-in auth, so nginx is the
gate.

The funnel targets a **dedicated hardened server block**, separate from the
existing LAN vhost, so the operator's daily LAN/Tailscale workflow is untouched
(no password prompts, Settings still works locally):

```
Editor's browser
  |  https://cardigan01.<tailnet>.ts.net   (public, valid TLS)
  v
Tailscale Funnel ingress  (TLS terminated at Tailscale edge; sets X-Forwarded-For)
  |
  v
cardigan01 host :3101  ->  web container nginx :3001   [ HARDENED PREVIEW VHOST ]
  |   - HTTP Basic Auth (per-person htpasswd)  <- the gate
  |   - rate-limited auth (keyed on real client IP via X-Forwarded-For)
  |   - location /api/config  -> 403   (Settings / model-backend writes blocked)
  |   - location /mcp/         -> 403   (SST-write tools never exposed)
  |
  +-- /            SPA (static files)
  +-- /api/        proxy -> api:8000   (producer workflow: queue/jobs/ingest/export/upload)
  +-- /api/ws/     proxy -> api:8000   (live updates)

cardigan01 host :3100  ->  web container nginx :3000   [ EXISTING LAN VHOST — unchanged ]
  (no auth, full surface, LAN / Tailscale only, NOT funneled)
```

Only the hardened preview port is funneled. The LAN vhost (`:3100`), the API
(`:8100`), and MCP (`:8180`) are never funneled. Because the SPA calls `/api/*`
**same-origin** through nginx, there is no CORS surface and no `CORS_ORIGINS`
change.

### Rejected alternatives

- **Cloudflare Tunnel + same gate** — same editor experience but adds a
  `cloudflared` daemon + a DNS zone (re-raising the personal-domain question),
  both to be torn down later. Only worth it for a prettier hostname.
- **Cloudflare Access** — real edge identity + MFA + email OTP (still nothing for
  editors to install). Most fully resolves the security concern, but heaviest to
  stand up and unwind; deferred as the escalation path if the hardened preview
  proves insufficient.
- **Single shared password over the full surface** — the original sketch;
  rejected in the security review (no attribution, and it left the config-write
  surface one public password away — see below).

---

## Security posture (why the design is shaped this way)

A review of "public URL + password" surfaced concrete risks. Findings:

**Confirmed safe / already contained:**
- **The Airtable SST cannot be corrupted via the funnel.** There is no
  Airtable-write endpoint in the REST API; SST writes exist only in the MCP
  server (`propose → commit`), which is not funneled (and `/mcp/` is 403'd on the
  preview vhost regardless).
- `secrets/` is gitignored, so the htpasswd file cannot leak into git.

**Risks the hardening addresses:**
- **The URL is public, not secret.** Tailscale provisions a Let's Encrypt cert
  for `cardigan01.<tailnet>.ts.net`, publishing the hostname to public
  Certificate Transparency logs. Treat the URL as discoverable; the password is
  the whole wall. → *strong per-person passwords + rate-limiting.*
- **Config-write = data-exfiltration path.** `PATCH /api/config/*` (the Settings
  page) can register an arbitrary OpenAI-compatible model backend and route a
  pipeline phase through it — i.e., send transcripts to an attacker's server or
  tamper with outputs. → *`/api/config` is 403'd on the preview vhost;* operators
  change config via the API over Tailscale during a preview window.
- **No attribution / no per-person revocation with a shared secret.** → *per-person
  htpasswd entries;* nginx access log records `$remote_user`. Revoke by deleting
  one line + `docker compose up -d web`.
- **Basic auth has no lockout.** A discoverable URL invites 24/7 brute-force. →
  *nginx `limit_req` on the auth,* keyed on the real client IP (`real_ip` from the
  Funnel ingress via `X-Forwarded-For`, since all funnel traffic otherwise shares
  the ingress source IP).

**Explicitly noted correction:** the origin `CARDIGAN_API_KEY` is **not** a second
layer for the funnel path — nginx *injects* that key for any request that passes
basic auth, so it only protects the direct `:8100` port (not funneled). The real
second layer here is the **surface-shrink**, not the origin key. Origin key stays
off for the preview.

**Residual, accepted risks (short window, watched):**
- Queuing jobs burns OpenRouter budget (cost-DoS) — accepted; monitor spend.
- `/api/upload` media parsing (whisper/ffmpeg) is reachable — accepted; upload is
  a legitimate producer feature. Revisit if not needed by the editors.
- Tailscale terminates TLS at its edge (as Cloudflare would in the alternative).

---

## Design decisions

- **Gate/hardening baked into the web image, dormant by default.** The preview
  vhost + auth + denies are switched on solely by the *presence* of the htpasswd
  secret — mirroring the existing `CARDIGAN_API_KEY` "empty = off" pattern. Secret
  absent → the image behaves byte-identical to today (LAN vhost only). Versioned,
  CI-tested.
- **Per-person passwords, not one shared** (e.g. `alice`, `bob` — multiple
  `user:hash` lines in one htpasswd file). Same mechanism, same "nothing to
  install" UX; restores attribution + individual revocation.
- **Dedicated hardened vhost** so the LAN/operator experience is untouched. (A
  simpler single-block alternative — apply auth + denies to the existing `:3000`
  block gated on the preview toggle — is possible but blocks Settings and prompts
  for a password on LAN too during a preview; rejected to keep daily work clean.)
- **Remove the old Cloudflare artifacts entirely** — one clear access story.

---

## In-repo changes (the feature)

### 1. `nginx.conf`

- Add a **second `server` block** (the hardened preview vhost, e.g. `listen
  3001;`) alongside the existing `:3000` block, rendered by envsubst placeholders
  so it is present only when preview mode is enabled:
  - `auth_basic "Cardigan Preview";` + `auth_basic_user_file /etc/nginx/.htpasswd;`
  - `location = /api/config { return 403; }` and `location /api/config/ { return 403; }`
  - `location /mcp/ { return 403; }`
  - `limit_req` referencing a `limit_req_zone` in the `http` block; `real_ip`
    config to trust the Funnel ingress and read `X-Forwarded-For`.
  - the producer locations (`/`, `/api/`, `/api/ws/`) proxying as the LAN block does.
- The existing `:3000` LAN block is left unchanged.

### 2. `Dockerfile.web`

Extend the entrypoint (currently a single `envsubst` line):

- If `/run/secrets/cardigan_web_htpasswd` (or `CARDIGAN_WEB_HTPASSWD`) is present
  and non-empty: write it to `/etc/nginx/.htpasswd` and render the preview vhost +
  auth directives. Otherwise render them empty (preview off).
- Extend the `envsubst '...'` allowlist to include the new vars.

The secret content is complete htpasswd lines (`user:hash`), so **no new packages**
are added to the `nginx:alpine` image at runtime.

### 3. `docker-compose.prod.yml` and `docker-compose.yml`

- Add an **optional** `cardigan_web_htpasswd` secret to the `web` service + a
  top-level `secrets:` entry (`file: ./secrets/cardigan_web_htpasswd`). Absent
  file = dormant.
- Publish the hardened preview port on `web` (e.g. `"3101:3001"`) — this is the
  only port Funnel targets.
- **Remove the `tunnel` service** (cloudflared) from both compose files, its
  `tunnel` profile, and the `CLOUDFLARE_TUNNEL_TOKEN` env reference.

### 4. `scripts/set_preview_password.sh` (new)

Prompts for a username + password (repeatable for multiple people) and appends a
line to `secrets/cardigan_web_htpasswd` using `htpasswd -nbB <user>` (bcrypt;
`htpasswd` ships on macOS), falling back to apr1 if `-B` is unavailable. Supports
`--revoke <user>` (delete that line). Prints a reminder to `docker compose up -d
web` and to share each password over a secure channel (Signal / 1Password link).

### 5. Remove stale Cloudflare wiring

- Delete `config/cloudflared.yml` (contains a stale Mac-dev tunnel UUID).
- `scripts/start.sh` — remove the `ENABLE_TUNNEL` block + cloudflared launch
  (~lines 84–101) and the tunnel status/URL echo (~lines 143–147).
- `scripts/stop.sh` — remove the `pkill -f 'cloudflared tunnel'` line (~13).
- `scripts/status.sh` — remove the cloudflared process + endpoint checks (~60–68).
- `.env.example` — drop the `secrets/cloudflare_tunnel_token` line; add a note for
  the optional `secrets/cardigan_web_htpasswd`.

> The Funnel command runs on the **box host**, outside Docker, so it is not wired
> into these (Mac-oriented) scripts. It lives in the runbook (below).

---

## Box-side runbook (ops — goes in `docs/REMOTE_ACCESS.md`)

1. **One-time tailnet admin:** enable the Funnel node attribute for `cardigan01`
   and HTTPS/MagicDNS in the tailnet ACL policy.
2. **Set passwords:** run `scripts/set_preview_password.sh` once per editor; land
   `secrets/cardigan_web_htpasswd` on the box; `docker compose up -d web`.
3. **Turn on the funnel** against the hardened port: `tailscale funnel --bg 3101`
   → `https://cardigan01.<tailnet>.ts.net`.
4. **Onboard an editor:** send the URL + their password over Signal / a 1Password
   link. Set a teardown/rotation date.
5. **Revoke one person:** `scripts/set_preview_password.sh --revoke <user>` +
   `docker compose up -d web`.
6. **Tear down** (WPM VPN lands): `tailscale funnel reset`; remove
   `secrets/cardigan_web_htpasswd`; `docker compose up -d web`. Back to today's state.

---

## Docs to update

- **`docs/REMOTE_ACCESS.md`** — full rewrite (currently a stale *Mac* Vite-dev
  Cloudflare tunnel). Replace with the Funnel + hardened-gate model + runbook.
- **`docs/PRODUCER_ONBOARDING.md`** — trim to the password flow; stamp **"temporary
  preview — retires when WPM VPN lands"**; note Settings is unavailable remotely.

---

## Testing & verification

**Local (docker compose up):**
- With `secrets/cardigan_web_htpasswd` present, against the **preview port (3101)**:
  - No credentials → **401**.
  - `curl -u alice:<pw>` → **200**, SPA served.
  - `GET/PATCH /api/config...` → **403** (surface-shrink proven).
  - `/mcp/...` → **403**.
  - Authenticated browser: SPA loads; the **live-updates WebSocket** (`/api/ws/jobs`)
    connects and streams. *Verify explicitly* — the browser must pass cached
    basic-auth creds on the WS handshake (PR #208 had to fix WS auth).
  - Repeated bad passwords → rate-limited (429/503 after the burst).
  - `$remote_user` appears in the access log (attribution).
- Against the **LAN port (3100)**: unchanged — no auth, `/api/config` reachable.
- With the secret **absent**: the preview vhost is not rendered; behavior
  byte-identical to today.

**On the box (post-deploy smoke):**
- Funnel URL prompts for a password; a valid per-person password loads the dashboard.
- A job's phases update live through the funnel.
- Through the funnel: `/api/config` and `/mcp/` return 403; `:8100` and `:8180`
  are unreachable.

---

## Out of scope

- Per-person identity beyond htpasswd, SSO, MFA, branded login/logout (WPM VPN
  will own real auth later; Cloudflare Access is the escalation path if needed).
- Exposing the API or MCP publicly (operators keep using Tailscale
  `cardigan01:8100` with a scoped consumer key).
- Per-user *authorization* inside the app (the funnel shrink is path-level at
  nginx, not role-based in the API).
