# Changelog

All notable changes to Cardigan. The git tag (`vX.Y.Z`) is the single
source of truth for the version (see `docs/VERSIONING.md`); this file is
the human-readable companion.

## [Unreleased]

### Changed
- **Local LLM backend is now `local-llm` (oMLX), portable across networks.**
  Retired the "dougie" server in favor of oMLX (`studio.riechers.co:8000`). The
  backend re-points at any OpenAI-compatible endpoint via `LOCAL_LLM_ENDPOINT` /
  `LOCAL_LLM_MODEL` / `LOCAL_LLM_API_KEY` (no committed-config edit) — `_resolve_endpoint`
  now accepts a `/v1` base URL and a new `model_env` override supplies the served model.
  Wired into `docker-compose.prod.yml` (api + worker) with a `local_llm_api_key` secret.
  Default-off for routing until the shadow-eval gate passes; see
  `planning/2026-07-02-local-llm-omlx-integration.md`.

### Removed
- Dead `local-ollama` / `remote-ollama` backends (no `ollama` dispatch existed).

## [4.2.0] — 2026-06-19

The **homelab operational milestone** — closes out a ~29-commit window
that had shipped past `v4.1.1` without a release tag while running live on
the homelab LXC.

### Added
- **mmingest caption pipeline** — crawler → indexer → FTS5 search, with
  `/api/mmingest/{search,assets,recent,captions}` endpoints and 3 MCP
  search/asset/recent tools (Sprints 1A–5).
- **Scoped consumer-key auth + audit log** — per-consumer keys with scope
  enforcement (Sprint 3A, migrations 017–018).
- **Staging soak harness + acceptance envelope** for pre-prod validation
  (Sprint 5).
- **Push-based homelab deploy job** replacing the removed Watchtower
  auto-updater (#216).
- **Optional local MLX ("local-dougie") $0 backend tier**, hardened for
  the Mac Studio being busy: defer-and-requeue, busy detection, seam
  hardening (#210).

### Fixed
- **mmingest scheduler was never started** — `start_mmingest_scheduler()`
  is now wired into the API lifespan; the crawler had never run in any
  deployment, so `/api/mmingest/recent` always returned empty (#215,
  closes #202).

### Changed
- `CARDIGAN_VERSION` deployment fallback bumped `v4.1` → `v4.2` in both
  compose files so deployed cost rows stamp the new epoch.
- `web/package.json` aligned to `4.2.0` (CI version-consistency).
- Versioning docs corrected: `app_version` is git-tag-derived (no static
  `database.py` default to hand-edit), and the deploy step no longer
  references Watchtower.
- Stopped tracking stale `__pycache__/*.pyc` files (#207).

### Notes
- `v4.1.x` remains the containerized release line.
- `v5.0.0` is reserved for full remote hosting.

## Earlier releases

Tags `v3.0.0`–`v3.5.0` (Dec 2025–Jan 2026) and `v4.1.0`/`v4.1.1`
(May 2026, the containerized release + cost-data versioning + setuptools_scm)
predate this changelog. See `git tag -l` and the annotated tag messages.
