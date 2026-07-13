# Cardigan hybrid deterministic-LLM pipeline — planning kickoff

> **Original request:** Craft a prompt to kick off a planning session on an exploratory
> feature branch that will likely result in an app version bump (mostly behind the scenes).
> Account for: every step of the pipeline needs evaluation and a mechanism for updating style
> rules based on performance during previous runs; a staged plan for simplifying agent prompts
> alongside adding the deterministic tools agents will use; and the constraint that the
> deterministic tools run as a step *before* inference is sent to the API endpoint.

## Role
You are the lead architect for an **exploratory redesign** of Cardigan's transcript→metadata
pipeline, running a **plan-mode session on a dedicated feature branch**. Produce a staged,
reviewable implementation plan — do not write production code in this session. The work will
likely land as an **app version bump**, even though most of the change is behind-the-scenes
pipeline behavior.

## Context
Cardigan runs six LLM roles (analyst, formatter, seo, validator, timestamp, copy_editor). The
worker (`api/services/worker.py`, `_run_phase`) threads each phase's output into the next and
calls `LLMClient.chat()` (`api/services/llm.py`), which sends inference to an OpenAI-compatible
API endpoint (OpenRouter or a local oMLX server). Rules live in `prompts/*.md` and the
canonical `/house-style` skill; routing is in `config/llm-config.json`.

**The problem, established empirically in a prior evaluation session:** every phase asks the
LLM to do both a small **semantic core** and a large **deterministic shell** — character
limits, down-style casing, forbidden-word/voice rules, keyword extraction, SRT timecode math,
output formatting, and validation. The model does the shell *badly* (house-style violations
that vary by model — one model over-capitalizes titles, another over-lowercases and drops
proper nouns) and *expensively* (bigger models, more tokens).

**Evidence in the repo — read these first (they survive as `OUTPUT/eval/` is git-ignored):**
- `OUTPUT/eval/REPORT_job20_gemma.md`, `REPORT_experiment2_model_sweep.md`,
  `REPORT_phase_heft_assessment.md` — per-phase quality/speed/cost across model tiers
  (4B / mid gemma-26B-A4B / large Qwen3.6-35B-A3B / cloud) vs a production baseline job.
- The reports document two prototypes from that session that you should **recreate/extend**:
  - a **standalone, DB-free phase eval harness** (`scripts/eval_pipeline.py`) that runs any
    phase on any backend, isolated on fixed upstream context, and computes completeness — this
    is your **evaluation mechanism** for acceptance criteria; and
  - a **house-style normalizer PoC** (`scripts/poc_house_style_normalizer.py`) that
    deterministically normalized SEO titles to house style, **converging different models'
    outputs to identical correct copy**, added char-limit + forbidden-word + first-person
    checks, and computed per-episode proper nouns from the analyst speaker table. Its one edge
    case was fixed by editing config, never the engine — proving the **rule-engine (code) /
    rule-data (config)** separation.
- The `/house-style` skill — canonical rules, including its own "Divergences to reconcile"
  section (prompts and guide already drift; e.g. short-desc hard limit 90 vs the seo prompt's
  "150 max").
- `api/services/completeness.py`, `seam_coverage.py` — existing precedent for deterministic
  checks wrapping a phase.
- The local-LLM/oMLX backend work is committed on `feat/local-llm-omlx-backend` (`40bf6c1`) —
  reference for how phases route to a local vs cloud endpoint.

**Findings to build on:** phases split into **extraction** (analyst, formatter —
self-contained, verifiable; a mid MoE local model handles them faithfully at $0) and
**generation/judgment** (seo, timestamp, validator — need knowledge/taste/precision). MoE beats
dense for throughput. The winning lever is *less model work, not more model*: move the
deterministic shell to code and fidelity becomes **model-agnostic**, so a smaller/local model
suffices.

## Objective
Design a hybrid architecture in which each phase is wrapped by deterministic tooling — a
**pre-inference stage** that computes and injects rule data (shrinking the model's task) and a
**post-inference stage** that enforces/validates deterministic rules — while agent prompts are
progressively simplified as the tools absorb their deterministic responsibilities. Rules live
in **editable data (single source of truth)**, and a **feedback mechanism updates the rules
based on evaluation of previous runs**.

## The plan must cover
1. **Evaluate every phase.** For all six roles, decompose into (a) irreducibly-semantic core →
   stays LLM, (b) deterministic shell → moves to code, (c) fuzzy rule → *flag* for LLM/human.
   Baseline each with the eval harness + reports; define per-phase success metrics.
2. **Rule engine + rule data separation.** A single house-style rule source (e.g.
   `house_style.yaml`) consumed by **both** the deterministic tools and the prompt-builder —
   killing the prompt-vs-guide drift. Specify stable engine primitives (down-style normalizer,
   char-limit enforcer, forbidden-word scanner, entity/keyword extractor, SRT timecode snapper,
   format emitters) vs. the data (limits, forbidden lists, casing variants, proper-noun seeds,
   verb prefs). Content-dependent data (per-episode proper nouns) is **computed per job, not
   maintained**.
3. **Pre-inference deterministic stage (hard constraint).** The deterministic tools run as a
   step **before** the `chat()` API call in the phase runner. Specify where in `_run_phase`
   they execute, what they compute (entities/proper nouns, direct keywords, char budgets,
   parsed SRT timecodes, prior-phase structured data), and how they inject into the
   prompt/context. Also specify the **post-inference** stage (normalize + validate output) and
   how failures route (enforce vs flag; retry vs pause).
4. **Enforce-vs-flag tiering.** Deterministic-and-unambiguous rules are **enforced** in code
   (casing, limits, format); fuzzy rules are **flagged** (code detects, LLM/human resolves).
   Deterministic *rewriting* that changes meaning (trimming a 189-char description to 90) stays
   a flag, never an auto-fix.
5. **Staged prompt-simplification.** As each tool lands, strip the corresponding rules from
   that phase's prompt. Sequence phase-by-phase, highest fidelity-per-effort first (seo
   casing/limits normalizer + validator lint suite), always incremental — un-encoded rules keep
   flowing through the prompt; no big-bang.
6. **Rule-update feedback loop.** Design how evaluation of previous runs updates the rule data:
   eval-harness signals and editor corrections surface recurring violations → a
   review-and-approve step → a **config edit** (preserving the data-not-code maintainability
   property). Define how "performance during previous runs" is measured and fed back, and who
   approves rule changes (editors edit data, not code).
7. **Versioning & rollout.** Respect Cardigan's versioning (git tag is SoT;
   `docs/COST_DATA_VERSIONING.md` / `docs/VERSIONING.md`; `CARDIGAN_VERSION`). Plan to A/B the
   hybrid pipeline against the current one via the harness before flipping, and how per-phase
   routing (`config/llm-config.json` `phase_backends`) shifts toward smaller/local models as
   the tools absorb deterministic load.

## Approach
- **Phase 1 (research — dispatch parallel agents):** (a) map `worker.py` phase execution, where
  `chat()` is called, how context threads between phases; (b) inventory the deterministic rules
  embedded across `prompts/*.md` + `/house-style`, tagging each per phase as deterministic /
  fuzzy / semantic; (c) study `completeness.py` / `seam_coverage.py` + the eval-harness approach
  so new tools follow the established pattern.
- **Phase 2 (design):** rule-source schema; pre/post deterministic-stage interfaces; per-phase
  decomposition tables; the feedback loop; the staged migration sequence with per-phase
  acceptance criteria measured via the harness.
- **Phase 3:** write the staged plan — concrete files to add/change, representative examples,
  and a verification section (A/B via harness; don't break the API/OpenAPI contract).

## Considerations / Constraints
- **Maintainability is the #1 constraint:** rule updates must be config/data edits, not engine
  changes — prove it the way the PoC did.
- Don't break the API contract; keep the DB-backed queue + worker model; deterministic tools
  must be unit-testable in isolation (DB-free, like the PoC/harness).
- **Model-agnostic fidelity is the goal:** after the tools land, a smaller/local (gemma-tier)
  model should suffice for more phases — validate with the harness.
- Incremental and reversible; feature-flag the hybrid path where sensible.
- Honor the pre-inference constraint: deterministic tooling is a **first-class pipeline stage
  before the API call**, not an afterthought on output.

## Deliverable
A staged, reviewable plan-mode implementation plan: per-phase decomposition, the rule-source +
tool interfaces, the feedback mechanism, the prompt-simplification sequence, and harness-based
acceptance criteria — ready to execute on the feature branch.
