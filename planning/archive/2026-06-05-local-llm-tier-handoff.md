# Handoff: Local LLM as a Cardigan backend tier

**Date:** 2026-06-05
**Origin:** the-lodge session (local MLX server setup + `/outsource` skill build)
**Status:** Analysis + integration sketch — no Cardigan code touched
**Note:** Written while cardigan-v4 was checked out on `pr-187`; this file is intentionally
untracked. Commit it from a Cardigan session once the branch situation is clear.

---

## Update 2026-06-10 — seam landed; endpoint is now `dougie`, not raw MLX

The local-agent service **`dougie`** now exists (`~/Developer/dougie-local-agent`): a
FastAPI proxy at `:27180` that wraps the same `mlx_lm.server` behind a model-lifecycle
supervisor (lazy-load, **idle-unload**, OOM guard). **Cardigan should point at dougie
(`:27180`), not the raw MLX server (`:27190`)** — otherwise the model never idle-unloads
and we reintroduce the 19 GB OOM freeze dougie exists to fix. dougie is OpenAI-compatible,
so it's a drop-in `"type": "openai"` backend.

**What this PR added (default-off seam — no phase routing changed):**
- `backends.local-dougie` in `config/llm-config.json` (`enabled: false`, endpoint
  `:27180`, model = Qwen id, `cost_per_project: 0.0`, `timeout: 300`).
- Two opt-in backend flags, so existing backends are untouched:
  - `strip_reasoning: true` → `_call_openai` runs `strip_reasoning()` (the `<think>` /
    fence stripping from caveats #4/#5, ported from `outsource.py`).
  - `force_model: true` → the backend's own model id wins over `phase_models`, so a
    local backend isn't sent a cloud model id the MLX server can't serve.

**To actually enable `analyst` on local (do the shadow eval FIRST — see below):**
1. `backends.local-dougie.enabled: true`
2. `phase_backends.analyst: "local-dougie"`
3. Ensure dougie is running on the Studio (`:27180`); cloud tiers remain the escalation
   fallback via existing retry machinery.

**Do not flip routing before the shadow test.** Both this handoff (§"Suggested first
experiment") and the local-agent plan require measuring Qwen-vs-cloud on real jobs first —
the eval is the deliverable, not the wiring.

## What now exists on the Mac Studio

- `mlx_lm.server` serving **Qwen3.6-35B-A3B-4bit** (MoE, 3B active) on
  `http://localhost:27190/v1` — OpenAI-compatible chat completions
- Measured: **~109 tok/s generation**, ~1s/item warm on short structured tasks,
  ~19 GB resident, cold load 30–60s (lazy, on first request)
- Managed by the `llm` shell dispatcher (the-lodge `config/shell/aliases.zsh`):
  `llm serve` / `llm status` / `llm stop`. Env: `LODGE_LLM_MODEL`, `LODGE_LLM_PORT`
- Also available: `Qwen3-Coder-30B-A3B` (one download away, same server)

## The token-economics argument (why Cardigan is the right home)

Three ways to wire a local model into agentic work; the cost structure differs by
**where coordination state lives**:

| Architecture | Coordination lives in | Claude/cloud token cost |
|---|---|---|
| Skill dispatch (the-lodge `/outsource`) | Claude's context | Fixed ~5k/batch overhead; content stays out of context |
| Claude teammate wrapping the local model | A second Claude context | *Worse* — wrapper rent + message traffic both directions |
| **Pipeline stage → local endpoint (Cardigan)** | **Code (the pipeline graph)** | **~Zero marginal** — no model carries the workflow |

Cardigan's four-phase pipeline + `llm-config.json` backend routing is architecture 3
already built. Assigning a phase to the local endpoint makes that phase's tokens free
with **no added coordination cost** — the win the other two architectures can't reach.

## Current config state (read 2026-06-05)

`config/llm-config.json` already has the seams:

- Two **disabled** `local-ollama` backends pointing at `qwen2.5:14b` on `:11434` —
  legacy drift from an earlier experiment; the new server is MLX + OpenAI-protocol,
  not Ollama (different API, don't re-enable those)
- `"type": "openai"` backends take arbitrary endpoints → the integration path
- `cost_per_project` field per backend; tier system (`model_families`, tier 0/1/2)
- **Per-phase retry with tier escalation** — this is the cascade pattern for free:
  local fails/truncates → escalate to a paid tier automatically
- `auto_select` health checking (5s interval) — handles "Mac asleep / server down"
  fallback if the local backend participates in selection

## Proposed integration

### 1. New backend

```json
"local-mlx": {
  "type": "openai",
  "endpoint": "http://localhost:27190/v1/chat/completions",
  "model": "mlx-community/Qwen3.6-35B-A3B-4bit",
  "timeout": 300,
  "cost_per_project": 0.0,
  "enabled": false
}
```

Verify: does the `openai`-type client *require* `api_key_env`? mlx_lm.server ignores
auth — a dummy key env is fine if the client insists.

### 2. Phase assignments, by dispatch-contract fit

A phase fits the local model when its input is **self-contained** (full transcript/chunk
in the prompt, no tool use) and its output is **cheap to verify** (schema-checkable).

| Phase | Current | Local fit | Rationale |
|---|---|---|---|
| `analyst` | haiku (cheapskate) | **Strong — start here** | Theme extraction from a provided transcript; self-contained, schema-verifiable |
| `seo` | haiku | **Strong** | Keyword analysis from provided text |
| `formatter` | sonnet | **Good** | Mechanical restructuring; verify with completeness checker |
| `validator` | haiku | Moderate | It IS the quality gate — if local grades local, errors correlate. Keep cloud until trust is built, or cross-check |
| `timestamp` | sonnet | Moderate | Caption-anchored; test accuracy before switching |
| `copy_editor` | opus (big-brain) | **No** | The Cardigan voice — judgment + taste stays cloud |

### 3. Escalation = existing retry machinery

Set local as the phase's first backend; tier-escalation on failure/truncation already
provides the safety net. The completeness checker (`coverage_threshold: 0.70`) is the
right tripwire.

## Operational caveats (from the-lodge session evidence)

1. **Serialized inference.** One server, one lane. `max_concurrent_jobs: 3` +
   `batch_size: 4` will queue, not parallelize. Wall-clock per job rises even though
   cost falls. Fine for an overnight/queue-driven pipeline; check WebSocket-visible
   latency expectations.
2. **Server lifecycle.** 19 GB resident. The worker should probably health-check and
   fall back (auto_select) rather than assume the server is up. Don't auto-start from
   Cardigan — that's a human/launchd decision on the Studio.
3. **Known failure mode — recency bias on judgment labels.** In skill evals, Qwen
   tagged sentiment by the *ending* of a text rather than dominant tone. For Cardigan:
   harmless for keyword/theme extraction, relevant for anything tone-flavored.
   Mitigation: explicit decision criteria in the phase prompt.
4. **Thinking tokens.** Qwen3.6 emits `<think>` blocks by default. Disable via
   `chat_template_kwargs: {"enable_thinking": false}` in the request body, and/or
   strip `<think>.*?</think>` from responses (the-lodge `outsource.py` does both —
   steal that code).
5. **Markdown fences.** Asks for JSON come back fenced (```json). Strip before parse
   (also handled in `outsource.py`).
6. **Timeouts.** 180s default may be tight for long-transcript phases at 109 tok/s
   with large prompts; 300s suggested for the local backend. Chunking
   (`threshold_words: 3000`) already bounds the worst case.
7. **Safety config.** `max_cost_per_1k_tokens` and allowlist logic should treat the
   local backend as cost-0 without tripping validation.

## Suggested first experiment

Shadow mode: run `analyst` on **both** local-mlx and the current haiku backend for
~10 real jobs; diff outputs + completeness scores before flipping any phase for real.
The per-phase output records in the dashboard should make this comparison cheap.

## Mark's supplementary-agent ideas (2026-06-05)

Beyond the backend-tier integration, two adjacent roles for the Mac Studio:

1. **Caption pre-processing on arrival** — when a new caption file lands (job listener
   extension), the local model generates the initial analyst-style report as a
   jumping-off point before a full pipeline run is even queued. Event-driven version
   of the shadow test above; same dispatch shape (self-contained caption in, structured
   report out). Strong fit.
2. **whisperx for hardware-intensive transcription** — not an LLM task (GPU
   transcription/alignment), but it shares the idle-window scheduling scaffolding
   being built in the-lodge (#207 night-shift queue): same queue and lifecycle
   discipline, different worker process. Worth co-scheduling so transcription and
   LLM passes don't fight for the same unified memory at the same time —
   **do not run whisperx and the 19 GB Qwen server concurrently on the 36 GB box.**

## Cross-references

- the-lodge `/outsource` skill: `.claude/skills/misc/outsource/` (dispatch contract,
  fence/think stripping, batch client)
- the-lodge session eval data: `.claude/skills/misc/outsource-workspace/iteration-1/`
  (benchmark.json shows the honest small-N economics)
