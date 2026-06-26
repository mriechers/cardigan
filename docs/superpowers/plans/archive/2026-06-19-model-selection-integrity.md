# Model Selection Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the model selected for each pipeline phase actually run on the worker and be recorded honestly — closing the gap where Settings selections are ignored and the chunked formatter silently drops model overrides.

**Architecture:** Cardigan runs as separate `api` and `worker` containers, each with its own in-memory `LLMService` loaded from a relative, container-local `config/llm-config.json`. This plan (1) moves config to a shared, env-resolved path and has the worker reload it per job, (2) threads `model_override` through the chunked-formatter path, and (3) records the model that actually ran (chunked path + post-retry re-validation). It implements Spec A (`docs/superpowers/specs/2026-06-19-model-selection-integrity-design.md`).

**Tech Stack:** Python 3.13, FastAPI, SQLite, httpx, pytest, `unittest.mock`. LLM routing via `api/services/llm.py` (OpenRouter).

## Global Constraints

- Type hints required on all new/modified functions.
- `ruff` clean; `pytest` green.
- No `.env` files — secrets via macOS Keychain / Docker secrets; config via `llm-config.json` only.
- Do not break the existing OpenAPI contract (no endpoint signature changes here).
- The non-chunked `_run_phase` model resolution is the reference behavior and is already correct. As of the current `chat()` (`api/services/llm.py:600-611`) the priority is: **(0) backend `force_model`** (local-only backends that serve one model) → **(1) explicit `model` arg** → **(2) `phase_models[phase]`** → **(3) backend default**. The chunked formatter uses the `openrouter` backend, which has **no `force_model`**, so passing `model=model_override` (branch 1) wins as intended. This plan makes the worker config, the chunked path, and the recorded model match the reference behavior — it does not change that resolution order.
- Tier labels (`cheapskate/default/big-brain`) are out of scope — do not touch `phase_backends` semantics.

---

### Task 1: Worker reloads config at the start of each job

**Why:** The worker's `LLMService` is loaded once at startup (`worker.py:166`) and never refreshed, so Settings changes made via the API never affect running jobs. A per-job reload is simple and always-correct given low job throughput.

**Files:**
- Modify: `api/services/worker.py` — `process_job` (starts line 657)
- Test: `tests/test_model_selection_integrity.py` (create)

**Interfaces:**
- Consumes: `JobWorker.llm` (an `LLMService` with `reload_config() -> None`, defined at `api/services/llm.py:480`).
- Produces: nothing new; behavioral guarantee that `process_job` calls `self.llm.reload_config()` before running any phase.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_model_selection_integrity.py
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_process_job_reloads_config_before_running(monkeypatch):
    """process_job must reload config (picking up Settings changes) before phases run."""
    from api.services import worker as worker_mod
    from api.services.worker import JobWorker

    w = JobWorker.__new__(JobWorker)
    w.llm = MagicMock()
    w.llm.reload_config = MagicMock()
    w._current_job_id = None

    calls = []
    w.llm.reload_config.side_effect = lambda: calls.append("reload")

    # Short-circuit process_job right after the reload point by making
    # project-dir setup raise, and record ordering.
    def boom(_job):
        calls.append("setup")
        raise RuntimeError("stop here")

    monkeypatch.setattr(worker_mod, "start_run_tracking", lambda job_id: MagicMock())
    monkeypatch.setattr(worker_mod, "log_event", AsyncMock())
    monkeypatch.setattr(worker_mod, "update_job_status", AsyncMock())
    monkeypatch.setattr(worker_mod, "end_run_tracking", AsyncMock(return_value={"total_cost": 0}))
    monkeypatch.setattr(w, "_setup_project_dir", boom)
    monkeypatch.setattr(w, "_heartbeat_loop", AsyncMock())

    with pytest.raises(Exception):
        await w.process_job({"id": 1, "project_name": "X"})

    assert calls and calls[0] == "reload", f"reload must precede setup; got {calls}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_model_selection_integrity.py::test_process_job_reloads_config_before_running -v`
Expected: FAIL — `reload_config` not called (assertion error, `calls` is `['setup']` or empty).

- [ ] **Step 3: Add the reload call at the top of `process_job`**

In `api/services/worker.py`, inside `process_job`, immediately after `self._current_job_id = job_id` (line 660) and before the `logger.info("Processing job", ...)` line, add:

```python
        # Pick up any model/config changes made via the Settings API since
        # this worker process started (api and worker are separate containers).
        self.llm.reload_config()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_model_selection_integrity.py::test_process_job_reloads_config_before_running -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/worker.py tests/test_model_selection_integrity.py
git commit -m "fix(worker): reload LLM config at job start so Settings changes take effect

[Agent: Main Assistant]"
```

---

### Task 2: Resolve config from a shared, env-configurable path (+ seed on startup)

**Why:** Even with a per-job reload, the worker reads its *own* container-local `config/llm-config.json`. Both containers must read/write the *same* file. Make the path an absolute, env-configurable location on a shared volume, seeded from the image default if missing.

**Files:**
- Modify: `api/services/llm.py` — `LLMService.__init__` (line 364)
- Modify: `api/routers/config.py` — `CONFIG_PATH` (line 20) and add a resolver
- Create: `api/services/config_path.py` (single source of truth for the path + seeding)
- Modify: `docker-compose.prod.yml` — add shared config volume + `LLM_CONFIG_PATH` to `api` and `worker`
- Test: `tests/test_model_selection_integrity.py` (append)

**Interfaces:**
- Produces: `api.services.config_path.resolve_config_path() -> pathlib.Path` — returns `Path(os.getenv("LLM_CONFIG_PATH", "config/llm-config.json"))`, and, if the target does not exist but a packaged default (`config/llm-config.json` relative to repo root) does, copies the default to the target (creating parent dirs) before returning.
- Consumes (Task 1's reload benefits from this): `LLMService.reload_config()` re-reads this path.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_model_selection_integrity.py
import os
from pathlib import Path


def test_resolve_config_path_uses_env(monkeypatch, tmp_path):
    from api.services.config_path import resolve_config_path

    target = tmp_path / "shared" / "llm-config.json"
    monkeypatch.setenv("LLM_CONFIG_PATH", str(target))
    # Seed default content so seeding logic has a source.
    monkeypatch.setattr("api.services.config_path.DEFAULT_CONFIG",
                        tmp_path / "default.json")
    (tmp_path / "default.json").write_text('{"primary_backend": "openrouter"}')

    resolved = resolve_config_path()
    assert resolved == target
    assert target.exists(), "missing target must be seeded from default"
    assert "primary_backend" in target.read_text()


def test_resolve_config_path_defaults_relative(monkeypatch):
    from api.services.config_path import resolve_config_path
    monkeypatch.delenv("LLM_CONFIG_PATH", raising=False)
    assert str(resolve_config_path()).endswith("config/llm-config.json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_model_selection_integrity.py -k resolve_config_path -v`
Expected: FAIL — `ModuleNotFoundError: api.services.config_path`.

- [ ] **Step 3: Create the resolver**

```python
# api/services/config_path.py
"""Single source of truth for the LLM config file location.

api and worker run as separate containers; both must read/write the SAME
config file so Settings changes made on the API take effect on the worker.
Point LLM_CONFIG_PATH at a path on a shared volume in production.
"""
import os
import shutil
from pathlib import Path

# Packaged default shipped in the image (repo-relative).
DEFAULT_CONFIG = Path("config/llm-config.json")


def resolve_config_path() -> Path:
    """Return the active config path, seeding it from the packaged default if absent."""
    target = Path(os.getenv("LLM_CONFIG_PATH", str(DEFAULT_CONFIG)))
    if not target.exists() and DEFAULT_CONFIG.exists() and target != DEFAULT_CONFIG:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_CONFIG, target)
    return target
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_model_selection_integrity.py -k resolve_config_path -v`
Expected: PASS

- [ ] **Step 5: Wire the resolver into `LLMService` and `config.py`**

In `api/services/llm.py`, `LLMService.__init__`, replace:

```python
        if config_path is None:
            config_path = "config/llm-config.json"

        self.config_path = Path(config_path)
```

with:

```python
        from api.services.config_path import resolve_config_path

        self.config_path = Path(config_path) if config_path else resolve_config_path()
```

In `api/routers/config.py`, replace `CONFIG_PATH = Path("config/llm-config.json")` (line 20) with:

```python
from api.services.config_path import resolve_config_path

CONFIG_PATH = resolve_config_path()
```

- [ ] **Step 6: Add the shared volume + env to compose**

In `docker-compose.prod.yml`: add a named volume `config-data:` under `volumes:`, and to BOTH the `api` and `worker` services add the mount and env:

```yaml
    environment:
      - LLM_CONFIG_PATH=/data/config/llm-config.json
    volumes:
      - config-data:/data/config
```

(Append these to each service's existing `environment:`/`volumes:` blocks — do not replace them.)

> **Deploy note for the operator:** the live host (cardigan01, CT 103) runs a hand-trimmed 3-service compose, not this file verbatim. The same `config-data` volume + `LLM_CONFIG_PATH` env must be applied to the deployed compose, and the existing API-container config seeded into the shared volume on first boot (the resolver does this automatically when the target is absent).

- [ ] **Step 7: Run the full test file + ruff**

Run: `pytest tests/test_model_selection_integrity.py -v && ruff check api/services/config_path.py api/services/llm.py api/routers/config.py`
Expected: PASS, no lint errors.

- [ ] **Step 8: Commit**

```bash
git add api/services/config_path.py api/services/llm.py api/routers/config.py docker-compose.prod.yml tests/test_model_selection_integrity.py
git commit -m "fix(config): resolve llm-config from shared LLM_CONFIG_PATH so api+worker share one file

[Agent: Main Assistant]"
```

---

### Task 3: Chunked formatter honors `model_override`

**Why:** When chunking is enabled, `_run_phase` routes the formatter to `_run_formatter_chunked` **without** passing `model_override`, and the per-chunk `chat()` call passes no `model`. So formatter model changes (manual retry or, later, escalation) are silently ignored — verified live on job 11.

**Files:**
- Modify: `api/services/worker.py` — `_run_phase` chunked branch (call at ~1559), `_run_formatter_chunked` signature (line 1702), `process_chunk` `chat()` call (line 1817)
- Test: `tests/test_model_selection_integrity.py` (append)

**Interfaces:**
- Consumes: `LLMService.chat(messages, backend, model=None, job_id=None, phase=None)` — passing `model=` overrides phase resolution (`api/services/llm.py:480-489`).
- Produces: `_run_formatter_chunked(self, job_id, chunks, context, project_path, chunking_config, model_override=None)` — new trailing `model_override` param.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_model_selection_integrity.py
@pytest.mark.asyncio
async def test_chunked_formatter_passes_model_override(monkeypatch, tmp_path):
    """Each chunk's chat() call must receive the model_override."""
    from types import SimpleNamespace
    from api.services import worker as worker_mod
    from api.services.worker import JobWorker
    from api.services.chunking import TranscriptChunk

    w = JobWorker.__new__(JobWorker)
    w.llm = MagicMock()
    w.llm.get_backend_for_phase = MagicMock(return_value="openrouter")
    w.llm.get_backend_config = MagicMock(return_value={"timeout": 120})

    seen_models = []

    async def fake_chat(**kwargs):
        seen_models.append(kwargs.get("model"))
        return SimpleNamespace(content="formatted", cost=0.01, total_tokens=10,
                               input_tokens=6, output_tokens=4,
                               model="anthropic/claude-sonnet-4.6")

    w.llm.chat = fake_chat
    monkeypatch.setattr(worker_mod, "log_event", AsyncMock())
    monkeypatch.setattr(w, "_load_agent_prompt", lambda phase: "system")

    chunks = [TranscriptChunk(index=0, content="a", overlap_prefix=""),
              TranscriptChunk(index=1, content="b", overlap_prefix="a")]

    await w._run_formatter_chunked(
        job_id=1, chunks=chunks, context={"analyst_output": ""},
        project_path=tmp_path, chunking_config={"max_parallel": 2},
        model_override="anthropic/claude-sonnet-4.6",
    )

    assert seen_models == ["anthropic/claude-sonnet-4.6", "anthropic/claude-sonnet-4.6"], seen_models
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_model_selection_integrity.py::test_chunked_formatter_passes_model_override -v`
Expected: FAIL — `_run_formatter_chunked` has no `model_override` parameter (TypeError) or `seen_models` is `[None, None]`.

- [ ] **Step 3: Add the parameter and pass it through**

In `api/services/worker.py`:

(a) Update the signature (line 1702):

```python
    async def _run_formatter_chunked(
        self,
        job_id: int,
        chunks: list,
        context: Dict[str, Any],
        project_path: Path,
        chunking_config: Dict[str, Any],
        model_override: Optional[str] = None,
    ) -> Dict[str, Any]:
```

(b) Pass it from the `_run_phase` chunked branch (the `return await self._run_formatter_chunked(...)` call near line 1559) by adding:

```python
                        model_override=model_override,
```

(c) In `process_chunk`, the `self.llm.chat(...)` call (line 1817), add the `model` kwarg:

```python
                    self.llm.chat(
                        messages=messages,
                        backend=backend,
                        job_id=job_id,
                        phase="formatter",
                        model=model_override,
                    ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_model_selection_integrity.py::test_chunked_formatter_passes_model_override -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/worker.py tests/test_model_selection_integrity.py
git commit -m "fix(formatter): thread model_override through the chunked formatter path

[Agent: Main Assistant]"
```

---

### Task 4: Record the model that actually ran (chunked output + post-retry re-validation)

**Why:** Two recording gaps. (a) The chunked formatter records the opaque string `chunked (N chunks via <backend>)` instead of the real model, so family/diagnostic logic can't read it. (b) The post-retry re-validation (`worker.py:482-498`) writes a new `validation_result` and `validator_output.md` but never updates the validator's `phases[].model` — this is exactly why job 10's record said Haiku while the file said Sonnet.

**Files:**
- Modify: `api/services/worker.py` — `process_chunk` return (line ~1837), `_run_formatter_chunked` provenance header (line ~1908) and return dict (line ~1937), post-retry re-validation block (line 493)
- Test: `tests/test_model_selection_integrity.py` (append)

**Interfaces:**
- Consumes: `LLMResponse.model` (the resolved model id from the LLM layer).
- Produces: `_run_formatter_chunked` returns `"model"` = the actual model id; the post-retry re-validation updates the validator entry in `phases[].model`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_model_selection_integrity.py
@pytest.mark.asyncio
async def test_chunked_formatter_records_real_model(monkeypatch, tmp_path):
    """The chunked formatter result must report the model that ran, not 'chunked (...)'."""
    from types import SimpleNamespace
    from api.services import worker as worker_mod
    from api.services.worker import JobWorker
    from api.services.chunking import TranscriptChunk

    w = JobWorker.__new__(JobWorker)
    w.llm = MagicMock()
    w.llm.get_backend_for_phase = MagicMock(return_value="openrouter")
    w.llm.get_backend_config = MagicMock(return_value={"timeout": 120})

    async def fake_chat(**kwargs):
        return SimpleNamespace(content="formatted", cost=0.01, total_tokens=10,
                               input_tokens=6, output_tokens=4,
                               model="anthropic/claude-sonnet-4.6")

    w.llm.chat = fake_chat
    monkeypatch.setattr(worker_mod, "log_event", AsyncMock())
    monkeypatch.setattr(w, "_load_agent_prompt", lambda phase: "system")

    chunks = [TranscriptChunk(index=0, content="a", overlap_prefix="")]
    result = await w._run_formatter_chunked(
        job_id=1, chunks=chunks, context={"analyst_output": ""},
        project_path=tmp_path, chunking_config={"max_parallel": 1},
        model_override="anthropic/claude-sonnet-4.6",
    )
    assert "claude-sonnet-4.6" in result["model"]
    assert "chunked" not in result["model"].split()[0]  # real id, not the opaque string
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_model_selection_integrity.py::test_chunked_formatter_records_real_model -v`
Expected: FAIL — result["model"] is `chunked (1 chunks via openrouter)`.

- [ ] **Step 3: Capture and report the real model in the chunked path**

In `api/services/worker.py`, `process_chunk`'s return dict (line ~1837), add the model:

```python
                return {
                    "content": response.content,
                    "cost": response.cost,
                    "tokens": response.total_tokens,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "model": response.model,
                }
```

After the `chunk_results` aggregation (near line 1895, where `total_cost`/`total_tokens` are summed), add:

```python
            actual_model = next((r.get("model") for r in chunk_results if r.get("model")), None)
```

Change the provenance header (line ~1908) to use it:

```python
            provenance_header = (
                f"<!-- model: {actual_model} (chunked, {total_chunks} chunks) | "
                f"backend: {backend} | "
                f"cost: ${total_cost:.4f} | tokens: {total_tokens} -->\n"
            )
```

Change the return dict's `model` (line ~1937) to:

```python
                "model": actual_model or f"chunked ({total_chunks} chunks via {backend})",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_model_selection_integrity.py::test_chunked_formatter_records_real_model -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for post-retry re-validation model recording**

```python
# append to tests/test_model_selection_integrity.py
def test_revalidation_updates_validator_model():
    """After re-validation, the validator entry in phases[] must reflect the model that judged."""
    # Pure helper test: the update logic lives in a small helper we add.
    from api.services.worker import apply_validator_model

    phases = [
        {"name": "formatter", "model": "anthropic/claude-sonnet-4.6"},
        {"name": "validator", "model": "anthropic/claude-4.5-haiku-20251001"},
    ]
    updated = apply_validator_model(phases, "anthropic/claude-sonnet-4.6")
    val = next(p for p in updated if p["name"] == "validator")
    assert val["model"] == "anthropic/claude-sonnet-4.6"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_model_selection_integrity.py::test_revalidation_updates_validator_model -v`
Expected: FAIL — `ImportError: cannot import name 'apply_validator_model'`.

- [ ] **Step 7: Add the helper and call it from the re-validation block**

In `api/services/worker.py`, add a module-level helper (near the other module-level helpers, above the `JobWorker` class):

```python
def apply_validator_model(phases: list, model: Optional[str]) -> list:
    """Set the validator phase's recorded model to the model that just re-judged."""
    for p in phases:
        if p.get("name") == "validator" and model:
            p["model"] = model
    return phases
```

In the post-retry re-validation block (line 493), after `validation_data = self._parse_validation_result(validator_result["output"])` and before/with the `update_job` call, load the current phases, apply the model, and persist:

```python
                            validation_data = self._parse_validation_result(validator_result["output"])
                            refreshed = await get_job(job_id)
                            phases = refreshed.phases or [] if refreshed else []
                            phases = apply_validator_model(phases, validator_result.get("model"))
                            await update_job(
                                job_id,
                                JobUpdate(validation_result=validation_data, phases=phases),
                            )
```

(Ensure `get_job` and `JobUpdate` are imported in this scope — they are already used elsewhere in `worker.py`.)

- [ ] **Step 8: Run both new tests + ruff**

Run: `pytest tests/test_model_selection_integrity.py -k "real_model or revalidation" -v && ruff check api/services/worker.py`
Expected: PASS, no lint errors.

- [ ] **Step 9: Commit**

```bash
git add api/services/worker.py tests/test_model_selection_integrity.py
git commit -m "fix(worker): record the model that actually ran (chunked output + re-validation)

[Agent: Main Assistant]"
```

---

### Task 5: Full suite + verification gate

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: PASS (no regressions). If pre-existing unrelated failures exist, confirm they fail on `main` too before proceeding.

- [ ] **Step 2: Lint the changed modules**

Run: `ruff check api/ tests/test_model_selection_integrity.py`
Expected: clean.

- [ ] **Step 3: Live smoke (manual, optional, ~$0.15)**

After deploy: set a non-default model for one phase via the Settings screen, submit a small job, and confirm via `GET http://cardigan01:8100/api/jobs/<id>` that `phases[].model` for that phase matches the selection (and the chunked formatter reports a real model id, not `chunked (...)`).

---

## Deferred (not in this plan)

**Fix 4 — stale `current_phase`.** Spec A lists this as minor. Investigation showed `current_phase` *is* updated in both the main and optional phase loops (`worker.py:830`, `~988`); job 10's stale `"seo"` is almost certainly an artifact of the per-phase **retry** flow (which sets/clears `current_phase` independently of pipeline order), not a main-pipeline bug. Because the root cause is ambiguous, writing a fix now risks a test that doesn't capture the real scenario. Defer until the retry-driven `current_phase` lifecycle is reproduced and understood.

## Self-Review

- **Spec coverage:** Fault 1 → Tasks 1+2; Fault 2 → Task 3; Fault 3 → Task 4 (chunked + re-validation); Fault 4 → explicitly deferred with rationale. Goals 1–3 covered; Goal 4 (current_phase) deferred.
- **Placeholder scan:** none — every code/test step shows complete content.
- **Type consistency:** `resolve_config_path() -> Path`, `_run_formatter_chunked(..., model_override=None)`, `apply_validator_model(phases, model) -> list`, and `LLMResponse.model` are used consistently across tasks.
