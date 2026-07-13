# Per-phase model-heft assessment — how much model each Cardigan phase needs

**Date:** 2026-07-03 · **Transcript:** `6POL0201.srt` (*Inside Wisconsin Politics*, 18.5 min,
~3,300 dialogue words) · **Method:** each phase run on 4 model tiers, isolated on the same
fixed baseline upstream (`--context-dir OUTPUT/eval/baseline_20`) so we measure *phase
difficulty*, not error propagation. Quality = Claude review vs `/house-style`.

## Model tiers tested

| Tier | Model | Size | Fits safe (aggressive) ceiling? |
|---|---|---|---|
| **small** | `Qwen3-VL-4B-Instruct` (4B) | ~3 GB | always |
| **mid** | `gemma-4-26B-A4B` (26B/4B MoE) | ~15 GB | only when Studio is idle (dynamic ceiling) |
| **large** | `Qwen3.6-35B-A3B-4bit` (35B/3B MoE) | ~19 GB | **no** — needs guard raised to ~24 GB |
| **cloud** | Claude haiku/sonnet/opus (baseline) | — | — |

## The matrix

| Phase | small 4B | mid 26B (gemma) | large 35B (Qwen) | cloud | **Floor** |
|---|---|---|---|---|---|
| **analyst** | ❌ factual error (Piker's politics inverted), speaker table polluted with glossary names | ✅ faithful, 4 speakers, correct | ✅✅ 9 speakers w/ roles, rich | ✅✅ | **mid** |
| **formatter** | ❌ **broken**: 159% bloat + truncated at 8k cap, 4.6 min | ✅ **0.994 completeness**, faithful | ✅ (extraction — expected faithful) | ✅ (chunked) | **mid** |
| **seo** | ❌ title-case, generic, weak | ⚠️ title-case, "we" voice, thin entities | ✅ full entities + attribution, no "we" | ✅✅ | **large / cloud** |
| **validator** | ⚠️ valid JSON but hallucinated flags | ✅ valid JSON (passes all — low sensitivity) | (not run) | ✅ | **cloud** (trust) |
| **timestamp** | ❌ too slow to finish (>5 min timeout) | ⚠️ clean names, coarse segmentation (merged a segment) | ✅ 6 chapters, separates segments, splits SCOTUS | ✅✅ precise boundaries | **large / cloud** |

## The dividing line

The phases split cleanly by **what kind of work they are**, and that predicts the heft needed:

**Extraction / transcription — MID model is the safe floor.**
`analyst` and `formatter` take self-contained input (the transcript is *in the prompt*) and
produce a faithful restructuring/summary that's cheaply verifiable (completeness coverage,
schema). The mid model (gemma-26B-A4B) does these well at $0 — analyst is correct and
formatter preserves 99.4% of dialogue. **A small 4B is NOT safe even here** — it invents
facts (analyst) and bloats/truncates (formatter). So the floor is *mid*, not small.

**Generation / judgment — needs LARGE-local or CLOUD.**
`seo`, `timestamp`, and `validator` need something the model must *bring*, not just
reshape:
- `seo` needs **world knowledge** (which named entities matter) and **editorial taste**
  (house voice) — mid is thin and off-voice; large closes most of the gap; residual
  house-style issues are prompt-fixable (done).
- `timestamp` needs **precise segmentation judgment** — mid is coarse; large is close;
  cloud is best.
- `validator` is the **quality gate** — it must be *trustworthy*, and "local grades local"
  correlates errors. Keep on cloud regardless of size until trust is earned.

## Bottom line (per-phase routing recommendation)

| Phase | Recommended | Why |
|---|---|---|
| analyst | **local mid (gemma)** — or large if the ceiling is up | faithful extraction, $0 |
| formatter | **local mid (gemma)**, via the worker's chunking for long transcripts | 0.994 completeness, $0 |
| seo | **cloud** now; **local large (Qwen-35B) viable** once the memory ceiling is raised + with the fixed prompt | knowledge + voice |
| timestamp | **cloud** (or local large) | segmentation precision |
| validator | **cloud** | trust / avoid local-grades-local |

**Net:** a *small* model earns no phase; the *mid* model safely owns the two extraction
phases (the bulk of token spend), and everything requiring knowledge/taste/precision stays
*cloud* — or moves to a *large* local model only if the Studio gets the memory headroom
(see [[omlx-capacity-optimization-future]]). Caveat: even the mid model's loadability is
gated by the **dynamic** aggressive ceiling — a busy Studio can 507 gemma
(`defer_when_unavailable` requeues, but slower).

## Caveats
- One episode, one genre (politics). Confirm on longer cross-genre programs before locking
  routing. Single-pass (no chunking) in this harness; production formatter chunks.
- 4B timestamp was not completed (timed out) — recorded as "impractically slow", which is
  itself the verdict for that pairing.
