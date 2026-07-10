# Self-updating local-model backends

> **Design doc — ready to hand to a build session.** Scopes a small, self-contained
> feature out of draft PR #291. *Not* the deterministic-pipeline redesign (that stays a
> separate later epic); this is only the "generic, self-updating local backend" piece.

## Goal (the north star)

Make local LLMs first-class, self-onboarding agent options. The whole design serves one
contract:

> **Pull a model onto local hardware → the server serves it → the next "Refresh models" in
> Cardigan lists it → assign it to a phase in Settings.**
> No Cardigan code. No hand-editing `llm-config.json`. No redeploy. The only thing the user
> touches is a dropdown — which is *data*, not code.

The server (oMLX, vLLM, llama.cpp, LM Studio, Ollama-compat, …) is the **source of truth**
for "what models exist," discovered via the OpenAI-standard `GET /v1/models`.

## Current state (what #291 already gives us, and where it stops)

Grounded in the branch code:

- **The generic client already exists.** `LLMClient._call_openai` (`api/services/llm.py:787`)
  POSTs to any OpenAI-compatible endpoint — keyless-auth aware, `/v1`-base normalization
  (`_resolve_endpoint`, `:313`), `max_tokens` capping for MLX, `strip_reasoning`, and
  `defer_when_unavailable` so a down/busy local box requeues the job (503/timeout) instead of
  failing it. A `local-llm` backend entry already points at the oMLX box
  (`config/llm-config.json:86`).
- **The model dropdown is OpenRouter-only.** Settings' per-phase picker is populated solely by
  `model_roster.get_available_models()` (`api/services/model_roster.py:127`), which fetches
  `openrouter.ai/api/v1/models` (`:49`) and **drops any id that doesn't match a
  `model_families` fnmatch pattern** (`:87`). It never reads the `backends` registry, so local
  models cannot appear there. The UI renders whatever the roster returns (`Settings.tsx`,
  `fetch('/api/config/models')` → `sortedModels.map(...)`) — so fixing the roster fixes the UI
  for free.
- **Routing is split and half-migrated.** `phase_backends` (phase→backend name) and
  `phase_models` (phase→model id) are separate maps (Epic L consolidation, note at
  `llm.py:562`). Local routing lives only in `phase_backends`; the model dropdown writes only
  `phase_models`.
- **The local backend is locked to one model.** `local-llm` uses `force_model: true` +
  `LOCAL_LLM_MODEL`, and `chat()` (`llm.py:615`) makes `force_model` override the per-phase
  model — so today you get exactly *one* local model, not a choice among what the server offers.

## The three friction-removals (acceptance criteria)

These are the only things standing between #291 and the contract. Each is a testable exit
criterion.

| # | Friction today | Fix | Done when |
|---|---|---|---|
| **A** | `_classify_models` **drops** ids that match no `model_families` pattern → a new local model is invisible until someone adds a pattern | Local models **bypass the pattern filter**: list *everything* `/v1/models` returns, tagged `provider` (the serving software, from `owned_by` → e.g. `oMLX`), `backend` (the host → e.g. `studio.riechers.co:8000`), `tier: null`, `pricing_input/output: 0`, plus context length from `max_model_len` | A model present in the server's `/v1/models` but matching no pattern appears in the roster after a refresh, labeled by its real server + host |
| **B** | `MODEL_PRICING` is a per-model cloud table; an unknown id gets a conservative $-estimate that can trip the run cost cap | Already handled — the local backend declares flat `cost_per_project: 0.0` and `_backend_cost` (`llm.py:346`) uses it *instead of* the table. **Keep this invariant; add no per-model pricing rows for local.** | A never-before-seen local model runs at recorded cost `$0.00` with no `MODEL_PRICING` edit |
| **C** | `force_model` locks `local-llm` to a single served model | Route on the **(backend, model) pair**: the backend supplies endpoint+key, the *assignment* supplies which discovered id to send. **Drop `force_model`** on discoverable backends (it's no longer needed once the roster feeds real served ids) | Two different local models can be assigned to two different phases and each receives its own id |

## Design

### 1. Multi-source model roster (`model_roster.py`)

`get_available_models()` gains a second source. After the OpenRouter fetch/fallback, iterate
`config["backends"]` for entries that are `enabled`, `type: "openai"`, and flagged
`discover: true`; for each, `GET {base}/models` (derive the base by stripping
`/chat/completions`; store the `/v1` base as the canonical `endpoint` going forward). Merge the
returned ids into the roster **unfiltered**, each tagged:

```jsonc
{ "id": "Qwen2.5-7B-Instruct-4bit", "name": "Qwen2.5-7B-Instruct-4bit",
  "provider": "oMLX", "backend": "studio.riechers.co:8000", "tier": null,
  "pricing_input": 0, "pricing_output": 0, "context_len": 32768 }
```

Both labels are **derived from discovery, never hand-entered**: `provider` from each model's
`owned_by` field in the `/v1/models` response (oMLX returns `"owned_by": "omlx"` → shown as
`oMLX`; fall back to the host when `owned_by` is generic or absent), and `backend`/identity from
the endpoint host itself. Naming a server is not a step — a new box labels itself.

- Reuse the existing 1h cache + `invalidate_cache()`; `/config/models/refresh` re-queries **all**
  sources (rename its intent from "fetch from OpenRouter" to "refresh all backends").
- **A discovery call that errors is non-fatal** — log it, attach a per-source status
  (`ok`/`unreachable`), and return the cloud models plus whatever local sources answered. A down
  endpoint must never blank the whole dropdown.

### 2. Route on the (backend, model) pair

The roster entry now carries `backend`. Each option is a pair, not a bare id. Minimal,
low-risk consolidation (no schema migration):

- When the Settings dropdown assigns an option to a phase, write **both** `phase_backends[phase]
  = option.backend` and `phase_models[phase] = option.id`, atomically. (Both maps already
  coexist and must agree today — e.g. `analyst` → `openrouter-cheapskate` + `claude-haiku-4.5`
  — so this just makes the UI maintain the pair it already implies.)
- `chat()` already resolves backend via `get_backend_for_phase` and model via
  `phase_models[phase]` — so once both are written and **`force_model` is dropped**, a local
  assignment sends the assigned served id to the local endpoint. No worker changes.
- Full "single map, `phase → {backend, model}`" cleanup (Epic L's endpoint) is a *later*
  optional refactor; call it out but don't block on it.

### 3. Backend-definition CRUD + Settings panel

- **API** (`api/routers/config.py`): add `GET/POST/PATCH /config/backends` to
  create/edit/enable a `type: openai` backend — fields: `endpoint` (base URL), `enabled`,
  `discover`, optional key reference. **The backend is keyed by its host** (e.g.
  `studio.riechers.co:8000`) so identity is self-describing and multiple servers never collide —
  no invented names like `local-llm-2`. (The existing `local-llm` entry can be renamed to its
  host, or kept as a legacy alias.) Persist via the existing `_save_config`; the generic client
  already consumes whatever is written — **no client changes**.
- **Settings UI** (`web/src/pages/Settings.tsx`): an "Add local endpoint" form — **base URL** +
  optional **API key** + a **Test / Discover** button (one call to `{base}/models` that both
  validates the connection and previews the models). On save, the endpoint's models flow into
  the *same* per-phase dropdown, grouped with `<optgroup>` by source — **Cloud** (OpenRouter)
  vs each server's **`provider` · `host`** (e.g. `oMLX · studio.riechers.co:8000`). Picking a
  local option is what writes the (backend, model) pair from §2.

## Non-goals / explicitly out of scope

- The deterministic-shell pipeline redesign — separate epic; this feature must not depend on it.
- Any **per-model** code or config: no curated local allowlist, no hard-coded tiers, no pricing
  rows. If onboarding a model requires editing a file, the design has failed criterion A/B/C.
- Auto-judging local model quality. Tier/fit for a phase is a **manual** call (or a later
  model-fit eval) — that's the step the user *wants* to own.

## Edge cases & caveats

- **Endpoint down at roster-build time** → per-source `unreachable` status, cloud models still
  render, local ones show as "endpoint unreachable — retry" rather than silently vanishing.
- **Listed ≠ loadable** — `/v1/models` advertises availability; a too-big pick still returns
  `HTTP 507` at inference (memory ceiling). The client's 503/deferral path covers busy/loading;
  surface 507 as a clear "won't fit" signal, not a job failure.
- **Secrets** — the key resolves via `api_key_env` → `get_secret` (Docker secret → env →
  Keychain). Do **not** write a raw key into `llm-config.json`. For a LAN box the key is
  low-sensitivity, but keep the definition (endpoint/model/enabled) in config and the key in the
  secret store. Keyless servers: the client already omits the auth header when there's no key.
- **Server without `/v1/models`** (rare for OpenAI-compat) → fall back to a free-text model id
  field; discovery is a convenience, not a hard requirement.
- **Trust boundary** — an "arbitrary endpoint" field means the app POSTs transcript content to a
  user-supplied URL. For single-team, tailnet-only Cardigan this is a non-issue; note it so it
  isn't a surprise if the app ever goes multi-tenant.

## Build sequence (small, reviewable PRs)

1. **Route-on-pair + drop `force_model`.** Make the local backend send an assigned model; UI
   writes both maps. *Verify:* assign a known local model to one phase, run a job, confirm the
   local endpoint received that id (Langfuse trace / event log) at `$0`.
2. **Roster multi-source.** Local models appear in `/config/models`, unfiltered, tagged, $0.
   *Verify:* a model in `/v1/models` matching no family pattern shows up after
   `/config/models/refresh`.
3. **Backend CRUD API.** `GET/POST/PATCH /config/backends`. *Verify:* create a backend over the
   API, confirm it persists and its models discover.
4. **Settings panel.** Add-endpoint form + Test/Discover + grouped dropdown. *Verify:* the full
   loop — save endpoint → refresh → local model appears alongside cloud → assign → job runs
   local — with no code or config-file edits.

## Relationship to #291

De-scope PR #291 to **this** feature (generic local backend + self-updating roster + Settings
config). It's ~80% landed; criteria A/B/C + the CRUD/panel finish it. Detach the
deterministic-pipeline docs (`planning/expanded-prompt-*`, `planning/hybrid-pipeline-eval/`)
into their own later epic so this ships on its own.
