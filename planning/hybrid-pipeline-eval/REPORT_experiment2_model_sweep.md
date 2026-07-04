# Experiment 2 — model sweep + memory-headroom exploration

**Date:** 2026-07-02/03 · **Baseline:** job 20 (`6POL0201.srt`, *Inside Wisconsin Politics*) ·
**Method:** isolated per-phase eval on **fixed baseline upstream** (each model's seo/timestamp
saw the *same* baseline analyst+formatter outputs → fair A/B), `scripts/eval_pipeline.py
--context-dir OUTPUT/eval/baseline_20`. Quality = Claude review vs `/house-style`.

## Models compared

| Model | Size | Fits aggressive (~16 GB) ceiling? | Speed |
|---|---|---|---|
| `gemma-4-26B-A4B-it-QAT-MLX-4bit` (Exp 1 default) | ~15 GB | yes | ~30–50 tok/s |
| `Qwen3.6-35B-A3B-4bit` (mlx-community) | ~19 GB | **no — needs ceiling raised to ~24 GB** | ~30 tok/s |
| baseline (cloud Claude: haiku/sonnet/sonnet-5) | — | — | fast/parallel |

*(A `pahajokiconsulting/…MXFP4` quant model-fit ranked #1 was a **dud** — 22 GB, packed-weight
format oMLX's MLX runtime can't load. The canonical `mlx-community/Qwen3.6-35B-A3B-4bit` is the
one that works — same model family dougie used.)*

## SEO phase — the decisive A/B (identical baseline upstream)

| | Title | Entities named | Voice | House-style flags |
|---|---|---|---|---|
| **baseline (sonnet-5)** | "…Turns Negative: Barnes vs. Rodriguez" | Barnes, Rodriguez, Hong, Piker, Tiffany, Evers, Roys + panelists | descriptive | flags "no SEMRush data" honestly |
| **Qwen3.6-35B** | "…Attacks, Fundraising & Court Rulings" | Barnes, Rodriguez, Hong, Piker, Tiffany, Evers **+ panelist attribution** | **descriptive (no "we")** | title-case; "Gub" abbrev; fabricates SEO-volume cols |
| **gemma-26B** | "…Attacks & Fundraising Controversy" | Barnes, Hong, Piker (fewer) | **first-person "We break down…"** | title-case; "we" voice; fabricates SEO-volume cols |

**Qwen3.6-35B clearly beats gemma on SEO** — full entity set, panelist attribution, and it drops
the off-voice "we". It approaches baseline richness. Residual issues (title-case titles vs
down-style, fabricated Search-Volume/Difficulty tables) are **prompt-level**, not model-size —
they persist across both local models and would need a prompt fix, not a bigger model.

## Other phases

| Phase | gemma-26B | Qwen3.6-35B | vs baseline |
|---|---|---|---|
| analyst | 4 speakers, 5 themes, terse (1,072 w) | **9 speakers** w/ roles, 5 rich themes (1,897 w) | Qwen ≈ baseline |
| timestamp | 5 chapters, merged Hong segment | **6 chapters**, separates Hong + splits SCOTUS | Qwen better; both mis-size the intro (boundary timing < baseline) |
| formatter | 0.994 completeness (Exp 1) | not re-run (extraction phase; gemma already faithful) | — |

## Verdict

**Qwen3.6-35B-A3B-4bit wins the sweep** — materially better than gemma on analyst, seo, and
timestamp, closing much of the gap to cloud Claude. **But it requires raising oMLX's memory
guard** (~16 GB → ~24 GB): 19 GB resident leaves ~14 GB of the 36 GB box for everything else.

Updated per-phase routing recommendation (if the ceiling is raised for Qwen3.6-35B):
- **local:** analyst, formatter (both strong, $0), and **seo now viable** with Qwen-35B + a
  prompt tweak (down-style titles, drop the fake SEO-volume tables).
- **cloud / needs work:** timestamp (boundary precision), validator (keep cloud for trust).

## Memory-headroom exploration — what more RAM unlocks (data-backed)

- **At the safe `aggressive` ceiling (~16 GB):** gemma-26B-A4B is the best general model that
  fits; SEO quality is the weak spot.
- **Raising to ~24 GB** unlocks **Qwen3.6-35B-A3B-4bit** → the SEO/analyst quality jump measured
  above. This is the single highest-value lever and the concrete answer to "what would more
  memory buy us."
- **Higher still / co-residency (~28–32 GB):** would allow higher-precision quants (5–6 bit →
  less quantization loss) or keeping two models resident (a strong copy model + gemma) to avoid
  load/unload churn between phases — but pushes a 36 GB daily-driver into memory pressure
  (existing swap already ~14 GB).
- **Real fix (per [[omlx-capacity-optimization-future]]):** a dedicated inference partition
  (lean Studio user for inference, daily work elsewhere) or a dedicated box — so a 19–22 GB model
  can sit resident without competing with Pro Tools/Avid/browser.

## Test conditions / cleanup

- Ceiling raised to 27 GB for the Qwen-35B test, then **restored to `aggressive`/0.0**; model
  unloaded; oMLX restarted; original pin (`Qwen3-VL-4B`) intact. Machine returned to 82% free.
- `Qwen3.6-35B-A3B-4bit` (19 GB) **kept** on disk (it won the sweep) — but disk is 96% full and
  it's only usable with the ceiling raised; keep-long-term is the user's call.
- Prompt-level follow-up (down-style titles, remove fabricated SEO-volume tables) would lift
  BOTH local models' SEO output and is independent of the memory decision.

## Addendum — SEO prompt fix (2026-07-03)

Applied the prompt-level fixes to `prompts/seo.md` (helps cloud + every local model):
- New **"## Copy style (REQUIRED)"** section: down-style titles, no clickbait/CTA, third
  person (no "we"), spell out place names — moved OUT of the output template (a first
  attempt put the rules *inside* the title template and a 4B model echoed them verbatim;
  lesson: rules go in a rules section, the template shows only output shape).
- Keyword tables: replaced the `Search Volume | Difficulty` columns (which models fabricate
  with no data) with a groundable **`Source` (direct/implied)** column + an explicit
  "do not invent metrics" note; volume/difficulty stay in the SEMRush section, data-gated.
- Description templates: third-person, no calls to action.

**Validation:** structural check on `Qwen3-VL-4B` confirmed the keyword-table fix and that
the instruction-leak is gone. **Full quality re-test on gemma/Qwen-35B is pending** — the
box was memory-pressured at test time (see below) so the mid/large models wouldn't load.

## Operational finding — the `aggressive` ceiling is DYNAMIC

It's computed from *current free RAM*, not fixed: ~16.2 GB at ~80% free, but **~13.97 GB at
~40% free** (after large-model churn maxed swap). So `gemma-26B` (15.26 GB) loads on an idle
Studio but **`507`s on a busy one**. Real reliability caveat for pointing Cardigan at oMLX;
`defer_when_unavailable` requeues but adds latency. Reinforces the dedicated-inference-host
argument.
