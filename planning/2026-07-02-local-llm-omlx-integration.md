# Local LLM as a Cardigan backend — oMLX integration (supersedes dougie)

**Date:** 2026-07-02
**Supersedes:** `planning/archive/2026-06-0{5,10,12}-*dougie*.md`
**Status:** Code + config + deploy wiring landed; **operational enable (model pick,
reachability, shadow-eval, analyst flip) still pending** — needs the live Studio + LXC.

## Decision

Mark standardized on **oMLX** (`github.com/jundot/omlx`, OpenAI-compatible server at
`http://studio.riechers.co:8000/v1`) as the single local-model-serving stack on the Mac
Studio. The earlier **dougie** supervisor (`:27180`) is **retired** — all references
scrubbed from code, config, tests, and scripts.

The backend is now a **network-neutral `local-llm`** so Cardigan can be re-pointed at any
OpenAI-compatible endpoint (a future WPM/Vilas-Hall server, with the app container on a
different box than the model) by changing **env vars only** — no committed-config edit.

## What landed in this change

### `config/llm-config.json`
- New `backends.local-llm` (`type:"openai"`, `enabled:true`):
  - `endpoint` = `http://studio.riechers.co:8000/v1/chat/completions`,
    `endpoint_env` = `LOCAL_LLM_ENDPOINT`
  - `model` = a placeholder sentinel; `model_env` = `LOCAL_LLM_MODEL` (deploy supplies it)
  - `api_key_env` = `LOCAL_LLM_API_KEY` (oMLX enforces a Bearer key)
  - `strip_reasoning`, `force_model`, `defer_when_unavailable`, `max_tokens:8192`,
    `cost_per_project:0.0`, `timeout:300`
- Removed the dead `local-ollama` / `remote-ollama` backends (no `ollama` dispatch branch).
- `phase_backends` unchanged (all cloud) — the `analyst` flip happens **after** the eval.

### `api/services/llm.py`
- `_resolve_endpoint()` now also **normalizes a `/v1` base URL** to
  `/v1/chat/completions`, so the `/local-llm` skill's `LOCAL_LLM_ENDPOINT` base form works
  verbatim.
- New `_resolve_model()` — `model_env` override for the served model id (mirrors
  `_resolve_endpoint`); the `force_model` branch uses it.
- dougie-specific comments genericized. All behavior (`_parse_unavailable_503`, deferral,
  `strip_reasoning`, keyless-tolerant auth) is unchanged and now serves oMLX.

### Secrets + deploy
- `api/services/secrets.py`: `LOCAL_LLM_API_KEY` added to `bootstrap_secrets()`.
- `docker-compose.prod.yml`: api + worker get `LOCAL_LLM_ENDPOINT` / `LOCAL_LLM_MODEL`
  env + the `local_llm_api_key` Docker secret (registered under top-level `secrets:`).
- `secrets/local_llm_api_key.example`, `secrets/README.md`, `.env.example` documented.
  The real key lives in **1Password → "oMLX Local LLM Key"** — never committed.

### Tests
- `tests/api/test_llm.py`, `tests/api/test_worker.py`: renamed to `local-llm` /
  `LOCAL_LLM_ENDPOINT`; new cases for `model_env` override, `/v1` endpoint normalization,
  and Bearer-present auth (oMLX is keyed, unlike dougie). Autouse fixture clears ambient
  `LOCAL_LLM_*` so the suite is deterministic on a dev shell that exports them.

## Remaining operational steps (need the live homelab)

1. **Pick + load the oMLX model** via `/model-fit` — the strongest analyst-capable model
   that fits oMLX's ~13 GB ceiling (a too-big model returns HTTP 507). Put its id in
   `LOCAL_LLM_MODEL` (host `.env`) and, optionally, the config `model` default.
2. **Verify LXC→Studio reachability** from inside the `cardigan01` container:
   `curl -H "Authorization: Bearer $KEY" http://studio.riechers.co:8000/v1/models`.
   DNS failure ⇒ the Docker/Tailscale MagicDNS hijack; fix via the
   `/etc/docker/daemon.json` DNS pin to OPNsense (192.168.1.1). Confirm the Studio's
   macOS firewall allows `:8000` from the LXC subnet. (See homelab
   `2026-06-30-mac-studio-omlx-llm-access-handoff.md`.)
3. **Shadow-eval** (do NOT trust-flip):
   `python scripts/shadow_eval_analyst.py --backends openrouter-cheapskate,local-llm`
   over ~5–10 real transcripts; compare quality / completeness / latency / `$0` cost.
4. **Flip `analyst` → `local-llm`** only if the eval holds — durably via
   `PATCH /config/phase-backends` on cardigan01 (persists on the config volume) or in
   committed config.

## Deploy note (config-volume reconciliation)

Prod reads config from the `config-data` volume (`LLM_CONFIG_PATH=/data/config/...`), which
`resolve_config_path()` only seeds when **absent**. So a committed-config change (the new
`local-llm` backend definition) does **not** auto-propagate to an existing volume: after
watchtower pulls the new image, overwrite `/data/config/llm-config.json` on the volume with
the new file (or delete it to reseed). Env + secret changes need a `docker compose up -d`
recreate.

## Risks

- **oMLX ceiling vs. analyst quality (primary).** dougie ran a 35B MoE; oMLX caps ~13 GB,
  so the analyst model is smaller and possibly weaker. The shadow eval is the go/no-go — if
  nothing that fits is good enough, keep `analyst` on cloud; `local-llm` stays
  available-but-unassigned (wiring/portability goal still met).
- **HTTP 507 (model-too-big) is not a deferral signal.** Avoided by pinning a fitting
  model. Optional future hardening: treat 507 as unavailable-non-retryable (pause).
- **Local-dev key.** `secrets.get_secret` reads keychain, not 1Password; for local runs
  `export LOCAL_LLM_API_KEY="$(...op...)"` before hitting the backend.
