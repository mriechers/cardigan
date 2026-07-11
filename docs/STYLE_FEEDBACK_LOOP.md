# Style Feedback Loop (v1)

How house-style rule quality gets measured and improved over time, and the
one invariant that governs the whole loop:

> **This loop NEVER edits `config/house_style.yaml` automatically.** No
> script, cron job, or agent writes that file. `scripts/style_report.py`
> only reads events and renders PROPOSED diffs in a markdown report — a
> human copies an approved proposal into a hand-authored pull request.
> There is no code path, today or planned, that commits a rule change
> without that PR.

## Signal sources

Three kinds of evidence feed the report, all persisted to the `session_stats`
table (`event_type` column) via `api.services.database.log_event`:

1. **`style_violation` (post-stage).** Emitted by
   `api/services/worker.py:_apply_style_post`, one event per
   `RuleViolation` the deterministic post-generation stage
   (`api.services.style_engine.post_stage.run_post_stage`) surfaces for a
   phase's raw model output. Payload: `data.extra` = the violation's
   `to_dict()` (`rule_id`, `phase`, `severity`, `message`, `field`,
   `model_fixable`) plus `mode` (`shadow`|`enforce`, the style-engine's
   configured mode for that phase) and `action` (`"flagged"` in enforce
   mode, `"shadow"` in shadow mode — post-stage violations are flag-tier by
   construction, so they're never auto-fixed regardless of mode).
2. **`style_violation` (lint).** Emitted by
   `api/services/worker.py:_apply_style_lint`, one event per violation the
   deterministic validator lint suite (`api.services.style_engine.lint.
   run_lint`) finds when merging into the QA verdict. Same `rule_id`/
   `phase`/`severity` shape, plus `source: "lint"` and `mode`
   (`shadow`|`enforce`) — no `action` key.
3. **`editor_correction`.** Emitted by
   `mcp_server/server.py:_log_editor_corrections`, once per writable field
   after a successful `commit_sst_edits` MCP write. Payload: `field`,
   `media_id`, `committed_value` (what the editor actually wrote to
   Airtable), `pipeline_value` (the SEO phase's original recommendation for
   that field, recovered from `seo_output.md` when the field is
   `title`/`short_description`/`long_description` — genuinely
   unrecoverable for `keywords`/social fields in v1, so `pipeline_value` is
   `None` for those), and `original_value` (the pre-edit Airtable
   snapshot).

A fourth, complementary source lives **outside** `session_stats`: the eval
harness (`scripts/eval_pipeline.py --style-report`, compared across runs by
`scripts/eval_compare.py`) captures the same kind of pre/post violation and
title-convergence data from offline eval runs against known transcripts —
useful for validating a rule change (see "Before a release tag" below)
even though it isn't wired into `style_report.py`'s DB read today.

## Cadence

**Manual, weekly to start.** No cron job, no CI trigger, no scheduled
agent in v1 — an editor or engineer runs the command below by hand, on
whatever cadence surfaces enough new production traffic to be worth
reviewing (weekly is the starting assumption, not a hard rule).

## Command line

```bash
# From the repo root, with the venv active:
python -m scripts.style_report

# Common flags:
python -m scripts.style_report \
  --db dashboard.db \
  --since 2026-07-01 --until 2026-07-10 \
  --app-version v4.2 \
  --out OUTPUT/reports/style_report_2026-07-10.md
```

| Flag | Default | Purpose |
|---|---|---|
| `--db` | `$DATABASE_PATH` or `dashboard.db` | SQLite file to read `session_stats` from |
| `--since` / `--until` | unset (all time) | `YYYY-MM-DD` window bounds, inclusive |
| `--app-version` | unset (all versions) | Filter to one cost-epoch tag (e.g. `v4.2`) |
| `--rules-file` | `config/house_style.yaml` | Rules file to check zero-hit candidates and render proposals against |
| `--out` | unset (stdout) | Write the markdown report here instead of printing it |

Reports go in **`OUTPUT/reports/`** — that directory is already covered by
the repo's `OUTPUT/` gitignore rule, so reports never get committed
accidentally. Point `--db` at a copy of the *production* database (e.g. a
`scripts/snapshot_db.sh` snapshot pulled from the homelab box) to review
real editorial signal; running against a fresh/empty local `dashboard.db`
is also valid and simply produces a "0 events" report — that's the
smoke-test path, not an error.

## What the report contains

1. **Violations summary** — counts by `rule_id` × `phase`, split into
   `enforce`/`flagged`/`shadow` columns (see `classify_action`'s docstring
   in `scripts/style_report.py` for the exact mode→bucket mapping — lint's
   `mode: enforce` maps to the `enforce` column, post-stage's `mode:
   enforce` maps to `flagged`, since post-stage violations are never
   auto-fixed), sub-broken by model and `app_version` when present in the
   payload.
2. **Correction patterns** — word-level diff clustering (`difflib`) over
   `editor_correction` events with a recoverable `pipeline_value`, grouping
   recurring replacements (e.g. `explores` → `examines` ×7) with example
   provenance. Corrections with no recoverable `pipeline_value` are listed
   separately, by field, count-only.
3. **Zero-hit rules** — enforce/flag-tier `config/house_style.yaml` entries
   that never appeared in any violation event in the window: retirement or
   review candidates. **Caveat baked into the report itself:** enforce-tier
   substitutions apply as silent `AppliedFix`es and are *never* logged as
   `style_violation` events at all (only flag-tier `RuleViolation`s are
   logged) — so every enforce-tier substitution will always show zero hits
   here. That's a structural gap in what `session_stats` can observe, not
   evidence the substitution never fires; don't retire an enforce-tier rule
   on this signal alone. Forbidden-phrase entries sharing a `category` also
   collapse onto one event `rule_id`, so the zero-hit table can only tell
   you a *category* had no hits, not which specific phrase within it never
   fired.
4. **Proposed YAML edits** — heuristic, evidence-gated only: a correction
   cluster needs **≥ 3 occurrences** before it's proposed at all. Two
   proposal shapes: an `old`/`new` pair differing only by case becomes a
   `casing.casing_variants` addition; everything else becomes a
   `voice.forbidden_phrases` addition flagging the *old* wording for
   editorial review (never a blind auto-substitution — wording swaps need
   judgment). Every proposal is a fenced YAML snippet plus the evidence
   count and example provenance, wrapped in the same
   never-auto-applied banner that opens the report.

## Review path

1. An editor or engineer runs `python -m scripts.style_report` (see
   command line above) and reads the report.
2. Editor + engineer review it together. Zero-hit entries and proposed
   edits are candidates, not verdicts — editorial judgment decides what
   actually changes.
3. Approved items become a **pull request that edits
   `config/house_style.yaml` ONLY** (never anything under `api/`,
   `mcp_server/`, or `scripts/style_report.py` itself as part of the same
   change — a rule-data PR should be reviewable as pure data).
4. CI on that PR must pass:
   - **YAML load test** — `tests/test_house_style_config.py`
     (`test_real_yaml_loads_without_error`,
     `test_real_yaml_does_not_raise_style_rules_error`, plus the drift
     checks in the same file that keep prompt-block numbers and
     `WRITABLE_FIELDS` limits reconciled with the YAML).
   - **style_engine suite** — the full `tests/test_style_*.py` family
     (scanner/limits, substitutions, casing/entities, lint, phase_io,
     qa_merge, rules, timecodes).
   - **Rendered-prompt snapshot diffs** — `tests/test_style_prompt_blocks.py`
     (`test_*_prompt_renders_against_real_config_for_both_profiles` and the
     full/slim block-content assertions), which fail loudly if a rule
     change silently changes what a prompt renders.
5. Merge. The rule change ships in the next release.

## Before a release tag

A merged `house_style.yaml` PR is a data change, not a code change, but it
still shifts pipeline behavior — validate it the same way any other
pipeline change is validated before cutting a tag: run
`scripts/eval_pipeline.py` on both the pre- and post-change rules (or
before/after commits) against the same known transcripts, then
`scripts/eval_compare.py` the two run directories to check the style
violation counts and title-convergence sections didn't regress. This
mirrors the epoch-tagging discipline in `docs/COST_DATA_VERSIONING.md` —
don't let a rule change land in a tagged release without an A/B check.
