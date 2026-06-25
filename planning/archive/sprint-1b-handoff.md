# Sprint 1B Drone Handoff — mmingest Crawler Core

**Dispatched by:** the-conductor
**Date:** 2026-06-04
**Plan:** `/Users/mriechers/.claude/plans/anyway-the-reason-i-noble-whistle.md` (Sprint 1B section)
**Repo:** `mriechers/cardigan`
**Branch:** `sprint-1b/crawler-refactor` (off post-merge `main` — Sprint -1 + Sprint 1A both merged)
**Gates merged upstream:**
- Sprint -1 (cheap fix) — `mriechers/cardigan#176` merged `36fc41a`
- Sprint 1A (schema) — `mriechers/cardigan#175` merged `3db4e6c`

---

## Your job, in one paragraph

Build the **incremental, polite directory crawler** for `mmingest.pbswi.wisc.edu` under `api/services/mmingest/`. The crawler walks Apache `mod_autoindex` HTML, classifies discovered files, parses filenames into structured records via the Sprint 0 Media ID parser, applies the variant-selection rule, and emits in-memory `WorkItem` records on a two-lane priority queue (sidecars first, MP4 metadata second). **You do NOT write to the database in this sprint** — Sprint 2 (next) wires this orchestrator to the new `mmingest_files` + `mmingest_sidecars` tables. Resist any urge to add `op.execute()`, session.commit()`, or schema lookups. Pure in-memory pipeline + tests.

---

## Files to create

| Path | Purpose |
|------|---------|
| `api/services/mmingest/crawler.py` | Async incremental directory walker over `mod_autoindex` HTML, bounded queue, token-bucket rate limiter, change-detection via `(etag, last-modified, content-length)` triple |
| `api/services/mmingest/sidecar_fetcher.py` | Polite GET for `.srt`/`.scc` files, decode + return text; **no DB writes**, just bytes/text outputs |
| `api/services/mmingest/scheduler.py` | APScheduler wiring: directory delta-walk every 1h (cheap HEAD-driven), sidecar+MP4 enqueue continuous |
| `api/services/mmingest/parsers.py` | `mod_autoindex` HTML parser + filename parser. Filename parser MUST import + reuse the Sprint 0 parser at `pbswi/.claude/skills/reference/media-id/parse_media_id.py` (don't re-implement). |
| `api/services/mmingest/__init__.py` | Already exists — re-export the new public API (`Crawler`, `SidecarFetcher`, `Scheduler`, `WorkItem`) |
| `tests/services/mmingest/test_crawler.py` | Crawler tests: politeness assertions (concurrency cap ≤ 4, rate-limit holds), change-detection skips unchanged files, two-lane priority ordering verified |
| `tests/services/mmingest/test_parsers.py` | Parser tests: `mod_autoindex` HTML → file records (using snapshot fixture), filename → structured record, **variant rule** (REV vs known tag vs unknown tag) |
| `tests/services/mmingest/fixtures/autoindex_snapshot.html` | A real snapshot of an `mmingest.pbswi.wisc.edu` directory listing. Don't fabricate this — capture a real response and commit it. |
| `tests/services/mmingest/__init__.py` | Empty, just for pytest discovery |

**Already in place — DO NOT recreate:**
- `api/services/mmingest/_db.py` — Sprint 1A's FTS parity helper. Leave alone.
- `api/services/mmingest/__init__.py` — Sprint 1A docstring stub. Add re-exports, don't replace.

---

## Critical rules

### 1. NO DATABASE WRITES IN THIS SPRINT.

The plan is explicit: Sprint 1B's deliverable is **in-memory work items**. The crawler emits `WorkItem(url, kind, etag, last_modified, ...)` records. The scheduler holds queues. **Nothing touches `mmingest_files` or `mmingest_sidecars` yet.** If you find yourself writing `session.execute(INSERT INTO mmingest_...)`, you're out of scope.

Sprint 2 will wire the crawler outputs to the Sprint 1A schema. Keep the seam clean.

### 2. ffprobe metadata is Phase 2, NOT Phase 1.

The crawler can HEAD files for cheap headers (`ETag`, `Last-Modified`, `Content-Length`, `Content-Type`). It must NOT range-probe MP4s for ffprobe metadata. Anyone tempted to add `subprocess.run(["ffprobe", ...])` or `aiohttp` byte-range MOV/MP4 parsing is overscoping. Files that need probing land with `probe_status='deferred'` (the Sprint 1A column on `available_files`); the actual probing service comes in Phase 2.

### 3. `mod_autoindex` parser MUST drive off a snapshot fixture.

Apache's directory listing HTML format is fragile across versions and config. **Capture a real response from `mmingest.pbswi.wisc.edu`** (with credentials if needed — ask Mark) and save it to `tests/services/mmingest/fixtures/autoindex_snapshot.html`. The parser test runs against this fixture so an Apache config change can't silently break the walk.

Prefer `?F=0` plain listing if Apache serves it (less HTML chrome to parse). Test both with-and-without `?F=0` if both work on mmingest.

### 4. Two-lane priority queue.

The plan calls this out:
> **Two priority lanes:** sidecar work first (cheap, search depends on it), MP4 metadata work second. Sidecar-first means search stays current even when an MP4 batch backs up.

Implement the queue with two priorities. `.srt` and `.scc` files are lane 1 (high priority). `.mp4` is lane 2. Images and other files are lane 2 or lower. Lane 1 drains first; lane 2 only runs when lane 1 is empty (or when a configurable interleave kicks in — keep that simple).

### 5. Politeness defaults.

- Max in-flight requests: **4** (configurable via constant)
- Token-bucket rate limit (configurable — start with something conservative like 2 req/sec)
- Exponential backoff on 5xx and timeouts (start at 1s, cap at 60s)
- Configurable "quiet window" (e.g. don't crawl 6am–10am during broadcast traffic hours). Start with a class constant; Sprint 2 may wire this to a config table row.
- Change detection: only fetch a file's body if `(etag, last-modified, content-length)` differs from what we'd previously seen. **For Sprint 1B you don't yet have DB state to diff against**, so accept a `prior_state` argument to the crawler entrypoint and use it for the comparison. Sprint 2 will plumb DB state into that argument.

### 6. Variant-selection rule (parser-side, per `[[mmingest-variant-selection]]`).

The filename parser MUST distinguish:

- Trailing `_REV<YYYYMMDD>` (e.g. `6POL0101_REV20260319.srt`) → **iterative**. Strip from filename, record `revision_date='2026-03-19'`. Latest REV date within a `(media_id, variant_tag)` group is the "winner"; older REVs are superseded.
- Trailing `_<UPPERCASE_TAG>` where TAG is in the known vocabulary (start: `PLEDGE`, `DS`) → **true variant**. Set `variant_tag='PLEDGE'` or `variant_tag='DS'`. Coexists with the primary; do NOT mark as superseded.
- Trailing `_<UPPERCASE_TAG>` where TAG is NOT in the known vocabulary → **unknown**. Log to a structured ops record (e.g. add an entry to an in-memory `unknown_variants: list[dict]` field on the crawler) so Mark can grow the vocabulary. Do NOT silently collapse it into the primary or invent semantics.

The **superseded_by** column on `mmingest_files` is Sprint 2's problem — the parser just emits the structured fields. Sprint 2 will do the lookup-and-link.

The known variant vocabulary should live as a class constant (or `__init__.py` export) so future PRs can grow it without touching the parser logic.

### 7. Use the Sprint 0 Media ID parser, don't re-implement.

The canonical parser is at `pbswi/.claude/skills/reference/media-id/parse_media_id.py` (relative to workspace root — Cardigan is a submodule of `pbswi`, so adjust your import path). Import it. Use it. Don't re-implement the prefix lookup or filename grammar.

If the Sprint 0 parser is missing something Sprint 1B needs (e.g. it doesn't currently strip `_REV` / `_PLEDGE` / `_DS` suffixes), **add to the Sprint 0 parser via a separate PR against `public-media-work/pbswi`** — don't fork it inside Cardigan. Surface that to Mark before doing it.

### 8. Asterisk semantics are STILL OPEN.

The Sprint 0 YAML preserves a `*` on a subset of Broadcast prefixes (`2CSS*`, `2CSQ*`, `2KGG*`, etc.). The semantics of the asterisk are not yet defined. **Do not invent meaning.** If your parser encounters a `*` while resolving a prefix, log it as `prefix_has_asterisk=True` on the structured record and pass through. Sprint 2 or later will handle it once Mark clarifies.

---

## Verification gates (must pass before opening the PR)

1. **`pytest tests/services/mmingest/` — green.** All tests pass.
2. **`mod_autoindex` parser test runs against the committed snapshot fixture.** Don't ship a parser without a fixture-grounded test.
3. **Politeness assertions in `test_crawler.py`:**
   - Max in-flight requests ≤ 4 during a simulated burst
   - Token-bucket rate limiter holds the average request rate under the configured cap
4. **Two-priority lane test:** Given a mixed queue of sidecars and MP4s, sidecars drain first.
5. **Variant rule tests:**
   - `6POL0101_REV20260319.srt` parses with `revision_date='2026-03-19'`, no `variant_tag`
   - `2WLI0501_PLEDGE.mp4` parses with `variant_tag='PLEDGE'`
   - `2WLI0501_DS.mp4` parses with `variant_tag='DS'`
   - `2WLI0501_UNKNOWNTHING.mp4` parses to a record AND emits an entry to the `unknown_variants` ops log
6. **Live smoke test (manual, document the result in PR description):** Point the crawler at one mmingest directory at depth 1 (e.g. `/Programs/InsideWisconsinPolitics/`) and confirm it walks without errors. Don't run a full scan — just one directory, one pass. Include the request count and elapsed time in the PR description.
7. **Lint stack clean:** Cardigan CI runs BOTH `ruff check` and `black --check`. Run both locally before pushing. (`black .` then `ruff check --fix .` is the safe sequence.)
8. **`pytest tests/` — overall test suite still green.** No regressions in Sprint -1 or Sprint 1A tests.

---

## What to do when you finish

1. Open a PR against `mriechers/cardigan` `main` from branch `sprint-1b/crawler-refactor`.
2. PR title: `feat(mmingest): Sprint 1B — crawler core (no DB writes)`.
3. PR body should include:
   - Summary linking back to the plan and this handoff doc
   - The verification gates with checkboxes (test that they pass before submitting)
   - The manual live smoke-test result (request count, elapsed time, any errors observed)
   - A "Scope guard" section confirming you did NOT touch the DB
   - Reference the `[Sprint 1B complete]` format the conductor uses to surface to Mark
4. Mark will not merge until a separate `code-reviewer` (or `pr-review-toolkit:review-pr`) agent has run a full pass on the PR and posted findings.

---

## Commit attribution

Per `~/Developer/the-lodge/conventions/COMMIT_CONVENTIONS.md`. Use:

```
feat(mmingest): <subject>

[Agent: the-drone]

<body>

Agent: the-drone
Machine: <hostname>

Co-Authored-By: Claude <noreply@anthropic.com>
```

(The deprecated bracket form `[Agent: the-drone]` AND the new trailer block — keeping the bracket for visual parity with Sprint -1 and Sprint 1A merge commits, while the trailer block satisfies the new convention.)

---

## Reference docs (read these before starting)

- `/Users/mriechers/.claude/plans/anyway-the-reason-i-noble-whistle.md` — full plan, Sprint 1B section especially
- `mriechers/cardigan` branch `main` post-merge — your start state (Sprint -1 batched upsert + Sprint 1A schema both landed)
- `pbswi/.claude/skills/reference/media-id/parse_media_id.py` — Sprint 0 parser, **import and reuse**
- `pbswi/.claude/skills/reference/media-id/media_id_prefixes.yaml` — the 100-prefix lookup table
- `pbswi/docs/media-id.md` — canonical media ID grammar reference
- `cardigan-v4/api/services/ingest_scanner.py` — current implementation; **read** to understand what you're replacing, but the refactored output lives in `api/services/mmingest/`, NOT in this file. Don't delete or modify `ingest_scanner.py` in this sprint — Sprint 2 will retire it.
- `cardigan-v4/api/services/ingest_scheduler.py` — same pattern, your `scheduler.py` is the replacement
- `cardigan-v4/api/services/mmingest/_db.py` — Sprint 1A's parity helper, don't touch
- `cardigan-v4/alembic/versions/015_add_mmingest_files_table.py` — the schema you'll target in Sprint 2 (not this one), useful to understand the data shape your `WorkItem` records should map to cleanly
- `cardigan-v4/alembic/versions/016_add_mmingest_sidecars_and_fts.py` — sidecar table + FTS5 read-time JOIN design note (Sprint 2 will use; you can ignore for Sprint 1B but worth knowing)

---

## If you get stuck

- **You need broker credentials for mmingest** — ask Mark. The crawler will need to authenticate to mmingest.pbswi.wisc.edu (cookie? basic auth? IP allowlist?). Don't invent.
- **The asterisk semantics question comes up** — surface it. Do not invent meaning.
- **The Sprint 0 parser doesn't handle a case you need** — surface it; open a separate PR against `public-media-work/pbswi` to extend the Sprint 0 parser rather than forking it.
- **You hit a real blocker** — STOP and report. Don't ship a half-working crawler. The conductor would rather you escalate than push through.

Good hunting. — the-conductor
