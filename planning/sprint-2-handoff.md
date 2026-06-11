# Sprint 2 Drone Handoff — mmingest Indexer Integration

**Dispatched by:** the-conductor
**Date:** 2026-06-04
**Plan:** `/Users/mriechers/.claude/plans/anyway-the-reason-i-noble-whistle.md` (Sprint 2 section)
**Repo:** `mriechers/cardigan`
**Branch:** `sprint-2/mmingest-indexer-integration` off `origin/main` (`afd5fee`)
**Gates merged upstream:**
- Sprint -1 (cheap fix) — `mriechers/cardigan#176` merged `36fc41a`
- Sprint 1A (schema) — `mriechers/cardigan#175` merged `3db4e6c`
- Sprint 1B (crawler core) — `mriechers/cardigan#177` merged `afd5fee`

---

## Your job, in one paragraph

Wire S1B's in-memory `FileWorkItem` + `SidecarResult` pipeline to S1A's `mmingest_files`, `mmingest_sidecars`, and FTS5 schema. Build the orchestrator at `api/services/mmingest/indexer.py` that runs the end-to-end loop: **walk → diff against DB-known state → enqueue → fetch sidecars → upsert into `mmingest_files` → write `mmingest_sidecars` (FTS5 syncs via the triggers from migration 016) → verify parity**. Wire the scheduler stub points S1B left for you. Add an integration test at `tests/integration/test_mmingest_index.py` that exercises the full pipeline end-to-end against an isolated sqlite DB. **You ARE writing to the DB in this sprint — that's the whole point.** Don't reinvent the crawler, parser, or sidecar fetcher; import them.

---

## Start state (already on `origin/main`)

The S1B merge gave you a complete, tested, in-memory pipeline. Read `api/services/mmingest/__init__.py` for the full public API; the highlights are:

```python
from api.services.mmingest import (
    MmingestCrawler,        # async incremental delta walker
    FileWorkItem,           # dataclass: one discovered/changed file
    SidecarFetcher,         # async .srt/.scc GET
    SidecarResult,          # dataclass: one fetched sidecar
    AutoindexParser,        # mod_autoindex HTML → DirEntry[]
    DirEntry,
    parse_filename,         # filename → ParsedFilename | ParseError
    ParsedFilename,
    ParseError,
    select_primary,         # pure REV/variant winner-selection
    KNOWN_VARIANT_VOCAB,    # frozenset (start: {'PLEDGE', 'DS'})
    get_mmingest_scheduler,
    configure_mmingest_jobs,
    start_mmingest_scheduler,
    stop_mmingest_scheduler,
)
```

**Key facts about the S1B API you'll wire to:**

- **`MmingestCrawler.delta_walk(directories=[...], known={url: (etag, last_modified, content_length)}) -> list[FileWorkItem]`** — pass `known` from the DB. S1B's scheduler stub currently passes nothing (returns everything as new); your indexer is the layer that supplies it.
- **`FileWorkItem` mirrors `mmingest_files` columns exactly.** Fields: `url, directory_path, filename, media_id, prefix, prefix_category, show_name, season, episode, hd, revision_date, variant_tag, unknown_tag, file_type, remote_modified_at, file_size_bytes, change_triple, lane`. Straightforward upsert.
- **`SidecarResult`** has `url, filename, kind, ok, body_text, bytes, fetched_at, etag, last_modified, error, status_code, file_id_hint`. Map `url → mmingest_files.id` to populate `mmingest_sidecars.file_id`, then INSERT — the migration 016 triggers handle the FTS5 sync automatically.
- **`select_primary(group: list[FileWorkItem]) -> tuple[primary, variants, superseded]`** — pure function. Pass it a `(media_id, variant_tag)` group; it returns the REV winner, the variants that coexist, and the older REVs that get `superseded_by` pointers. **Use this, don't re-implement REV/variant logic.**

---

## Files to create

| Path | Purpose |
|------|---------|
| `api/services/mmingest/indexer.py` | The orchestrator. Public class `MmingestIndexer`. Methods: `load_known_state() -> dict[str, ChangeTriple]`, `run_once() -> IndexerRun` (returns a result summary), `_upsert_files(items: list[FileWorkItem]) -> dict[str, int]` (URL → file_id), `_apply_variant_lineage(media_id_groups: dict[tuple, list[FileWorkItem]])`, `_persist_sidecars(results: list[SidecarResult], url_to_id: dict[str, int])`, `_verify_parity_after_batch()`. |
| `tests/integration/__init__.py` | Empty, pytest discovery |
| `tests/integration/test_mmingest_index.py` | Integration tests against isolated sqlite DB; uses the `migrated_engine`-style fixture from `tests/api/test_mmingest_parity.py` as a model. |

**Files to modify (surgical only):**

| Path | Change |
|------|--------|
| `api/services/mmingest/scheduler.py` | Replace the placeholder `run_delta_walk()` body so it invokes `MmingestIndexer.run_once()`. Activate the "continuous sidecar enqueue" stub that S1B left labeled `# S2 activates this job` (lines around `logger.debug("mmingest continuous sidecar enqueue: STUB — S2 activates this job")`). Keep `delta_walk_interval_hours` parameter; default still 1. |
| `api/services/mmingest/__init__.py` | Re-export `MmingestIndexer` and `IndexerRun` (whatever return summary type you settle on). Update the `__all__` list. |

**Files to leave alone (do not touch):**

- `api/services/mmingest/crawler.py`, `parsers.py`, `sidecar_fetcher.py` — S1B's work, frozen surface for S2.
- `api/services/mmingest/_db.py` — S1A's parity helper; **call it**, don't modify it.
- `api/services/mmingest/media_id_prefixes.yaml` — vendored copy from Sprint 0; out of scope here. (Issue #182 tracks the long-term sync mechanism.)
- `api/services/ingest_scanner.py` — Sprint 2 does NOT retire this; Sprint 4 or later will. The S-1 batched upsert lives here and must keep working.
- All Alembic migrations.

---

## Critical rules

### 1. Use S1B as a library, don't reimplement.

Anything that smells like "rewriting `parse_filename` because I want a slightly different return shape" or "let me write my own `mod_autoindex` parser" is wrong. Import. Use. If S1B's surface genuinely lacks something you need, surface to Mark — don't fork.

### 2. Honor the variant-selection rule (the S1A schema is ready for it).

**Per the plan and `[[mmingest-variant-selection]]`:**

- Trailing `_REV<YYYYMMDD>` → iterative. Latest REV date wins within `(media_id, variant_tag)`. Older entries' `superseded_by` column points at the winning row's id. `select_primary()` from S1B does the selection; your job is the persistence + the FK update.
- Trailing `_<KNOWN_TAG>` (where TAG is in `KNOWN_VARIANT_VOCAB`, currently `{'PLEDGE', 'DS'}`) → true variant. `variant_tag` set, `superseded_by` NULL until that variant itself gets a newer REV.
- Trailing `_<UNKNOWN_TAG>` → preserved on the row's `unknown_tag` field (S1B's `FileWorkItem.unknown_tag`). Do NOT write this to `variant_tag`; the row's `variant_tag` stays NULL so it's treated as a primary. Log at INFO level when persisting an unknown_tag (per issue #184 — but note that issue is about S1B's parse-time log; if S1B's logging is at DEBUG, your indexer's persistence-time log should be INFO regardless).

**Persistence order matters:**
1. Upsert all `FileWorkItem` rows into `mmingest_files` (one batch). Collect URL → id mapping.
2. Group rows by `(media_id, variant_tag)` (treating None variant_tag as its own group).
3. For each group, call `select_primary()` to get `(primary, variants, superseded)`.
4. For each row in `superseded`, set its `superseded_by` to the `primary` row's id. UPDATE batch.
5. Variants stay with `superseded_by=NULL` (until they themselves get a newer REV in a later run — same algorithm re-runs idempotently).

**Idempotency:** the indexer must be safe to re-run. Re-running with no DB changes should produce zero writes (the change-detection triple from the crawler ensures this). Re-running after a new `_REV` arrives should flip the previous winner's `superseded_by` to the new winner.

### 3. Parity helper after every batch.

After every sidecar persistence batch, call `await fts_parity_delta(conn)` from `api/services/mmingest/_db.py`. If the delta is not 0:

```python
logger.warning(
    "FTS parity delta non-zero after sidecar batch: %d (expected 0). "
    "FTS index may be out of sync with mmingest_sidecars; check trigger health.",
    delta,
)
```

Do NOT raise on a non-zero delta — the indexer should keep running. The WARNING is a signal for Mark / ops to investigate. The `None` return (pre-016 schema) should never happen at this point (016 is merged) but defensively log a single WARNING if it does and move on.

### 4. Use a transaction per upsert batch, not per row.

The S-1 cheap fix established the pattern: `executemany` with batched param dicts, 500 per batch. Mirror it here for `mmingest_files` upsert and `mmingest_sidecars` insert. The 500 / 9-params math from S-1's batch-size comment still applies; SQLite's per-statement param cap is 32766.

### 5. Don't break the S-1 batched upsert.

`api/services/ingest_scanner.py` still runs (Sprint 4 retires it). Its batched upsert into `available_files` must keep working. Run `pytest tests/test_ingest_scanner.py` after your changes and confirm 3/3 of `TestBatchedUpsert` still pass.

### 6. Politeness inherits from S1B's crawler defaults.

You construct `MmingestCrawler` — don't override its rate/concurrency defaults unless you have a reason. If you do override them in the indexer entrypoint, document why and surface to Mark. (Note: issue #183 tracks tuning the default TokenBucket burst to match smoke-test envelope — not your job to fix, but if your indexer overrides `rate_per_second` or `max_concurrent` you'll want to coordinate.)

### 7. The indexer's `run_once()` is the unit of work; the scheduler is the trigger.

`MmingestIndexer.run_once()` does one full pass: walk → diff → upsert → sidecar fetch → persist → parity. It returns a summary dataclass (`IndexerRun`) with counts (`files_seen`, `files_new`, `files_changed`, `sidecars_fetched`, `sidecars_persisted`, `fts_parity_delta`, `errors`). The scheduler calls `await MmingestIndexer().run_once()` on each tick and logs the summary.

### 8. Test against an isolated sqlite DB, not the dev DB.

The fixture pattern from `tests/api/test_mmingest_parity.py::migrated_engine` is the model — shell out to `alembic upgrade head` against a `tempfile.mkstemp` DB. Reuse it as a pytest fixture in `tests/integration/test_mmingest_index.py`. Mock the HTTP layer (`MmingestCrawler._fetch` or whatever HTTP entrypoint you wire) so the test doesn't hit real mmingest.

### 9. Cardigan lint stack (HARD rule, has bitten prior sprints).

CI runs BOTH `black --check` and `ruff check`. Run both locally before pushing:

```bash
black .
ruff check --fix .
black --check .   # confirm clean
ruff check .      # confirm clean
```

The `ingest_scanner.py` file got bitten by this in S-1 review; the migrations got bitten in S1A; do not be the third. The memory at `feedback_cardigan_lint_stack` is explicit about this.

### 10. Don't merge. Surface for Mark's gate.

Open the PR. Run yourself through the verification gates below. Surface in the standard format. Do NOT click merge.

---

## Verification gates (must all pass before opening the PR)

1. **`alembic upgrade head`** on a fresh dev DB → schema tables present (`mmingest_files`, `mmingest_sidecars`, `mmingest_sidecars_fts`, `consumer_keys`). `PRAGMA integrity_check` returns "ok".

2. **The regression-case end-to-end test (from the plan):** Seed `mmingest_sidecars` with the canonical `6POL0101_REV20260319.srt` body text via the full indexer path (NOT a manual INSERT). After the indexer finishes:
   - `SELECT body_text FROM mmingest_sidecars_fts WHERE body_text MATCH 'inside wisconsin politics'` returns the row.
   - BM25 rank is negative (FTS5 convention: more negative = higher relevance).
   - The corresponding `mmingest_files` row has `media_id='6POL0101'`, `revision_date='2026-03-19'`, `variant_tag=NULL`, `show_name='Inside Wisconsin Politics'`.

3. **`fts_parity_delta()` returns 0** after the seed insert. The parity-after-batch helper is wired and exercised.

4. **Variant lineage test:** Seed two files with the same `media_id='6POL0101'` and different `_REV` dates (e.g. `_REV20260101` and `_REV20260319`). After indexer runs, the older one has `superseded_by` set to the newer one's id. The newer one has `superseded_by=NULL`. Both rows persist (no deletes).

5. **Variant coexistence test:** Seed `6POL0101.srt` (primary) and `6POL0101_PLEDGE.srt` (variant). After indexer runs, both rows exist; primary has `variant_tag=NULL, superseded_by=NULL`; variant has `variant_tag='PLEDGE', superseded_by=NULL`. They are NOT linked via `superseded_by`.

6. **Unknown-tag preservation test:** Seed `6POL0101_NOVELTAG.srt`. After indexer runs, the row has `variant_tag=NULL` (NOT 'NOVELTAG'); the unknown tag is preserved on the row's `unknown_tag` field (which is a transient field on `FileWorkItem` — confirm how you decided to persist or log it; per the plan, "log to ops table for vocabulary growth", but no ops table exists yet, so structured INFO log is acceptable; document your choice in the PR description).

7. **Idempotency test:** Run the indexer twice with no DB changes. Second run produces zero INSERT/UPDATE writes (use SQL query counters or a mock-session call-count assertion).

8. **No regression on S-1's batched upsert:** `pytest tests/test_ingest_scanner.py::TestBatchedUpsert` — 3/3 pass.

9. **No regression on S1A's parity tests:** `pytest tests/api/test_mmingest_parity.py` — 11/11 pass.

10. **No regression on S1B's crawler/parser tests:** `pytest tests/services/mmingest/` — all green.

11. **Full integration test suite:** `pytest tests/integration/` — all your new tests green.

12. **Lint stack:** `black --check .` and `ruff check .` both clean on the whole project.

13. **Live smoke test (manual, document in PR):** Point the indexer at one mmingest directory at depth 1 (e.g. `/Programs/InsideWisconsinPolitics/`). Run one pass. Confirm:
    - The `mmingest_files` table gets populated with N rows where N matches the directory's file count.
    - At least one sidecar gets persisted to `mmingest_sidecars`.
    - The FTS5 table picks up the sidecar(s) via the migration 016 triggers.
    - `fts_parity_delta()` returns 0 after the pass.
    - Include the row count, elapsed wall-clock time, and request count in the PR description.

---

## What to do when you finish

1. Open the PR against `mriechers/cardigan` `main` from branch `sprint-2/mmingest-indexer-integration`.
2. PR title: `feat(mmingest): Sprint 2 — indexer integration (crawler → DB → FTS5)`.
3. PR body must include:
   - Summary linking back to the plan and this handoff doc.
   - Verification gates as a checklist (test that each passes before submitting).
   - The live smoke-test result (row counts, elapsed time, request count, any errors observed).
   - A "Scope guard" section confirming you did NOT touch the S-1 ingest_scanner, S1A migrations, or S1B crawler/parser/fetcher modules (beyond the documented surgical edits to `scheduler.py` and `__init__.py`).
   - Notes on any decisions you made about unknown-tag persistence (no ops table exists yet — your choice gets recorded for the next sprint to inherit).
4. Mark will not merge until a separate `code-reviewer` (or `pr-review-toolkit:review-pr`) agent has run a full pass and posted findings.

---

## Commit attribution

```
feat(mmingest): <subject>

[Agent: the-drone]

<body>

Agent: the-drone
Machine: <hostname>

Co-Authored-By: Claude <noreply@anthropic.com>
```

No emojis in commit messages. (Per workspace convention; an Auto Mode reminder noted "the assistant MUST avoid using emojis" — extends to commit subjects/bodies.)

---

## Reference docs (read before starting)

- `/Users/mriechers/.claude/plans/anyway-the-reason-i-noble-whistle.md` — full plan, Sprint 2 section especially
- `mriechers/cardigan` branch `main` at `afd5fee` — your start state (S-1 + S1A + S1B all merged)
- `api/services/mmingest/__init__.py` — public API to import
- `api/services/mmingest/crawler.py` — `MmingestCrawler.delta_walk` signature + `FileWorkItem` shape
- `api/services/mmingest/sidecar_fetcher.py` — `SidecarFetcher.fetch` signature + `SidecarResult` shape
- `api/services/mmingest/parsers.py` — `select_primary` signature (use this for REV/variant logic)
- `api/services/mmingest/scheduler.py` — the stub points S1B left labeled `# S2 activates this job` and `# S2 supplies ``known`` from the database`
- `api/services/mmingest/_db.py` — `fts_parity_delta(conn) -> int | None` (call after every batch)
- `alembic/versions/015_add_mmingest_files_table.py` — the schema you're targeting (`variant_tag`, `superseded_by`, full column list)
- `alembic/versions/016_add_mmingest_sidecars_and_fts.py` — sidecar + FTS5 triggers + the JOIN read-shape docstring (the triggers fire automatically — your INSERTs to `mmingest_sidecars` propagate to FTS5 without explicit code)
- `tests/api/test_mmingest_parity.py` — `migrated_engine` fixture pattern to model your integration test fixture on
- `tests/services/mmingest/test_crawler.py` and `test_parsers.py` — S1B test patterns, useful for mocking the HTTP layer
- `api/services/ingest_scanner.py::_track_files_batch` — the S-1 batched upsert pattern (500 rows / executemany) — mirror this for the new tables

---

## Active issues you should be aware of (not your job to fix)

- **#182** — parser sync between cardigan-v4 and pbswi Sprint 0 skill. Until this lands, the parser lives in two places (S1B vendored a copy with a provenance header). Your indexer imports from `api/services/mmingest/parsers.py`; don't touch the upstream.
- **#183** — TokenBucket burst tuning. Production defaults may not match smoke-test burst=1 yet. If your indexer instantiates `MmingestCrawler` with explicit `burst=1` you're being safer than the default; document the choice.
- **#184** — unknown-variant-tag log level (DEBUG → INFO). At the parse-time log site. Your indexer's persistence-time logging of unknown_tag should be INFO regardless (per rule 2 above).

---

## If you get stuck

- **Variant-lineage UPDATE pattern unclear** — the order is upsert all → group → select_primary → UPDATE superseded. If you're tempted to do it inline with the INSERT, stop and re-read rule 2.
- **FTS5 not picking up your inserts** — the migration 016 triggers fire on `mmingest_sidecars` INSERT/UPDATE/DELETE. If they don't fire, check that you're committing the transaction (the triggers run within the transaction; without commit, the FTS state is invisible to other connections including the parity-helper read). The S1A test `test_parity_delta_zero_after_insert` is the proof that the triggers work end-to-end.
- **Schema mismatch with FileWorkItem** — they're designed to match exactly (per S1B's docstring on FileWorkItem: "Fields mirror `mmingest_files` columns (migration 015) so the indexer can do a straightforward upsert without re-parsing"). If you find a mismatch, surface to Mark — don't paper over it.
- **Real blocker** — STOP and report. A broken Sprint 2 wedges S3A + S3B (both depend on S2 data).

Good hunting. — the-conductor
