# Remote Access via Cloudflare Tunnel

The Metadata Neighborhood can be accessed remotely at
`https://cardigan.bymarkriechers.com` via a Cloudflare Tunnel.

## Architecture

```
Remote browser (https://cardigan.bymarkriechers.com)
  |
  | HTTPS / WSS
  v
Cloudflare Edge (TLS termination + Access auth)
  |
  | cloudflared tunnel (encrypted QUIC, outbound from your Mac)
  v
localhost:3100 (Vite dev server)
  |
  | proxy /api/* and /api/ws/*
  v
localhost:8100 (FastAPI / uvicorn)
```

No ports are opened on your machine. The tunnel is outbound-only.

## One-Time Setup

### 1. Install cloudflared

```bash
brew install cloudflared
cloudflared login  # Opens browser to authenticate with Cloudflare
```

### 2. Create the tunnel

```bash
cloudflared tunnel create cardigan
```

This outputs a tunnel UUID (e.g., `a1b2c3d4-...`) and creates a
credentials file at `~/.cloudflared/<UUID>.json`.

### 3. Update the config

Edit `config/cloudflared.yml` and replace both instances of `<TUNNEL_UUID>`
with the UUID from step 2.

### 4. Create DNS route

```bash
cloudflared tunnel route dns cardigan cardigan.bymarkriechers.com
```

This creates a CNAME record in the `bymarkriechers.com` zone pointing
`cardigan` to the tunnel.

### 5. Configure Cloudflare Access (recommended)

In the [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com/):

1. Go to **Access > Applications > Add an application**
2. Type: **Self-hosted**
3. Application domain: `cardigan.bymarkriechers.com`
4. Policy name: "Email allowlist"
5. Action: **Allow**
6. Include rule: **Emails** — add approved colleague email addresses
7. Session duration: 24 hours (or preferred)

Users will see a Cloudflare login page, enter their email, click a
magic-link verification, and receive a 24-hour session cookie.

### 6. Enable in .env

```bash
# In your project .env file:
ENABLE_TUNNEL=true
```

### 7. Start the neighborhood

```bash
./scripts/start.sh
```

The tunnel will start alongside the other services.

## Daily Usage

The tunnel starts and stops with the other services:

```bash
./scripts/start.sh   # Starts everything including tunnel
./scripts/stop.sh    # Stops everything including tunnel
./scripts/status.sh  # Shows tunnel status
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Tunnel log errors | `tail -f logs/tunnel.log` |
| "cloudflared not installed" | `brew install cloudflared` |
| "config not found" | Update `<TUNNEL_UUID>` in `config/cloudflared.yml` |
| 403 from Vite | Verify `cardigan.bymarkriechers.com` is in `allowedHosts` in `web/vite.config.ts` |
| CORS errors on API | Verify tunnel origin is in `allow_origins` in `api/main.py` |
| WebSocket not connecting | Verify `ws: true` in Vite proxy config |
| Tunnel info | `cloudflared tunnel info cardigan` |
| List tunnels | `cloudflared tunnel list` |
