# Stage 0.6 — cloud baseline corpus (six transcripts)

Six fresh `scripts/eval_pipeline.py` runs against the `openrouter` (cloud)
backend, with `--style-report`/`--emit-normalized` on, run **2026-07-10**
against HEAD `ad2a805` (post style-engine/feedback-loop work). This is the
pre-hybrid-pipeline baseline: current prompts, current
`config/house_style.yaml` rules, deterministic house-style checks run
*after* generation (no model-side enforcement yet). It exists to capture
what the cloud models produce and violate today, before the hybrid
deterministic-pipeline work changes anything.

Raw run artifacts (phase outputs, full `report.md`) live in the git-ignored
`OUTPUT/eval/local_cloud_base_*/` directories and are **not** committed —
only each run's `metrics.json` is copied into this directory (see
"Regenerating" below to reproduce them).

## Corpus manifest

| Label | Source file | Show / genre | Duration† | Source words | Content type | Phases run |
|---|---|---|--:|--:|---|---|
| `6POL0201` | `6POL0201.srt` | Inside Wisconsin Politics (politics/public-affairs panel) | 18.4 min | 3,316 | full | analyst, formatter, seo, validator, timestamp |
| `6POL0202` | `6POL0202.srt` | Inside Wisconsin Politics | 18.0 min | 3,816 | full | analyst, formatter, seo, validator, timestamp |
| `6POL0114C` | `6POL0114CLEAN.srt` | Inside Wisconsin Politics — chunking-scale case | 19.4 min | 3,138 | full | analyst, formatter, seo, validator, timestamp |
| `2WLIJD` | `2WLIJingleDressesSM.srt` | Wisconsin Life — Jingle Dress Designer segment | 4.0 min | 278 | full | analyst, formatter, seo, validator |
| `6HNPGD3` | `6HNP2449GreendeerClip3.srt` | Here & Now — Ho-Chunk burial mounds clip | 2.8 min | 411 | full | analyst, formatter, seo, validator |
| `TLBCORN` | `TLB_PD_CORN.srt` | The Look Back — Menominee agriculture short | 1.0 min | 165 | **short** | analyst, formatter, seo, validator |

† Elapsed duration measured directly from each SRT's first/last caption
timestamp (`last_end − first_start`), not the raw wall-clock hour value in
the timecodes — `6POL0114CLEAN.srt`'s captions carry absolute
time-of-day timestamps (`13:39:15 → 13:58:36`, a live-caption artifact),
which would read as 838 minutes if you naively subtracted from midnight.
Its actual elapsed runtime (19.4 min) and word count (3,138) are in the
same range as the other two politics episodes — this transcript was picked
specifically because 3,138 words sits just above the production
formatter's 3,000-word single-pass chunking threshold (`scripts/eval_pipeline.py`
docstring), not because it's an unusually long episode.

**Content type provenance.** `metrics.json` doesn't store `--content-type`
directly; it's inferred here from each run's `seo_output.md` framing.
`TLBCORN` explicitly self-labels `**Format:** YouTube Short (<90 seconds)`,
uses a 40-char title cap, and appends `#Shorts` — confirms `--content-type
short`. `2WLIJD` and `6HNPGD3` use the same 80/90-char (title/short-desc)
framing as the three politics runs, despite being short clips content-wise
— confirms `--content-type full` (the default) was used for those two, and
their `--phases` list simply omitted `timestamp` (short clips don't need
chapter markers; production's `_should_run_timestamp_phase` skips it for
Shorts automatically, but these two aren't Shorts — the omission here was
by explicit `--phases` choice at invocation, not the worker's own gate).

## Backend / model routing

All runs: `--backend openrouter`. Per-phase model routing was identical
across all six runs:

| Phase | Model |
|---|---|
| analyst | `anthropic/claude-4.5-haiku-20251001` |
| formatter | `anthropic/claude-4.6-sonnet-20260217` |
| seo | `anthropic/claude-4.5-haiku-20251001` |
| validator | `anthropic/claude-4.5-haiku-20251001` |
| timestamp | `anthropic/claude-4.6-sonnet-20260217` |

**Total corpus cost: $0.5841** across 27 phase calls (6POL0201 $0.1355,
6POL0202 $0.1279, 6POL0114C $0.1666, 2WLIJD $0.0556, 6HNPGD3 $0.0588,
TLBCORN $0.0397 — per-run sums of `metrics.json`'s `phases[].cost`).

## Style violations by rule_id (seo phase, pre- vs. post-normalization)

Deterministic `config/house_style.yaml` checks (`limits.*`) run against the
seo phase's extracted `title` / `short_description` / `long_description`
fields, before and after the title-only down-style normalization pass.
Normalization never touches field length, so pre/post counts are always
equal — this table is really about **which rules fire**, not about
normalization fixing anything.

| Run | `limits.short_description.max` | `limits.long_description.max` |
|---|:--:|:--:|
| `6POL0201` | 1 / 1 (140 chars, limit 90) | 1 / 1 (369 chars, limit 350) |
| `6POL0202` | 1 / 1 (103 chars, limit 90) | 1 / 1 (400 chars, limit 350) |
| `6POL0114C` | 1 / 1 (110 chars, limit 90) | 1 / 1 (468 chars, limit 350) |
| `2WLIJD` | 1 / 1 (139 chars, limit 90) | 1 / 1 (402 chars, limit 350) |
| `6HNPGD3` | 1 / 1 (132 chars, limit 90) | 1 / 1 (357 chars, limit 350) |
| `TLBCORN` | 1 / 1 (142 chars, limit 90) | *(field not extracted — short-form seo report has no long_description)* |

**6 of 6 runs violate `short_description.max`; 5 of 5 applicable runs
violate `long_description.max`.** This is the headline pre-hybrid finding:
every single cloud run in this corpus overshoots the short-description
limit despite `prompts/seo.md` stating "Short Description (90 chars max)"
verbatim in the phase instructions — the model is told the limit and
blows past it anyway, by 13–58 characters (14–63% over) every time.
`fidelity_6POL0201.md` shows this isn't new: replaying 2026-07-02's
old-prompt production output through today's rules trips the exact same
two rule_ids at almost identical severity.

**`TLBCORN` (content_type=short) is a structural double-bind, not just a
model miss.** The `content_type == "short"` prompt injection in
`api/services/worker.py` (`_build_phase_prompt`, seo phase) tells the model
to "write a description under 200 characters" — but
`config/house_style.yaml`'s `limits.content_type_overrides.short` only
overrides `keywords` count, not `short_description.max` (still 90,
inherited from `limits.fields`). So a content_type=short job is
instructed toward a 200-char target by one part of the prompt while the
deterministic engine enforces 90 — a guaranteed-violation setup
independent of model quality. Worth a follow-up ticket regardless of the
hybrid-pipeline work.

## Title: raw vs. normalized (down-style casing pass)

| Run | Title (raw) | Title (normalized) | Changed |
|---|---|---|---|
| `6POL0201` | Wisconsin Democratic primary turns negative ahead of **August** vote | …ahead of **august** vote | yes |
| `6POL0202` | Wisconsin Democratic primary splits: establishment vs. progressive camps | *(identical)* | no |
| `6POL0114C` | Wisconsin Democratic primary: **Seven** candidates compete at state convention | …: **seven** candidates compete… | yes |
| `2WLIJD` | Jingle dress designer Aerius Benton-Banai on healing through **Ojibwe** art | …through **ojibwe** art | yes |
| `6HNPGD3` | Protecting Wisconsin's burial mounds: the **Ho-Chunk Nation's** fight | …the **ho-chunk nation's** fight | yes |
| `TLBCORN` | Menominee ancestors farmed Wisconsin at massive scale | *(identical)* | no |

4 of 6 titles changed under normalization. Of those four, **one is
correct** (`6POL0114C`: "Seven" is an ordinal common word mid-title, not a
proper noun — down-style is right to lowercase it) and **two look like
normalizer bugs**: `Ojibwe` and `Ho-Chunk Nation` are proper nouns
(demonym / tribal-nation name) that get lowercased because they're absent
from both `config/house_style.yaml`'s static `casing.proper_nouns` seed
*and* the per-episode `extract_proper_nouns` pass — which only harvests
names out of the analyst's "Speakers & Roles" table
(`api/services/style_engine/entities.py`), never place names, ethnonyms,
or tribal nations. `August` (`6POL0201`) is the same failure mode applied
to a month name — down-style has no month-name carve-out at all, so any
title containing a month gets it lowercased. None of these are wrong in a
way the current pipeline would catch (`title` is never checked against a
"changed for the worse" rule) — they're silent casing regressions worth
fixing in `casing.proper_nouns` (add tribal nations / demonyms) or via a
month-name exception, independent of the hybrid-pipeline work.

## Fidelity check vs. old-prompt production baseline

See `fidelity_6POL0201.md` — `local_cloud_base_6POL0201` vs. production
job 20 (`OUTPUT/eval/baseline_20/`, completed 2026-07-02, pre-style-engine
prompts). Short version: same model pair both eras (no model-mix
confound); the real confound is that `config/house_style.yaml`'s field
limits tightened (60/150/300 → 80/90/350) between job 20 and now.
Recomputing today's style engine against job 20's *unmodified* old output
shows it trips the identical two rule_ids at nearly identical severity —
so the short/long-description overflow is a pre-existing condition, not a
new-prompt regression. Full writeup and numbers in that file.

## Files in this directory

- `README.md` — this manifest.
- `comparison.md` — `eval_compare` across all six new runs, baselined on `6POL0201`.
- `fidelity_6POL0201.md` — `eval_compare` of `6POL0201` vs. old-prompt `baseline_20`, plus written fidelity assessment.
- `metrics_6POL0201.json`, `metrics_6POL0202.json`, `metrics_6POL0114C.json`, `metrics_2WLIJD.json`, `metrics_6HNPGD3.json`, `metrics_TLBCORN.json` — each run's full `metrics.json` (phase tokens/cost/wall-clock, completeness, style report).
- `metrics_baseline_20.json` — job 20's manifest reconstructed into the same `metrics.json` shape (job 20 predates `metrics.json`/`--style-report`), including a post-hoc recomputation of the seo-phase style report against today's rules. See the "Inputs" note in `fidelity_6POL0201.md` for how this was built.

## Regenerating the raw artifacts

Raw phase outputs (`analyst_output.md`, `formatter_output.md`,
`seo_output.md`, `seo_output.normalized.md`, `validator_output.md`,
`timestamp_output.md`, `report.md`) are git-ignored under `OUTPUT/eval/` and
not committed. Reproduce them with:

```bash
# Five-phase full-length runs (6POL0201, 6POL0202, 6POL0114C):
PYTHONPATH=. venv/bin/python scripts/eval_pipeline.py \
  --transcript transcripts/6POL0201.srt \
  --backend openrouter --label cloud_base_6POL0201 \
  --content-type full \
  --emit-normalized --rules config/house_style.yaml \
  --out OUTPUT/eval

# Four-phase full-length runs, no timestamp phase (2WLIJD, 6HNPGD3):
PYTHONPATH=. venv/bin/python scripts/eval_pipeline.py \
  --transcript transcripts/2WLIJingleDressesSM.srt \
  --backend openrouter --label cloud_base_2WLIJD \
  --content-type full \
  --phases analyst,formatter,seo,validator \
  --emit-normalized --rules config/house_style.yaml \
  --out OUTPUT/eval

# Shorts run (TLBCORN):
PYTHONPATH=. venv/bin/python scripts/eval_pipeline.py \
  --transcript transcripts/TLB_PD_CORN.srt \
  --backend openrouter --label cloud_base_TLBCORN \
  --content-type short \
  --phases analyst,formatter,seo,validator \
  --emit-normalized --rules config/house_style.yaml \
  --out OUTPUT/eval
```

Swap `--transcript`/`--label` per remaining transcript for the other two
politics runs. Requires the `openrouter` backend already configured in the
app's LLM config (`OPENROUTER_API_KEY` from macOS Keychain — never
hard-coded). Output lands at `OUTPUT/eval/local_<label>/`. Then:

```bash
venv/bin/python -m scripts.eval_compare OUTPUT/eval/local_cloud_base_* \
  --baseline OUTPUT/eval/local_cloud_base_6POL0201 \
  --out planning/hybrid-pipeline-eval/stage0-baseline/comparison.md
```
