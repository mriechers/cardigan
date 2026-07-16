# Remote Access — Temporary Preview via Tailscale Funnel + Shared Password

**Date:** 2026-07-16
**Status:** Design approved, pending implementation plan
**Scope:** Small feature. Give 1–2 remote editors a password-protected URL to
the production `cardigan01` dashboard, with **nothing to install on their end**.

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

- An editor receives **a URL + a password** and nothing else; no account, no app,
  no client install.
- Standing it up adds **no new daemon or service** that must run long-term (beyond
  what already runs on the box).
- Tearing it down when WPM's VPN lands is: remove one secret + one Funnel command.
  App behavior then returns byte-identical to today.

---

## Chosen approach

**Tailscale Funnel (exposure) + nginx HTTP Basic Auth (a single shared password).**

Rationale: both dependencies are *already installed and running* on `cardigan01`
— Tailscale (the node) and nginx (inside the `web` container). Tailscale Funnel
exposes a service to the **public internet** over HTTPS with a valid cert;
crucially, public users need **no Tailscale account and no client** (this is what
distinguishes Funnel from Tailscale node-sharing). Funnel has no built-in auth, so
a single shared password enforced at nginx is the gate.

```
Editor's browser
  |  https://cardigan01.<tailnet>.ts.net   (public, valid TLS)
  v
Tailscale Funnel ingress  (TLS terminated at Tailscale edge, re-encrypted to box)
  |
  v
cardigan01 host :3100  ->  web container nginx :3000
  |   [ nginx HTTP Basic Auth — the password gate ]
  |
  +-- /            SPA (static files)
  +-- /api/        proxy -> api:8000   (same-origin; password covers it)
  +-- /api/ws/     proxy -> api:8000   (WebSocket; same-origin)
  +-- /mcp/        proxy -> mcp:8080   (optional)
```

Only the web port (`:3100`) is funneled. The API (`:8100`) and MCP (`:8180`)
stay private / tailnet-only. Because the SPA calls `/api/*` **same-origin**
through nginx, there is no CORS surface and no `CORS_ORIGINS` change.

### Rejected alternatives

- **Cloudflare Tunnel + same password** — same editor experience but adds a
  `cloudflared` daemon + a DNS zone (re-raising the personal-domain question),
  both to be torn down later. Only worth it for a prettier hostname, which
  doesn't justify the cost for a throwaway preview.
- **Execute the Cloudflare Access plan (PR #208)** — nicest UX and per-person
  identity, but heaviest to stand up and unwind. Overkill for a 2-person preview
  and it's exactly the thing that sat un-executed.

---

## Design decisions

- **Gate location: in the web image, dormant by default.** The auth is baked into
  `nginx.conf` + `Dockerfile.web`, switched on solely by the *presence* of a
  secret — mirroring the existing `CARDIGAN_API_KEY` "empty = off" pattern. When
  the secret is absent, behavior is byte-identical to today. This is versioned and
  CI-tested, unlike a box-only overlay.
- **One shared password, single account** (`producer`). No per-person identity —
  appropriate for 1–2 trusted editors, temporarily. The password is the entire
  wall (Funnel is genuinely public), so it must be strong and shared over a secure
  channel (Signal / 1Password link — never plaintext email).
- **Origin `CARDIGAN_API_KEY` stays off for the preview.** Basic auth is the gate.
  The layers remain composable if both are ever wanted, but two secrets is more to
  manage for no added protection here (only the funneled, password-gated web
  surface is publicly reachable).
- **Remove the old Cloudflare artifacts entirely** — one clear access story.

---

## In-repo changes (the feature)

### 1. `nginx.conf`

Add basic-auth to the `server` block so it covers the SPA and all proxied
locations in one place. Use envsubst placeholders so the directives are present
only when enabled:

- Introduce `${AUTH_BASIC}` and `${AUTH_BASIC_USER_FILE}` placeholders at the
  `server` (or per-`location`) level.
- When enabled they render to `auth_basic "Cardigan Preview";` and
  `auth_basic_user_file /etc/nginx/.htpasswd;`. When disabled they render to
  empty strings.

### 2. `Dockerfile.web`

Extend the entrypoint (currently a single `envsubst` line):

- If `/run/secrets/cardigan_web_htpasswd` (or a `CARDIGAN_WEB_HTPASSWD` env value)
  is present and non-empty: write it to `/etc/nginx/.htpasswd` and set the two
  auth placeholders to the real directives.
- Otherwise: set both placeholders to empty (auth off).
- Extend the `envsubst '...'` allowlist to include the two new vars.

The secret content is a complete htpasswd line (`user:hash`), so **no new
packages** are added to the `nginx:alpine` image (no `htpasswd`/`openssl` needed
at runtime).

### 3. `docker-compose.prod.yml` and `docker-compose.yml`

- Add an **optional** `cardigan_web_htpasswd` secret to the `web` service and a
  matching entry under top-level `secrets:` (`file: ./secrets/cardigan_web_htpasswd`).
  When the file is absent, the gate stays dormant.
- **Remove the `tunnel` service** (cloudflared) from both compose files, plus its
  `tunnel` profile and the `CLOUDFLARE_TUNNEL_TOKEN` env reference.

### 4. `scripts/set_preview_password.sh` (new)

A tiny helper so no one hand-hashes: prompts for a password (or takes it as an
arg) and writes `secrets/cardigan_web_htpasswd` using `htpasswd -nbB producer`
(bcrypt; `htpasswd` ships on macOS). Falls back to the default apr1 hash if the
local `htpasswd` lacks `-B`. Prints a reminder to `docker compose up -d web` and
to share the password over a secure channel.

### 5. Remove stale Cloudflare wiring

- Delete `config/cloudflared.yml` (contains a stale Mac-dev tunnel UUID).
- `scripts/start.sh` — remove the `ENABLE_TUNNEL` block and cloudflared launch
  (~lines 84–101) and the tunnel status/URL echo (~lines 143–147).
- `scripts/stop.sh` — remove the `pkill -f 'cloudflared tunnel'` line (~13).
- `scripts/status.sh` — remove the cloudflared process + endpoint checks (~60–68).
- `.env.example` — drop the `secrets/cloudflare_tunnel_token` line; add a note for
  the optional `secrets/cardigan_web_htpasswd`.

> The Funnel command runs on the **box host**, outside Docker, so it is not wired
> into these (Mac-oriented, Vite-dev) scripts. It lives in the runbook (below).

---

## Box-side runbook (ops, not code — goes in `docs/REMOTE_ACCESS.md`)

1. **One-time tailnet admin:** enable the Funnel node attribute for `cardigan01`
   and HTTPS/MagicDNS in the tailnet ACL policy (a settings toggle).
2. **Set the password:** run `scripts/set_preview_password.sh`, land
   `secrets/cardigan_web_htpasswd` on the box, then `docker compose up -d web` to
   pick it up.
3. **Turn on the funnel:** `tailscale funnel --bg 3100` →
   `https://cardigan01.<tailnet>.ts.net`.
4. **Onboard an editor:** send the URL + password over Signal / a 1Password link.
5. **Tear down** (when WPM's VPN lands): `tailscale funnel reset`; remove
   `secrets/cardigan_web_htpasswd`; `docker compose up -d web`. App is back to its
   pre-preview state.

---

## Docs to update

- **`docs/REMOTE_ACCESS.md`** — full rewrite. It currently describes a *Mac* Vite-
  dev-server Cloudflare tunnel (stale). Replace with the Funnel + password model +
  the runbook above.
- **`docs/PRODUCER_ONBOARDING.md`** — trim Part A/B to the password flow; stamp it
  clearly **"temporary preview — retires when WPM VPN lands."** Remove the
  Cloudflare Access / origin-key language as the primary path.

---

## Testing & verification

**Local (docker compose up, web only):**
- With `secrets/cardigan_web_htpasswd` present:
  - `curl` without credentials → **401**.
  - `curl -u producer:<pw>` → **200**; SPA `index.html` served.
  - Authenticated browser: SPA loads; the **live-updates WebSocket**
    (`/api/ws/jobs`) connects and streams. *This is the one thing to verify
    explicitly* — the browser must pass cached basic-auth creds on the WS
    handshake (PR #208 had to fix WS auth; it gets its own test).
  - `/api/*` reachable only with credentials.
- With the secret **absent**: no `401` anywhere — behavior byte-identical to
  today (dormant-by-default proven).

**On the box (post-deploy smoke):**
- Funnel URL prompts for the password; correct password loads the dashboard.
- A job's phases update live through the funnel.
- API port `:8100` and MCP `:8180` are **not** reachable via the funnel URL.

---

## Out of scope

- Per-person identity, SSO, logout, branded login page (WPM VPN will own real
  auth later).
- Exposing the API or MCP publicly (operators keep using Tailscale
  `cardigan01:8100` with a scoped consumer key).
- Rate limiting / DDoS protection (Funnel + a strong password is sufficient for a
  short-lived 2-person preview; the box is otherwise private).
