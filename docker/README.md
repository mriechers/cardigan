# Docker Deployment Guide

Run the Editorial Assistant API and web dashboard as containers.

## Quick Start

```bash
# 1. Copy and fill in environment variables
cp .env.example .env
# Edit .env with your API keys (OPENROUTER_API_KEY, AIRTABLE_API_KEY, etc.)

# 2. Build and start
docker compose up -d

# 3. Verify
docker compose ps
curl http://localhost:8000/          # API health
open http://localhost:3000           # Web dashboard
```

## Architecture

```
┌──────────────┐     ┌──────────────┐
│   web (:80)  │────▶│  api (:8000) │
│   nginx +    │     │  FastAPI +   │
│   React SPA  │     │  Uvicorn     │
└──────────────┘     └──────┬───────┘
                            │
                     ┌──────▼───────┐
                     │   SQLite DB  │
                     │  (volume)    │
                     └──────────────┘
```

**Services:**

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `api` | `Dockerfile.api` | 8000 | FastAPI backend, job processing, LLM orchestration |
| `web` | `web/Dockerfile` | 3000 (→80) | React dashboard served by nginx, reverse-proxies `/api` to the API container |

**Volumes:**

| Volume | Container path | Contents |
|--------|---------------|----------|
| `db-data` | `/app/data` | SQLite database (`dashboard.db`) |
| `output-data` | `/app/OUTPUT` | Processed job outputs |
| `transcript-data` | `/app/transcripts` | Uploaded transcript files |
| `log-data` | `/app/logs` | Application logs |

## Configuration

Environment variables are passed through from the host `.env` file. See `.env.example` for the full list.

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | Primary LLM backend |
| `AIRTABLE_API_KEY` | Yes | Read-only Airtable access |
| `LANGFUSE_PUBLIC_KEY` | No | LLM observability |
| `LANGFUSE_SECRET_KEY` | No | LLM observability |
| `API_PORT` | No | Override API host port (default: 8000) |
| `WEB_PORT` | No | Override web host port (default: 3000) |

## Common Operations

```bash
# View logs
docker compose logs -f api
docker compose logs -f web

# Restart a single service
docker compose restart api

# Rebuild after code changes
docker compose up -d --build

# Stop everything
docker compose down

# Stop and remove all data (full reset)
docker compose down -v

# Run Alembic migrations manually
docker compose exec api alembic upgrade head

# Open a shell inside the API container
docker compose exec api sh
```

## Database Migrations

Migrations run automatically when the API container starts (via the entrypoint script). To run them manually:

```bash
docker compose exec api alembic upgrade head
```

The `DATABASE_PATH` environment variable controls where the SQLite file is stored. Inside the container this defaults to `/app/data/dashboard.db`, which lives on the `db-data` named volume.

## Production Notes

- The API runs as a non-root user (`editorial`) inside the container.
- The web dashboard is a static build served by nginx; it has no Node.js runtime in production.
- Health checks are configured for both services. Docker will restart unhealthy containers automatically (`restart: unless-stopped`).
- For HTTPS, place a reverse proxy (Caddy, Traefik, or Cloudflare Tunnel) in front of the `web` service.
- The `config/` directory is mounted read-only so LLM config changes require a container restart or a config reload endpoint.
