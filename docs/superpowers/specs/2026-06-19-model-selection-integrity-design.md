# Model Selection Integrity — Design

**Date:** 2026-06-19
**Status:** Approved (design); pending implementation plan
**Author:** Claude Code (brainstormed with Mark)
**Relationship:** Prerequisite for
`2026-06-18-qa-failure-auto-escalation-design.md` (Spec B). Independently
valuable; ship first.

## Problem

The model a user selects for a phase does not reliably run, and the model
the system *reports* having run is not always the model that actually ran.
Three distinct faults, all observed live on cardigan01:8100, plus one minor
reporting bug.

### Fault 1 — Settings never reach the worker

The Settings screen PATCHes `/api/config/models`, which writes
`phase_models` to `config/llm-config.json` (a **relative, container-local**
path) and calls `reload_config()` on the **API process's** LLM client only.
Jobs run in a **separate worker container** with its own `LLMService` that
loads config **once at startup** (`worker.py:166 → get_llm_client`) and
never receives the reload signal. `docker-compose.prod.yml` mounts
`db-data` / `output-data` / `transcript-data` but **not** a shared config
volume.

Evidence (job 11): user selected `claude-opus-4.8-fast` for analyst; the
analyst actually ran **Haiku** — confirmed three ways: the
`analyst_output.md` provenance header, the cost (**$0.0305** for ~21K
tokens, Haiku pricing; Opus would be far higher), and the phase record.

Three compounding sub-faults, each sufficient alone: relative/container-local
config path; reload only in the API process; worker caches at startup.

### Fault 2 — Chunked formatter silently drops the model override

When `routing.chunking.enabled` is true, `_run_phase` routes the formatter
to `_run_formatter_chunked(...)` **without passing `model_override`**. That
function's signature doesn't accept an override, and its per-chunk
`self.llm.chat(...)` call passes **no `model`** — so the formatter ignores
both an explicit per-phase retry override and (in practice) the selected
model, running on the cheap default regardless.

Evidence (job 11): a per-phase retry of the formatter with
`model=anthropic/claude-sonnet-4.6` re-ran (cost ticked up) but produced
`model: chunked (2 chunks)` at **Haiku-class cost** ($0.0548 / 44.6K
tokens), not Sonnet. Verified in code: `_run_formatter_chunked` neither
accepts nor forwards the override.

This is the most consequential fault for downstream work: the formatter is
the most frequently QA-flagged phase, so **no formatter model change —
manual or automated — takes effect while chunking is on.**

### Fault 3 — `phases[].model` mis-records the model that ran

The per-phase `model` field does not always match the model that produced
the output. Job 10's record reported the validator on Haiku while
`validator_output.md`'s provenance header reported Sonnet 4.6 (two validator
passes, one recorded). Any logic that reasons about "what model did this
phase run on" (e.g. Spec B's family escalation) needs this field to be true.

### Fault 4 (minor) — `current_phase` left stale

Job 10's `current_phase` stayed `"seo"` after `timestamp` ran and completed.
Cosmetic, but it misreports job state.

## Goals

1. A phase runs on the model selected for it (via `phase_models` or an
   explicit per-call override), **including the chunked formatter path**.
2. Settings-screen selections take effect on the jobs the worker actually
   runs — not just in the API process.
3. The recorded `phases[].model` reflects the model that actually ran.
4. `current_phase` reflects the true latest phase.

## Non-Goals

- Removing the `cheapskate / default / big-brain` tier labels from
  `phase_backends` system-wide (separate, larger refactor).
- Any QA-escalation / failure-handling behavior — that is Spec B.

## Design

### Fix 1 — Propagate config to the worker

- Persist config to a location **shared by both containers**: mount the
  config path as a shared volume (or place it under an existing shared data
  volume), resolved via an **absolute, env-configurable path** instead of
  the relative `config/llm-config.json`.
- Have the worker **reload config at the start of each job**
  (`self.llm.reload_config()` in `process_job` before running phases). Job
  throughput is low, so per-job reload is simple and always-correct without
  a file watcher.

### Fix 2 — Thread the model through the chunked formatter

- Add a `model_override` parameter to `_run_formatter_chunked` and pass it
  from `_run_phase`.
- In `process_chunk`, pass the resolved model to `self.llm.chat(...)` (either
  the explicit override or, when absent, let `chat()` resolve via
  `phase_models["formatter"]` as the non-chunked path already does).
- Record the actual model in the chunked provenance header instead of the
  opaque `chunked (N chunks)` string (ties into Fix 3).

### Fix 3 — Record the model that actually ran

- When writing each phase's result, set `phases[].model` from the model the
  LLM layer actually used (`LLMResponse.model` / the resolved `model_id`),
  not from a pre-call assumption. Applies to the chunked formatter (record
  the per-chunk model) and to re-validation passes.

### Fix 4 — Advance `current_phase`

- Update `current_phase` as each phase (including the final/optional phases)
  starts, so a finished or paused job shows the true latest phase.

## Testing

**Unit / integration** (LLM stubbed — no spend)
- A `phase_models` selection set via the API is honored by the **worker**
  after a per-job reload (assert the resolved model id, not the tier label).
- The **chunked formatter** honors an explicit `model_override` and, absent
  one, resolves via `phase_models["formatter"]` (assert each chunk's model).
- `phases[].model` equals the model the stubbed LLM reports, across normal,
  chunked, and re-validation paths.
- `current_phase` advances through the final phase.

**Live smoke (manual, low cost)**
- Set a non-default model for one phase via Settings; submit a job; confirm
  the worker ran that model (provenance header + recorded `phases[].model`).

## Notes for the implementer

- The non-chunked `_run_phase` path already resolves models correctly
  (explicit override > `phase_models[phase]` > backend default), confirmed
  live: analyst→Opus and seo→Sonnet per-phase retries both took effect. The
  work is making the **worker config**, the **chunked path**, and the
  **recorded model** match that behavior.
- Per-phase retries currently execute in the **API process**
  (`jobs.py` `run_retry` → `JobWorker()`), which is why overrides work there
  today; the gap is the separate long-running worker container.
