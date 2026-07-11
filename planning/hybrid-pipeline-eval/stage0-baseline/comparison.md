# eval_compare report

- Runs: local_cloud_base_6POL0201, local_cloud_base_6POL0202, local_cloud_base_6POL0114C, local_cloud_base_2WLIJD, local_cloud_base_6HNPGD3, local_cloud_base_TLBCORN
- Baseline: `local_cloud_base_6POL0201`

## Per-phase metrics

| Run | Phase | Model | Total tokens | Cost | Wall s | Output words | coverage_ratio |
|---|---|---|--:|--:|--:|--:|--:|
| local_cloud_base_6POL0201 | analyst | claude-4.5-haiku-20251001 | 23363 | 0.037115 | 68.0 | 4128 | — |
| local_cloud_base_6POL0201 | formatter | claude-4.6-sonnet-20260217 | 31219 | 0.040741 | 123.9 | 3490 | 1.01 |
| local_cloud_base_6POL0201 | seo | claude-4.5-haiku-20251001 | 15157 | 0.023653 | 48.5 | 2413 | — |
| local_cloud_base_6POL0201 | validator | claude-4.5-haiku-20251001 | 16814 | 0.017444 | 4.6 | 157 | — |
| local_cloud_base_6POL0201 | timestamp | claude-4.6-sonnet-20260217 | 15422 | 0.016586 | 11.3 | 277 | — |
| local_cloud_base_6POL0202 | analyst | claude-4.5-haiku-20251001 | 22136 | 0.03092 | 47.2 | 2601 | — |
| local_cloud_base_6POL0202 | formatter | claude-4.6-sonnet-20260217 | 30490 | 0.041008 | 140.2 | 3997 | 0.992 |
| local_cloud_base_6POL0202 | seo | claude-4.5-haiku-20251001 | 12967 | 0.022093 | 48.3 | 2598 | — |
| local_cloud_base_6POL0202 | validator | claude-4.5-haiku-20251001 | 15102 | 0.01565 | 4.1 | 134 | — |
| local_cloud_base_6POL0202 | timestamp | claude-4.6-sonnet-20260217 | 16918 | 0.018268 | 12.6 | 315 | — |
| local_cloud_base_6POL0114C | analyst | claude-4.5-haiku-20251001 | 34558 | 0.044378 | 50.1 | 2849 | — |
| local_cloud_base_6POL0114C | formatter | claude-4.6-sonnet-20260217 | 42824 | 0.053166 | 136.3 | 3614 | 1.09 |
| local_cloud_base_6POL0114C | seo | claude-4.5-haiku-20251001 | 13338 | 0.022024 | 44.6 | 2528 | — |
| local_cloud_base_6POL0114C | validator | claude-4.5-haiku-20251001 | 15331 | 0.015917 | 4.6 | 137 | — |
| local_cloud_base_6POL0114C | timestamp | claude-4.6-sonnet-20260217 | 29092 | 0.031072 | 21.5 | 468 | — |
| local_cloud_base_2WLIJD | analyst | claude-4.5-haiku-20251001 | 8318 | 0.014428 | 30.5 | 1803 | — |
| local_cloud_base_2WLIJD | formatter | claude-4.6-sonnet-20260217 | 11965 | 0.013068999999999999 | 9.1 | 349 | 1.0 |
| local_cloud_base_2WLIJD | seo | claude-4.5-haiku-20251001 | 10919 | 0.018603 | 38.7 | 2184 | — |
| local_cloud_base_2WLIJD | validator | claude-4.5-haiku-20251001 | 8534 | 0.009476 | 6.3 | 246 | — |
| local_cloud_base_6HNPGD3 | analyst | claude-4.5-haiku-20251001 | 9396 | 0.016738 | 34.6 | 2121 | — |
| local_cloud_base_6HNPGD3 | formatter | claude-4.6-sonnet-20260217 | 13135 | 0.014423 | 9.9 | 461 | 1.0 |
| local_cloud_base_6HNPGD3 | seo | claude-4.5-haiku-20251001 | 11135 | 0.018099 | 34.6 | 1943 | — |
| local_cloud_base_6HNPGD3 | validator | claude-4.5-haiku-20251001 | 8797 | 0.009569 | 5.1 | 206 | — |
| local_cloud_base_TLBCORN | analyst | claude-4.5-haiku-20251001 | 6418 | 0.009923999999999999 | 18.5 | 991 | — |
| local_cloud_base_TLBCORN | formatter | claude-4.6-sonnet-20260217 | 9819 | 0.010515 | 8.3 | 212 | 1.0 |
| local_cloud_base_TLBCORN | seo | claude-4.5-haiku-20251001 | 8225 | 0.013387 | 27.4 | 1406 | — |
| local_cloud_base_TLBCORN | validator | claude-4.5-haiku-20251001 | 5478 | 0.005842 | 2.6 | 77 | — |

## Style violations (seo phase)

| Run | rule_id | pre | post |
|---|---|--:|--:|
| local_cloud_base_6POL0201 | limits.long_description.max | 1 | 1 |
| local_cloud_base_6POL0201 | limits.short_description.max | 1 | 1 |
| local_cloud_base_6POL0202 | limits.long_description.max | 1 | 1 |
| local_cloud_base_6POL0202 | limits.short_description.max | 1 | 1 |
| local_cloud_base_6POL0114C | limits.long_description.max | 1 | 1 |
| local_cloud_base_6POL0114C | limits.short_description.max | 1 | 1 |
| local_cloud_base_2WLIJD | limits.long_description.max | 1 | 1 |
| local_cloud_base_2WLIJD | limits.short_description.max | 1 | 1 |
| local_cloud_base_6HNPGD3 | limits.long_description.max | 1 | 1 |
| local_cloud_base_6HNPGD3 | limits.short_description.max | 1 | 1 |
| local_cloud_base_TLBCORN | limits.short_description.max | 1 | 1 |

### title_changed flags

| Run | title_changed |
|---|---|
| local_cloud_base_6POL0201 | True |
| local_cloud_base_6POL0202 | False |
| local_cloud_base_6POL0114C | True |
| local_cloud_base_2WLIJD | True |
| local_cloud_base_6HNPGD3 | True |
| local_cloud_base_TLBCORN | False |

## Convergence (seo title)

### Post-normalization (normalized title)

- **1 of 6 runs byte-identical.**
- 6 distinct value(s):
  - `Jingle dress designer Aerius Benton-Banai on healing through ojibwe art` (1x)
  - `Menominee ancestors farmed Wisconsin at massive scale` (1x)
  - `Protecting Wisconsin's burial mounds: the ho-chunk nation's fight` (1x)
  - `Wisconsin Democratic primary splits: establishment vs. progressive camps` (1x)
  - `Wisconsin Democratic primary turns negative ahead of august vote` (1x)
  - `Wisconsin Democratic primary: seven candidates compete at state convention` (1x)

### Pre-normalization (raw title) -- for delta visibility

- **1 of 6 runs byte-identical.**
- 6 distinct value(s):
  - `Jingle dress designer Aerius Benton-Banai on healing through Ojibwe art` (1x)
  - `Menominee ancestors farmed Wisconsin at massive scale` (1x)
  - `Protecting Wisconsin's burial mounds: the Ho-Chunk Nation's fight` (1x)
  - `Wisconsin Democratic primary splits: establishment vs. progressive camps` (1x)
  - `Wisconsin Democratic primary turns negative ahead of August vote` (1x)
  - `Wisconsin Democratic primary: Seven candidates compete at state convention` (1x)

## Delta vs baseline (`local_cloud_base_6POL0201`)

| Run | Phase | Δ tokens | Δ cost | Δ duration (wall s) |
|---|---|--:|--:|--:|
| local_cloud_base_6POL0202 | analyst | -5.3% | -16.7% | -30.6% |
| local_cloud_base_6POL0202 | formatter | -2.3% | +0.7% | +13.2% |
| local_cloud_base_6POL0202 | seo | -14.4% | -6.6% | -0.4% |
| local_cloud_base_6POL0202 | validator | -10.2% | -10.3% | -10.9% |
| local_cloud_base_6POL0202 | timestamp | +9.7% | +10.1% | +11.5% |
| local_cloud_base_6POL0114C | analyst | +47.9% | +19.6% | -26.3% |
| local_cloud_base_6POL0114C | formatter | +37.2% | +30.5% | +10.0% |
| local_cloud_base_6POL0114C | seo | -12.0% | -6.9% | -8.0% |
| local_cloud_base_6POL0114C | validator | -8.8% | -8.8% | +0.0% |
| local_cloud_base_6POL0114C | timestamp | +88.6% | +87.3% | +90.3% |
| local_cloud_base_2WLIJD | analyst | -64.4% | -61.1% | -55.1% |
| local_cloud_base_2WLIJD | formatter | -61.7% | -67.9% | -92.7% |
| local_cloud_base_2WLIJD | seo | -28.0% | -21.4% | -20.2% |
| local_cloud_base_2WLIJD | validator | -49.2% | -45.7% | +37.0% |
| local_cloud_base_6HNPGD3 | analyst | -59.8% | -54.9% | -49.1% |
| local_cloud_base_6HNPGD3 | formatter | -57.9% | -64.6% | -92.0% |
| local_cloud_base_6HNPGD3 | seo | -26.5% | -23.5% | -28.7% |
| local_cloud_base_6HNPGD3 | validator | -47.7% | -45.1% | +10.9% |
| local_cloud_base_TLBCORN | analyst | -72.5% | -73.3% | -72.8% |
| local_cloud_base_TLBCORN | formatter | -68.5% | -74.2% | -93.3% |
| local_cloud_base_TLBCORN | seo | -45.7% | -43.4% | -43.5% |
| local_cloud_base_TLBCORN | validator | -67.4% | -66.5% | -43.5% |
