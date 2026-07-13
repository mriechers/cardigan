# eval_compare report

- Runs: local_cloud_base_6POL0201, baseline_20
- Baseline: `baseline_20`

## Per-phase metrics

| Run | Phase | Model | Total tokens | Cost | Wall s | Output words | coverage_ratio |
|---|---|---|--:|--:|--:|--:|--:|
| local_cloud_base_6POL0201 | analyst | claude-4.5-haiku-20251001 | 23363 | 0.037115 | 68.0 | 4128 | — |
| local_cloud_base_6POL0201 | formatter | claude-4.6-sonnet-20260217 | 31219 | 0.040741 | 123.9 | 3490 | 1.01 |
| local_cloud_base_6POL0201 | seo | claude-4.5-haiku-20251001 | 15157 | 0.023653 | 48.5 | 2413 | — |
| local_cloud_base_6POL0201 | validator | claude-4.5-haiku-20251001 | 16814 | 0.017444 | 4.6 | 157 | — |
| local_cloud_base_6POL0201 | timestamp | claude-4.6-sonnet-20260217 | 15422 | 0.016586 | 11.3 | 277 | — |
| baseline_20 | analyst | claude-4.5-haiku-20251001 | 21211 | 0.030875000000000003 | None | 2834 | — |
| baseline_20 | formatter | claude-4.6-sonnet-20260217 | 42028 | 0.05111400000000001 | 68.3 | 3370 | — |
| baseline_20 | seo | claude-4.5-haiku-20251001 | 12923 | 0.022091 | 48.0 | 1629 | — |
| baseline_20 | validator | claude-4.5-haiku-20251001 | 14763 | 0.015158999999999999 | 3.9 | 40 | — |
| baseline_20 | timestamp | claude-4.6-sonnet-20260217 | 15676 | 0.01715 | 13.9 | 182 | — |

## Style violations (seo phase)

| Run | rule_id | pre | post |
|---|---|--:|--:|
| local_cloud_base_6POL0201 | limits.long_description.max | 1 | 1 |
| local_cloud_base_6POL0201 | limits.short_description.max | 1 | 1 |
| baseline_20 | limits.long_description.max | 1 | 1 |
| baseline_20 | limits.short_description.max | 1 | 1 |

### title_changed flags

| Run | title_changed |
|---|---|
| local_cloud_base_6POL0201 | True |
| baseline_20 | True |

## Convergence (seo title)

### Post-normalization (normalized title)

- **1 of 2 runs byte-identical.**
- 2 distinct value(s):
  - `Wisconsin Dem primary turns negative: Barnes vs. Rodriguez` (1x)
  - `Wisconsin Democratic primary turns negative ahead of august vote` (1x)

### Pre-normalization (raw title) -- for delta visibility

- **1 of 2 runs byte-identical.**
- 2 distinct value(s):
  - `Wisconsin Dem Primary Turns Negative: Barnes vs. Rodriguez` (1x)
  - `Wisconsin Democratic primary turns negative ahead of August vote` (1x)

## Delta vs baseline (`baseline_20`)

| Run | Phase | Δ tokens | Δ cost | Δ duration (wall s) |
|---|---|--:|--:|--:|
| local_cloud_base_6POL0201 | analyst | +10.1% | +20.2% | — |
| local_cloud_base_6POL0201 | formatter | -25.7% | -20.3% | +81.4% |
| local_cloud_base_6POL0201 | seo | +17.3% | +7.1% | +1.0% |
| local_cloud_base_6POL0201 | validator | +13.9% | +15.1% | +17.9% |
| local_cloud_base_6POL0201 | timestamp | -1.6% | -3.3% | -18.7% |

## Fidelity assessment (written)

**Inputs.** `local_cloud_base_6POL0201` (2026-07-10, this stage-0 corpus) vs
`baseline_20` (production job 20, completed 2026-07-02, same `6POL0201.srt`
transcript). `baseline_20` predates `--style-report` entirely (no
`metrics.json` was ever written for it) — the row above is reconstructed
from `OUTPUT/eval/baseline_20/manifest.json` + its phase-output `.md` files;
`wall_s` for non-analyst phases is back-computed from successive
`completed_at` deltas in the manifest (includes DB/queue overhead, not a
clean per-phase clock — treat it as directional, not precise). The `style.seo`
block for `baseline_20` is **not** something job 20 ever recorded either — it's
recomputed post-hoc here by running today's deterministic style engine
(`config/house_style.yaml`, current limits) against the static
`seo_output.md` + `analyst_output.md` job-20 artifacts, so both rows in the
style table above are measured on identical, current-day rules. See
`metrics_baseline_20.json` in this directory for the full reconstruction.

**Model-mix confound: none found.** `baseline_20`'s manifest shows the exact
same model pair the new run used —
`anthropic/claude-4.5-haiku-20251001` (analyst/seo/validator) and
`anthropic/claude-4.6-sonnet-20260217` (formatter/timestamp). The backend's
model routing hasn't changed between 2026-07-02 and 2026-07-10. So this is
**not** a stale-vs-current-model comparison — it's an old-prompt-templates
vs current-prompt-templates comparison on the same model family.

**Confound that *is* real: the field-limit YAML changed underneath both.**
`baseline_20`'s own SEO report self-reports "Meets PBS Wisconsin's
60/150/300 character limits" (title/short/long) — those were the
prompt-embedded limits in effect on 2026-07-02. `config/house_style.yaml`
today enforces 80/90/350 (see commit `8de65bd`, "fix 60/160→80/90 limits",
which landed after job 20 ran). Short-description tightened hard
(150→90, a 40% cut); long-description loosened slightly (300→350); title
loosened (60→80). A naive "violations went up" reading would blame the new
prompts for a limit that moved out from under both.

**Controlling for that:** re-running today's style engine against
`baseline_20`'s *unmodified* old output shows it **also** violates
`limits.short_description.max` (139 chars vs. 90) and
`limits.long_description.max` (351 vs. 350) — the identical two rule_ids the
new run trips. Short-description severity is essentially unchanged
(baseline: 139/90, +49 over; new: 140/90, +50 over) — old-prompt output
would have failed today's gate by almost exactly the same margin. So the
short-description violation is not a new-prompt regression at all; it's a
persistent gap between what these models naturally produce for that field
(~130–150 chars) and a limit that was already too tight for old-prompt
output before the new prompts existed. Long-description is the one place
with directional movement: baseline overflows by 1 char (351/350, effectively
a rounding-error miss); the new run overflows by 19 chars (369/350, +5.4%).
Across the full six-run corpus (`comparison.md`), long-description overflows
range 357–468 chars against the same 350 limit — wider than this single
old-prompt data point, but N=1 on the old side means that's suggestive, not
proven; worth a controlled multi-transcript old-vs-new comparison before
calling it a real verbosity regression.

**Output size / structure.** Both `seo_output.md` files share an *identical*
section outline (Optimized Metadata → Keyword Strategy → Tags →
Platform-Specific Recommendations → SEMRush Integration → Accessibility &
Inclusivity → Quality Score → Next Steps) — the seo prompt's report
template itself hasn't been restructured. The new run is simply more
verbose within those same sections: 2,413 words vs. 1,629 words (+48%),
consistent with the long-description length increase above. Formatter
output size is comparable (3,490 vs. 3,370 words, +3.6%).

**Tokens / cost.** Mixed, no dramatic blowup either direction: formatter is
actually *cheaper* on the new prompts (-25.7% tokens, -20.3% cost — likely
the leaner `verbatim_instruction` + house-style-in-`prompt_blocks` path vs.
job 20's older formatter prompt). analyst/seo/validator each ran modestly
*more* expensive (+7% to +20% cost) — consistent with the added
style-guidance context now injected into those phases. Net effect across
all 5 phases is close to a wash.

**Qualitative signal worth flagging:** `baseline_20`'s validator phase
returned a blanket `"overall": "pass"` with **zero** flags on all three
upstream phases — including the seo phase, which (per the recomputation
above) was in fact over both length limits already at the time. The new
run's validator correctly flagged the same seo phase as `"fail"`, citing the
short-description overflow by name. That's the LLM-judgment validator
catching what the old-prompt run's validator missed — a quality
*improvement* in that phase, not a regression, though it's an N=1
observation and the validator's own accuracy against the deterministic
style engine is exactly what the hybrid pipeline work aims to make
unnecessary to rely on.

**Verdict:** no regression from the new prompts on the metrics that matter
most (cost, output structure, model mix). The short/long-description
overflow that shows up in every one of the six new runs is a **pre-existing
condition** — reproducible against 2026-07-02's old-prompt output once
measured under today's rules — not something the new prompts introduced.
This is exactly the target for the hybrid deterministic pipeline: a
persistent LLM-verbosity-vs-hard-limit gap that prompt-only iteration
hasn't closed across two prompt generations and isn't likely to close
without an enforcement/rewrite step.
