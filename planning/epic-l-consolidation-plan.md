# Epic L — Preset-tier removal: consolidation plan

**Status:** Increment 1 done (this PR). Increment 2 (the heavy part) is scoped
here for a **dedicated session** — it needs a cost rewire, a config migration,
and the deferred single-model-dropdown frontend.

## What Epic L actually is (investigation, 2026-06-20)

The "cheapskate / default / big-brain" preset tiers are **not** a load-bearing
runtime abstraction. The runtime already does direct per-phase model selection:

- **Model resolution** (`api/services/llm.py`, `generate()`): precedence is
  `force_model` (e.g. local-dougie) → explicit `model` override → **`phase_models[phase]`**
  → backend `model`/`fallback_model`. So `config/llm-config.json`'s `phase_models`
  (phase → concrete model id) already drives the model.
- **`phase_backends`** (phase → tier-named backend) now only supplies the
  *transport* (endpoint/type/api_key) and a flat `cost_per_project` estimate.
- **Auto-escalation is gone.** `get_next_tier` / `get_escalation_config` had zero
  callers (Sprint 3 replaced auto-escalation with user-driven retry +
  `model_override`). Removed in Increment 1.

## Increment 1 (this PR) — safe, done

- Removed dead `get_next_tier` + `get_escalation_config` (the tier ladder).
- Closed **#103** (stale `tier`/`tier_label`/`tier_reason` in `retry_single_phase`
  — already cleaned by Sprint 3; verified no code refs remain).

## Increment 2 (dedicated session) — the consolidation

**Goal:** make `phase_models` the single source of truth; delete `phase_backends`
and the tier-named backends; derive cost from the model, not the tier.

### Steps (in order)
1. **Cost rewire (the risk).** Today `cost_per_project` is per-backend
   (cheapskate 0.0 / openrouter 0.02 / big-brain 0.1). With direct model
   selection, cost must come from the model. Decide: per-model `cost_per_1k`
   (config has `safety.max_cost_per_1k_tokens`) computed from actual token
   usage, or a per-model `cost_per_project` table. Verify the `run_cost_cap`
   (`safety.run_cost_cap`) and `_backend_cost` path still hold. **This is the
   step that can mis-bill jobs — do it first, with tests, against real
   `session_stats` rows.**
2. **Collapse backends.** Point every phase at one transport backend
   (`openrouter` / `openrouter-direct`); keep `local-dougie` (force_model) and
   the disabled ollama entries. Delete `openrouter-cheapskate` /
   `openrouter-big-brain` once nothing references them.
3. **Config migration + back-compat shim.** A migration that maps any existing
   `phase_backends` value → the equivalent `phase_models` entry on load, so
   deployed `llm-config.json` files (incl. the homelab's) don't break. Keep the
   shim for one release, then remove.
4. **API contract.** Remove `PhaseBackendsUpdate` / `get_phase_backends` /
   `update_phase_backends` and the `0≤tier≤2` validation in
   `api/routers/config.py` once the frontend no longer calls them. The
   `phase_models` endpoints already exist and are the replacement.
5. **#92 roster narrowing** (pairs with the frontend). The `model_families`
   glob patterns pull ~25 models (old Gemini, image models, lite variants) into
   the Settings dropdown. Tighten to version-specific globs and/or add an
   `exclude` list. Validate against a live OpenRouter roster call. Belongs with
   the dropdown frontend because it shapes that dropdown's contents.
6. **Frontend (#69)** — single model dropdown replacing the tier dropdowns
   (Settings, JobDetail retry, TranscriptUploader). Deferred to the frontend
   session; the dropdowns already read `routing.tier_labels` dynamically, so the
   swap is contained. Needs browser verification.
7. **Remove per-model `tier` / `min_tier`.** Once nothing reads them
   (`available_models[].tier`, `model_families[].tier`, `routing.chunking.min_tier`
   — check the chunking path first), drop them from config.

### Test anchors
- `tests/api/test_llm.py`, `tests/api/test_worker.py` (model resolution).
- New: cost-from-model test against known token counts.
- Config round-trip: a legacy `phase_backends`-only config still resolves models
  via the shim.

### Sequencing
Cost rewire (1) → backend collapse (2) → migration/shim (3) → API + frontend
(4,6) → roster (5) → tier-field removal (7). Don't skip the shim — the homelab
runs a hand-trimmed `llm-config.json` (see memory: cardigan-lxc-deployment).
