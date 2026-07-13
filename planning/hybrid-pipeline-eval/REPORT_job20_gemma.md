# Eval — local oMLX (gemma-4-26B-A4B) vs production baseline (job 20)

**Date:** 2026-07-02 · **Backend:** `local-llm` → oMLX @ `127.0.0.1:8000` ·
**Model:** `gemma-4-26B-A4B-it-QAT-MLX-4bit` (the oMLX `is_default`; 26B total / **4B active** MoE, ~15 GB) ·
**Transcript:** `6POL0201.srt` — *Inside Wisconsin Politics*, Democratic gubernatorial primary
(18.5 min, ~3,300 dialogue words) · **Baseline job:** #20 on `cardigan01` (cloud Claude).

Harness: `scripts/eval_pipeline.py` (standalone, DB/Langfuse-free; single-pass per phase — **no
chunking**, unlike the production formatter). Quality = Claude review against `/house-style`.

## Headline

gemma handles the **extraction/transcription** phases (analyst, formatter) faithfully at **$0**,
but the **copy** phase (SEO) shows real house-style violations and thinner entity coverage, and
**timestamp segmentation** is coarser than cloud. Machine metrics all green; quality is
phase-dependent.

## Per-phase metrics

| Phase | Local model | Local in/out tok | Local wall | Baseline model | Base tok | Base $ |
|---|---|--:|--:|---|--:|--:|
| analyst | gemma-4-26B-A4B | 20,075 / 1,689 | 50.5s | claude-4.5-haiku | 21,211 | $0.031 |
| formatter | gemma-4-26B-A4B | 24,803 / 4,422 | 90.9s | claude-4.6-sonnet | 42,028 | $0.051 |
| seo | gemma-4-26B-A4B | 4,996 / 1,612 | 35.9s | claude-sonnet-5 | 12,923 | $0.022 |
| validator | gemma-4-26B-A4B | 8,305 / 97 | 10.3s | claude-4.5-haiku | 14,763 | $0.015 |
| timestamp | gemma-4-26B-A4B | 18,970 / 321 | 23.5s | claude-4.6-sonnet | 15,676 | $0.017 |
| **total** | | **8,141 out** | **211s** | | 106,601 | **$0.136** |

- **Time:** local pipeline **~3.5 min** vs baseline whole-job **74.5s** (~2.8× slower wall-clock).
  Caveat: local oMLX is single-lane on the shared Studio; cloud runs phases fast/parallel on the
  LXC. Directional, not a controlled benchmark. Per-phase local throughput ~35–50 tok/s.
- **Cost:** $0 local vs $0.136/job cloud.
- **Formatter completeness:** **coverage_ratio 0.994** (3,296/3,316 dialogue words) — single-pass
  and still faithful; no dropped content.

## Quality (house-style rubric, 1–5)

| Phase | Score | Verdict |
|---|--:|---|
| **analyst** | 4 | Strong. Correct themes, speakers (Shawn Johnson/host, van Wagtendonk, Kremer, Ann Jacobs), Act structure, caught the $92K Hong figure + "Frank's RedHot" detail. More concise than haiku (1,072 vs ~4k words) but faithful. **Keep-local candidate.** |
| **formatter** | 4 | Strong faithfulness (0.994 coverage). Needs a speaker-attribution spot-check, but content preserved single-pass. **Keep-local candidate** (route long transcripts through the real worker for chunking parity). |
| **seo** | 2 | Weakest. Violations: **title case** ("Attacks Heat Up" — house wants down style), sensational teaser ("THE ATTACKS BEGIN" thumbnail, "Attacks Heat Up"), first-person "we also analyze". Thinner: no candidate names in title, dropped Francesca Hong/Hasan Piker prominence and the Evers/Trump snub, **no quote attribution**. **Fabricated** Search-Volume/Difficulty columns with no data (baseline flags "no SEMRush data"). Baseline (sonnet-5) is clearly better. **Keep-cloud.** |
| **validator** | 3 | Produced a structurally valid JSON verdict (`overall: pass`) at 97 tokens — correct format. But it graded local-on-local (error-correlated) and didn't flag the SEO house-style issues (it checks structural flags, not copy nuance). **Keep-cloud for trust.** |
| **timestamp** | 3 | House-style-clean *naming* (sentence case, good titles, `0:00 Episode intro`), but **weaker segmentation**: implausible 5-min "intro" (baseline: 22s), merged the distinct Francesca Hong segment away (5 chapters vs baseline's 6). Boundaries less accurate. **Lean keep-cloud / needs work.** |

## Recommendation (this transcript, this model)

- **Route to local now:** `analyst`, `formatter` — faithful, $0, good enough for the brainstorm
  doc and transcript cleanup. Formatter should still use the worker's chunking path for long
  content (this run was single-pass).
- **Keep on cloud:** `seo` (house-style + entity richness matter for published copy), `validator`
  (trust / avoid local-grades-local), `timestamp` (segmentation accuracy).
- This is one 18-min politics episode. Confirm the pattern on the planned longer, cross-genre
  programs before any deploy decision.

## Notes / limitations

- Single-pass (no chunking) — for a fully production-faithful whole-pipeline run, drive a real
  local worker job via an `LLM_CONFIG_PATH` scratch config (all `phase_backends` → local-llm).
- gemma mis-fills the cosmetic `**Model:**` field as "GPT-4o" (harmless boilerplate; the actual
  served model is recorded in metrics).
- oMLX had to be restarted (was wedged); I unloaded the pinned `Qwen3-VL-4B` to fit gemma. oMLX
  ceiling ~16 GB — of installed models only gemma (15 GB) and Qwen3-VL-4B fit; `Qwen3-Coder-30B`
  (16.8 GB) returns HTTP 507.

## Next (Experiment 2)

For the weak phases (seo, timestamp): `/model-fit recommend` for a better-fitting local model,
then `scripts/shadow_eval_phase.py` (to build) to sweep candidates on that single phase using the
real upstream context — but note the ~16 GB ceiling sharply limits options beyond gemma.
