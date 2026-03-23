# Docker CI/CD + Branch Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a main branch (Docker production) and dev branch (local development) workflow with automated GHCR image publishing and Watchtower-based deployment on PR merge to main.

**Architecture:** GitHub Container Registry (GHCR) stores Docker images tagged by commit SHA and `latest`. A deploy workflow builds and pushes all three images (api, web, worker) on merge to main. The production host runs Watchtower, which polls GHCR and auto-restarts containers when new images appear. The dev branch is for local iteration with hot-reload scripts.

**Tech Stack:** GitHub Actions, GitHub Container Registry (ghcr.io), Docker Compose, Watchtower, GitHub branch protection API

---

## File Structure

| Action | File | Purpose |
|--------|------|---------|
| Modify | `.github/workflows/deploy.yml` | Replace placeholder with GHCR build+push |
| Modify | `.github/workflows/ci.yml` | Add Docker build smoke test |
| Create | `docker-compose.prod.yml` | Production compose file pulling from GHCR |
| Modify | `docker-compose.yml` | Keep as local-build dev/staging compose |
| Modify | `.gitignore` | Stop tracking `.pyc`, `.db`, `.DS_Store` |

---

## Task 1: Commit Current Working State to Main

**Context:** There are ~63 files changed locally (Docker configs, middleware, tests, refactors) plus ~21 untracked files. All of this represents the working Docker-hosted version that should become the canonical main branch state.

**Files:**
- All modified and untracked files in the working directory

- [ ] **Step 1: Remove tracked files that should be gitignored**

`.pyc` files, `.DS_Store`, and `dashboard.db` are tracked but gitignored. Remove them from tracking:

```bash
git rm --cached -r '**/__pycache__/' '**/*.pyc' .DS_Store web/.DS_Store dashboard.db 2>/dev/null
```

- [ ] **Step 2: Stage all changes**

```bash
git add -A
```

Review staged changes with `git diff --cached --stat` to confirm nothing sensitive (`.env`, credentials) is included.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: Docker containerization with multi-service architecture

Add Docker deployment (API, Worker, Web, MCP, Tunnel services),
auth middleware, rate limiting, chunked processing, Langfuse
observability, and CI pipeline.

[Agent: Main Assistant]"
```

- [ ] **Step 4: Push to main**

```bash
git push origin main
```

---

## Task 2: Build the Deploy Workflow (GHCR)

**Files:**
- Modify: `.github/workflows/deploy.yml`

- [ ] **Step 1: Write the deploy workflow**

Replace the placeholder `deploy.yml` with a workflow that:
- Triggers on push to `main`
- Logs into GHCR using `GITHUB_TOKEN` (automatic, no secrets to configure)
- Builds all three Dockerfiles (api, web, worker)
- Tags each image with both the commit SHA and `latest`
- Pushes to `ghcr.io/mriechers/cardigan-api`, `ghcr.io/mriechers/cardigan-web`, `ghcr.io/mriechers/cardigan-worker`

```yaml
name: Deploy

on:
  push:
    branches: [main]

concurrency:
  group: deploy-${{ github.ref }}
  cancel-in-progress: true

env:
  REGISTRY: ghcr.io
  IMAGE_PREFIX: ghcr.io/mriechers/cardigan

jobs:
  build-and-push:
    name: Build & Push Images
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - uses: actions/checkout@v4

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build & push API image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: Dockerfile.api
          push: true
          tags: |
            ${{ env.IMAGE_PREFIX }}-api:latest
            ${{ env.IMAGE_PREFIX }}-api:${{ github.sha }}
          cache-from: type=gha,scope=deploy-api
          cache-to: type=gha,mode=max,scope=deploy-api

      - name: Build & push Worker image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: Dockerfile.worker
          push: true
          tags: |
            ${{ env.IMAGE_PREFIX }}-worker:latest
            ${{ env.IMAGE_PREFIX }}-worker:${{ github.sha }}
          cache-from: type=gha,scope=deploy-worker
          cache-to: type=gha,mode=max,scope=deploy-worker

      - name: Build & push Web image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: Dockerfile.web
          push: true
          tags: |
            ${{ env.IMAGE_PREFIX }}-web:latest
            ${{ env.IMAGE_PREFIX }}-web:${{ github.sha }}
          cache-from: type=gha,scope=deploy-web
          cache-to: type=gha,mode=max,scope=deploy-web
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: add GHCR image build and push on merge to main

[Agent: Main Assistant]"
```

---

## Task 3: Create Production Compose File

**Context:** The current `docker-compose.yml` builds from local Dockerfiles — good for dev/staging. Production needs a separate compose file that pulls pre-built images from GHCR. This is what runs on the deployment host (local Mac now, VM later, VPS eventually).

**Files:**
- Create: `docker-compose.prod.yml`

- [ ] **Step 1: Write the production compose file**

This mirrors `docker-compose.yml` but replaces `build:` directives with `image:` directives pointing to GHCR. It also adds a Watchtower service for automatic updates.

```yaml
# docker-compose.prod.yml
# Production deployment — pulls pre-built images from GHCR.
# Usage: docker compose -f docker-compose.prod.yml up -d
#
# Requires:
#   - .env file with API keys and CARDIGAN_API_KEY
#   - GHCR_TOKEN env var or docker login to ghcr.io

services:
  api:
    image: ghcr.io/mriechers/cardigan-api:latest
    ports:
      - "8000:8000"
    env_file: .env
    environment:
      - DATABASE_PATH=/data/db/dashboard.db
      - OUTPUT_DIR=/data/output
      - CORS_ORIGINS=${CORS_ORIGINS:-http://localhost:3000}
      - CARDIGAN_API_KEY=${CARDIGAN_API_KEY}
      - TRANSCRIPTS_DIR=/data/transcripts
    volumes:
      - db-data:/data/db
      - output-data:/data/output
      - transcript-data:/data/transcripts
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/system/health')"]
      interval: 30s
      timeout: 5s
      start_period: 10s
      retries: 3
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
    restart: unless-stopped

  worker:
    image: ghcr.io/mriechers/cardigan-worker:latest
    env_file: .env
    environment:
      - DATABASE_PATH=/data/db/dashboard.db
      - OUTPUT_DIR=/data/output
      - CARDIGAN_API_KEY=${CARDIGAN_API_KEY}
      - TRANSCRIPTS_DIR=/data/transcripts
    volumes:
      - db-data:/data/db
      - output-data:/data/output
      - transcript-data:/data/transcripts
    depends_on:
      api:
        condition: service_healthy
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
    restart: unless-stopped

  web:
    image: ghcr.io/mriechers/cardigan-web:latest
    ports:
      - "3000:3000"
    environment:
      - CARDIGAN_API_KEY=${CARDIGAN_API_KEY}
    depends_on:
      - api
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
    restart: unless-stopped

  mcp:
    image: ghcr.io/mriechers/cardigan-api:latest
    command: ["python", "-m", "mcp_server.server"]
    ports:
      - "8080:8080"
    env_file: .env
    environment:
      - MCP_TRANSPORT=sse
      - EDITORIAL_API_URL=http://api:8000
      - DATABASE_PATH=/data/db/dashboard.db
      - OUTPUT_DIR=/data/output
      - TRANSCRIPTS_DIR=/data/transcripts
    volumes:
      - db-data:/data/db
      - output-data:/data/output
      - transcript-data:/data/transcripts
    depends_on:
      api:
        condition: service_healthy
    profiles:
      - mcp
    restart: unless-stopped

  tunnel:
    image: cloudflare/cloudflared:latest
    command: tunnel --no-autoupdate run
    environment:
      - TUNNEL_TOKEN=${CLOUDFLARE_TUNNEL_TOKEN}
    depends_on:
      api:
        condition: service_healthy
      web:
        condition: service_started
    profiles:
      - tunnel
    restart: unless-stopped

  watchtower:
    image: containrrr/watchtower
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      # Set DOCKER_CONFIG in .env if Docker runs as non-root user
      # (e.g., DOCKER_CONFIG=/home/ubuntu/.docker)
      - ${DOCKER_CONFIG:-/root/.docker}/config.json:/config.json:ro
    environment:
      - WATCHTOWER_CLEANUP=true
      - WATCHTOWER_POLL_INTERVAL=300
      # Only update containers with the watchtower.enable=true label
      - WATCHTOWER_LABEL_ENABLE=true
    restart: unless-stopped

volumes:
  db-data:
  output-data:
  transcript-data:
```

**Key differences from dev compose:**
- `image:` instead of `build:` — pulls from GHCR
- `restart: unless-stopped` on all services
- Watchtower service polls GHCR every 5 minutes for new `latest` tags
- `CORS_ORIGINS` uses env var with fallback
- No local build context needed

- [ ] **Step 2: Commit**

```bash
git add docker-compose.prod.yml
git commit -m "feat: add production compose file with GHCR images and Watchtower

[Agent: Main Assistant]"
```

---

## Task 4: Add Docker Build Smoke Test to CI

**Context:** The existing CI runs on PRs to main and pushes to dev. Adding a Docker build step ensures images build successfully before merge, catching Dockerfile issues early.

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add docker-build job to CI**

Append this job after the existing jobs in `ci.yml`:

```yaml
  docker-build:
    name: Docker Build Smoke Test
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
      - name: Build API image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: Dockerfile.api
          push: false
          cache-from: type=gha,scope=api
          cache-to: type=gha,mode=max,scope=api
      - name: Build Worker image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: Dockerfile.worker
          push: false
          cache-from: type=gha,scope=worker
          cache-to: type=gha,mode=max,scope=worker
      - name: Build Web image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: Dockerfile.web
          push: false
          cache-from: type=gha,scope=web
          cache-to: type=gha,mode=max,scope=web
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add Docker build smoke test to PR checks

[Agent: Main Assistant]"
```

---

## Task 5: Push Main and Set Up Branch Protection

- [ ] **Step 1: Push all commits to main**

```bash
git push origin main
```

- [ ] **Step 2: Enable branch protection on main**

```bash
gh api repos/mriechers/cardigan/branches/main/protection \
  --method PUT \
  --input - <<'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["Python Lint", "Frontend Lint", "Python Tests", "Frontend Build", "DB Migrations", "Docker Build Smoke Test"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0
  },
  "restrictions": null
}
EOF
```

**Note:** `enforce_admins: false` lets you (the repo owner) bypass protection for emergency hotfixes. `required_approving_review_count: 0` means PRs are required but self-merge is fine — the CI checks are the real gate.

- [ ] **Step 3: Verify protection**

```bash
gh api repos/mriechers/cardigan/branches/main/protection --jq '.required_status_checks.contexts[]'
```

Expected: lists all 6 required checks.

---

## Task 6: Create Dev Branch

- [ ] **Step 1: Create and push dev branch from main**

```bash
git checkout -b dev
git push -u origin dev
```

- [ ] **Step 2: Verify branch setup**

```bash
git branch -vv
```

Expected: `dev` tracking `origin/dev`, `main` tracking `origin/main`.

---

## Task 7: Set Up Watchtower Auth for GHCR

**Context:** Watchtower needs to authenticate with GHCR to pull private images. This is done via Docker login on the production host. This is a manual step — documenting it here for when you set up the VM/VPS.

- [ ] **Step 1: Create a GitHub Personal Access Token (PAT)**

Go to: GitHub → Settings → Developer Settings → Personal Access Tokens → **Tokens (classic)**

Create a **classic** token (fine-grained tokens do not support `packages` scope):
- Scopes: `read:packages` only
- Name: `cardigan-watchtower`

- [ ] **Step 2: Docker login on the production host**

On whichever machine runs the production containers:

```bash
echo "<PAT_TOKEN>" | docker login ghcr.io -u mriechers --password-stdin
```

This creates `~/.docker/config.json` which Watchtower reads (mounted as a volume in docker-compose.prod.yml).

- [ ] **Step 3: Start production stack**

```bash
docker compose -f docker-compose.prod.yml up -d
```

Or with optional services:

```bash
docker compose -f docker-compose.prod.yml --profile tunnel --profile mcp up -d
```

---

## Summary: End-to-End Flow

```
Developer workflow:
  1. Work on `dev` branch locally (scripts/start.sh, hot reload)
  2. Push to `dev` → CI runs (lint, test, build, Docker smoke test)
  3. Create PR: dev → main → CI runs again on the PR
  4. Merge PR → deploy.yml triggers:
     - Builds 3 Docker images
     - Pushes to ghcr.io/mriechers/cardigan-{api,web,worker}:latest
  5. Watchtower (on production host) detects new :latest tags
     - Pulls new images
     - Recreates containers with zero manual intervention
```

**Future migration path:**
- Local Mac → VM: copy `.env` + `docker-compose.prod.yml`, `docker login ghcr.io`, `docker compose up -d`
- VM → VPS: same process, just a different host
- Watchtower handles updates regardless of where it runs
