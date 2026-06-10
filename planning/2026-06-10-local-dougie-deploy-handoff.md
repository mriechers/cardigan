# Handoff: deploy local-dougie so it's testable in the LXC-hosted app

**Date:** 2026-06-10
**For:** a Cardigan-side agent picking up the local-LLM integration
**Predecessor:** `2026-06-05-local-llm-tier-handoff.md` (the original analysis + enable path)
**Companion repo:** `~/Developer/dougie-local-agent` (the service this points at; PR #1)

## TL;DR — is it "all done"? No.

The **code seam** is done (PR #210 on `feat/local-dougie-backend-seam`): a default-off
`local-dougie` backend + `strip_reasoning`/`force_model` flags + 9 tests. But three things
stand between that and **actually picking `local-dougie` from the dropdown in the
LXC-hosted app and running a job**:

1. **The endpoint is wrong for the LXC→Mac network path** (blocker).
2. **It isn't merged/deployed** (the config is baked into the image).
3. **dougie must be reachable off-box** (bind + firewall — cross-repo, dougie side).

The dropdown itself is fine: `api/routers/config.py:81` builds `available_backends` from
**all** `backends` keys, *unfiltered by `enabled`*, so `local-dougie` shows up once the new
image is deployed. (`enabled` is currently just a marker — it is **not** enforced at
routing time in `get_backend_config`/`chat`.)

## What PR #210 already did

- `config/llm-config.json` → new `local-dougie` backend, `type: openai`, `enabled: false`,
  `strip_reasoning: true`, `force_model: true`, `cost_per_project: 0.0`, `timeout: 300`.
- `api/services/llm.py`:
  - `strip_reasoning()` helper (strips Qwen `<think>` blocks + whole-response ```fences```;
    ported from the-lodge `outsource.py`). Applied in `_call_openai` **only** when the
    backend sets `strip_reasoning: true`.
  - `force_model` precedence in `chat()` model resolution, so the backend's own model id
    wins over `phase_models` (a single-model local server can't serve a cloud model id).
- 9 new tests in `tests/api/test_llm.py` (43 pass; ruff clean; no regressions — the
  pre-existing `~31 failed / ~35 error` env-dependent suite failures are unchanged).

## The deployment reality (why "merge to main" matters here)

- Prod (`docker-compose.prod.yml`) runs **pre-built images** `ghcr.io/mriechers/cardigan-{api,worker,web}:latest`, auto-updated by **watchtower**.
- `Dockerfile.api:13` / worker do `COPY config/ config/` — **`llm-config.json` is baked
  into the image**, not volume-mounted. So:
  - The committed config IS the source of truth on the LXC.
  - Runtime Settings-UI edits to `phase_backends` (PATCH `/config/phase-backends` →
    `_save_config` writes the in-container file) **do not survive the next watchtower pull**.
- Deploy path: **merge PR #210 → CI builds + pushes `:latest` → watchtower pulls → restart.**

## The three gaps — concrete tasks

### 1. Fix the endpoint for LXC→Mac (BLOCKER)

`localhost:27180` resolves to the **container**, not the Mac Studio. The MLX/dougie service
runs on the Studio at `:27180`. Two options:

- **(Recommended) Make the endpoint env-overridable** so the committed config stays
  portable and the Mac's LAN address lives in the deployment env. Minimal change in
  `_call_openai` (and ideally a shared helper):
  ```python
  endpoint = os.getenv(config["endpoint_env"], config["endpoint"]) \
      if config.get("endpoint_env") else config["endpoint"]
  ```
  Then in `llm-config.json`: `"endpoint_env": "DOUGIE_ENDPOINT"`, and in
  `docker-compose.prod.yml` api+worker env:
  `- DOUGIE_ENDPOINT=http://<mac-studio-host>:27180/v1/chat/completions`.
  (TDD it — mirror the existing `_call_openai` tests in `test_llm.py`.)
- **(Quicker, less clean)** Hardcode the Mac's LAN address/hostname directly in
  `llm-config.json`'s `local-dougie.endpoint`. Couples the committed config to the homelab
  network; avoid if the repo is shared.

**Networking decision (use the LAN, not the public name).** The homelab uses `*.riechers.co`
internal-domain naming, and Cardigan itself is `cardigan.riechers.co` — but prod runs a
**`cloudflared` tunnel** (see `docker-compose.prod.yml`), so public `*.riechers.co` names go
out through Cloudflare. **dougie is a local model server on Mark's daily-driver Studio and
must NOT be exposed through the tunnel.** The LXC→dougie hop stays on the LAN:

- Use the **Studio's static LAN IP**: `DOUGIE_ENDPOINT=http://<studio-lan-ip>:27180/v1/chat/completions`
  (Mark is confirming the static address). An internal-only split-horizon DNS name is fine
  too, but NOT a Cloudflare-fronted public hostname.
- Confirm the Cardigan LXC can reach the Studio on that subnet (no VLAN/firewall block on
  `:27180`) — quickest check: `curl http://<studio-lan-ip>:27180/health` from inside the LXC.

### 2. dougie must be reachable off-box (cross-repo — `dougie-local-agent`)

- dougie's uvicorn defaults to `127.0.0.1`. For LXC access it must bind **`--host 0.0.0.0`**
  (and the launchd/service def in dougie should set that).
- macOS firewall on the Studio must allow inbound `:27180` from the LXC subnet.
- Health: `GET http://<studio>:27180/health` from *inside the LXC* should return
  `{"ready": false}` (model not loaded) — that's the reachability check before wiring a phase.
- Note the supervisor's OOM guard returns **503** under memory pressure; Cardigan's existing
  tier-escalation should treat that as "escalate to cloud."

### 3. Merge + deploy, then enable for a test

1. Merge PR #210 (+ the env-override change from task 1) → watchtower deploys the new image.
2. Confirm `local-dougie` appears in Settings → phase-backend dropdown.
3. **Shadow eval first** (don't trust-flip): per `2026-06-05` handoff §"Suggested first
   experiment" — run `analyst` on `local-dougie` vs the current `openrouter-cheapskate`
   for ~10 real jobs, diff outputs + completeness scores. The per-phase output records in
   the dashboard make this cheap.
4. Only then set `phase_backends.analyst: "local-dougie"` **in the committed config**
   (durable), or via the UI for an ephemeral test run.

## Key file pointers (so you don't re-derive)

- Dropdown source: `api/routers/config.py:71-86` (`get_phase_backends` → `available_backends`)
- Model resolution + `force_model`: `api/services/llm.py` `chat()` (search `force_model`)
- `_call_openai` (where endpoint is read + `strip_reasoning` applied): `api/services/llm.py`
- Backend def: `config/llm-config.json` → `backends.local-dougie`
- Baked config: `Dockerfile.api:13`; deploy: `docker-compose.prod.yml` (watchtower labels)
- Tests to mirror: `tests/api/test_llm.py` → `TestStripReasoning`, `TestLocalBackendIntegration`

## Pre-existing issue worth a ticket (not caused by this work)

`tests/api` has ~31 failures / ~35 errors on `origin/main` (mostly
`test_jobs_router.py::TestRetryPhaseEndpoint` setup errors — look DB/fixture-dependent).
Verified present before PR #210. Worth a separate issue.
