# Sprint 3B Drone Handoff — mmingest Search API Endpoints

**Dispatched by:** the-conductor
**Date:** 2026-06-05
**Plan:** `/Users/mriechers/.claude/plans/anyway-the-reason-i-noble-whistle.md` (Sprint 3B section)
**Repo:** `mriechers/cardigan`
**Branch:** `sprint-3b/mmingest-search-api` off `origin/main` (`1b9ae61`)
**Parallel sibling:** Sprint 3A (`sprint-3a/consumer-keys-scoped-auth`) — running independently; coordinate only on the auth seam called out below.

**Gates merged upstream:**
- Sprint -1 (cheap fix) — `mriechers/cardigan#176` merged `36fc41a`
- Sprint 1A (schema, all tables you query) — `mriechers/cardigan#175` merged `3db4e6c`
- Sprint 1B (crawler core) — `mriechers/cardigan#177` merged `afd5fee`
- Sprint 2 (indexer integration — the data is now LIVE in your tables) — `mriechers/cardigan#187` merged `1b9ae61`

---

## Your job, in one paragraph

Build the five FastAPI endpoints under `/api/mmingest/*` that surface the merged S2 data to consumers. Editors will hit `/api/mmingest/assets/{media_id}/url` to drop a URL into PMM; downstream apps will hit `/api/mmingest/search?q=...` for BM25-ranked full-text search; `/api/mmingest/recent` lets apps watch for new arrivals. Read from `mmingest_files`, `mmingest_sidecars`, and `mmingest_sidecars_fts` via the JOIN shape S1A's migration 016 mandates (display columns live on `mmingest_files`, not FTS). Honor the variant-selection rule in the asset endpoint response shape. Cross-reference Airtable records via `AirtableClient.batch_search_sst_by_media_ids`. **Do NOT build the auth layer** — Sprint 3A's drone is doing that in parallel; you leave a clean seam for scope enforcement.

---

## Start state (already on `origin/main`)

You're starting from `1b9ae61` with this in place:

| Piece | Path | What it gives you |
|-------|------|-------------------|
| **`mmingest_files`** | `alembic/versions/015_*` | All discovered files; `media_id`, `prefix`, `prefix_category`, `show_name`, `season`, `episode`, `hd`, `revision_date`, `variant_tag`, `superseded_by`, `airtable_record_id`, `file_type`, `remote_url`, `directory_path`, `filename`, `remote_modified_at`, `etag`, `content_type`, `first_seen_at`, `last_seen_at`, `status` |
| **`mmingest_sidecars`** | `alembic/versions/016_*` | Sidecar bodies (`body_text`), `kind` (`srt` or `scc`), FK `file_id` → `mmingest_files.id`, `fetched_at`, `bytes` |
| **`mmingest_sidecars_fts`** | `alembic/versions/016_*` | External-content FTS5 over `body_text` only. Triggers auto-sync from `mmingest_sidecars`. **Read via JOIN to `mmingest_sidecars` → `mmingest_files`** for display columns — declared columns on FTS5 are `body_text` only by design. |
| **Indexer-populated data** | S2 wired the crawler → DB | After a real crawl, the tables are populated with the variant lineage applied. You can trust the data shape. |
| **Existing auth middleware** | `api/middleware/auth.py` | Current shared-key check via `CARDIGAN_API_KEY` env var. S3A is extending this to recognize consumer keys with scopes. |
| **AirtableClient** | `api/services/airtable.py` | `AirtableClient.batch_search_sst_by_media_ids(media_ids: list[str]) -> dict[str, dict]` — batch lookup, factory `get_airtable_client()` |
| **DB layer** | `api/services/database.py` | Async session/engine factories — use these |
| **Router registration pattern** | `api/main.py` | `app.include_router(<module>.router, prefix="/api/<name>", tags=["<name>"])` |

---

## Files to create

| Path | Purpose |
|------|---------|
| `api/routers/mmingest.py` | The FastAPI router. Five endpoints (specs below). |
| `api/models/__init__.py` | Empty if not already present (the `api/models/` dir may not exist — create it) |
| `api/models/mmingest.py` | Pydantic models for request/response shapes (`SearchResult`, `AssetResponse`, `VariantEntry`, `CaptionResponse`, `RecentResponse`, etc.) |
| `tests/api/test_mmingest_router.py` | Endpoint tests via FastAPI's `TestClient` against a fresh DB seeded with known fixtures |

## Files to modify (surgical only)

| Path | Change |
|------|--------|
| `api/main.py` | Add `from api.routers import mmingest` to the existing router-import block and `app.include_router(mmingest.router, prefix="/api/mmingest", tags=["mmingest"])` to the existing block. Mirror the existing pattern exactly. |

## Files to leave alone (do not touch)

- `api/middleware/auth.py` — Sprint 3A's territory. You leave a stub scope-check placeholder (rule 5 below).
- All `api/services/mmingest/*` — S1B/S2's surface, frozen.
- All Alembic migrations — schema is locked.
- `api/services/airtable.py` — read-only consumer; do NOT modify the client.
- `api/routers/ingest.py` — the OLD ingest router; DO NOT bolt onto it. New code goes in `api/routers/mmingest.py`. Keep the seam clean.

---

## Endpoint specifications

All five endpoints live under the `/api/mmingest` prefix. Pydantic models live in `api/models/mmingest.py`.

### 1. `GET /api/mmingest/search`

**Query params:**
- `q: str` (required) — FTS5 MATCH query string
- `prefix: Optional[str]` — filter by `mmingest_files.prefix` (4-char) — exact match, case-insensitive
- `since: Optional[datetime]` — filter where `mmingest_files.remote_modified_at >= since`
- `limit: int = 25` (max 100)
- `offset: int = 0`

**Response:** `{results: list[SearchResult], total: int}` where `SearchResult` has:
```python
class SearchResult(BaseModel):
    media_id: Optional[str]   # may be None if filename didn't parse
    prefix: Optional[str]
    season: Optional[int]
    episode: Optional[int]
    revision_date: Optional[str]
    modified_at: Optional[datetime]   # alias for remote_modified_at
    snippet: str                       # FTS5 snippet() output around the match
    sidecar_kind: str                  # 'srt' | 'scc'
```

**SQL shape (per the S1A external-content learning baked into migration 016):**

```sql
SELECT
    mf.media_id,
    mf.prefix,
    mf.season,
    mf.episode,
    mf.revision_date,
    mf.remote_modified_at,
    snippet(mmingest_sidecars_fts, 0, '<b>', '</b>', '...', 32) AS snippet,
    s.kind AS sidecar_kind,
    rank
FROM   mmingest_sidecars_fts AS fts
JOIN   mmingest_sidecars AS s ON s.id = fts.rowid
JOIN   mmingest_files AS mf ON mf.id = s.file_id
WHERE  mmingest_sidecars_fts MATCH :q
  AND  (:prefix IS NULL OR mf.prefix = :prefix)
  AND  (:since IS NULL OR mf.remote_modified_at >= :since)
ORDER  BY rank
LIMIT  :limit OFFSET :offset
```

Bind `q`, `prefix`, `since`, `limit`, `offset` parameters. Use `text()` with named bindings. Filter out superseded rows from search results (`mf.superseded_by IS NULL`)? **Decision: yes, by default, surface only current rows.** Add `?include_superseded=true` if a consumer ever needs the full history; out of scope for now unless trivial.

`total` count is a separate query (same WHERE, no LIMIT, `SELECT COUNT(*)`); cap or short-circuit if a future perf concern arises but trivial for current corpus.

### 2. `GET /api/mmingest/assets/{media_id}`

**Returns** `{primary, variants, superseded}` per the variant-selection rule. Honors the variant lineage S2 persisted.

**Response:**
```python
class AssetEntry(BaseModel):
    file_id: int
    media_id: str
    variant_tag: Optional[str]
    revision_date: Optional[str]
    url: str
    file_type: str
    remote_modified_at: Optional[datetime]
    file_size_bytes: Optional[int]
    airtable_record_id: Optional[str]   # populated on primary only

class AssetResponse(BaseModel):
    primary: Optional[AssetEntry]      # None if no current primary exists
    variants: list[AssetEntry]
    superseded: list[AssetEntry]
```

**Logic:**
1. `SELECT * FROM mmingest_files WHERE media_id = :media_id`. Group rows by `variant_tag`.
2. The `variant_tag=NULL` group's `superseded_by IS NULL` row is the **primary** (REV winner). The rows where `superseded_by IS NOT NULL` are the **superseded** REVs.
3. Rows with `variant_tag IS NOT NULL` and `superseded_by IS NULL` are the **variants** (coexisting cuts: PLEDGE, DS, etc.). Older REVs within a variant group also go into `superseded`.
4. Look up Airtable record_id for the primary (and only the primary) via `AirtableClient.batch_search_sst_by_media_ids([media_id])`. Populate `primary.airtable_record_id`.
5. If no rows match, return 404.
6. If only superseded rows exist (primary missing — shouldn't happen with the S2 algorithm but defensive), return primary=None with the superseded list populated.

**Caching:** out of scope; the lookup is cheap. Document any TODOs in code comments.

### 3. `GET /api/mmingest/assets/{media_id}/url`

Consumer-1 convenience for editors. Returns just the resolved URL string.

**Query params:**
- `variant: Optional[str]` — override; if provided, returns the variant's URL instead of primary

**Response:** `{url: str}` (plain JSON, not a string body — easier for clients to parse consistently)

**Logic:** Reuse the asset-lookup logic. Default returns `primary.url`. With `?variant=PLEDGE`, returns the matching variant's URL (404 if no such variant for this media_id). If `media_id` has no primary AND no requested variant, 404.

### 4. `GET /api/mmingest/assets/{media_id}/captions`

Returns the cached sidecar body, served from DB. **No mmingest network hit.**

**Query params:**
- `format: Literal['srt', 'scc'] = 'srt'`

**Response:**
```python
class CaptionResponse(BaseModel):
    media_id: str
    kind: str  # 'srt' | 'scc'
    body_text: str
    bytes: Optional[int]
    fetched_at: Optional[datetime]
```

**Logic:**
1. Find the primary `mmingest_files` row for `media_id` (same primary-resolution logic as endpoint 2).
2. JOIN to `mmingest_sidecars` on `file_id`, filter `kind = :format`.
3. Return the row. If no matching sidecar exists (e.g. only `.srt` cached and `.scc` requested), 404.
4. **Do NOT GET from mmingest here.** If body_text is NULL or empty, the indexer hasn't filled it yet — return 503 with a message indicating the sidecar is being indexed; don't proxy to mmingest.

### 5. `GET /api/mmingest/recent`

Listing endpoint for apps watching for new arrivals.

**Query params:**
- `since: Optional[datetime]` — return rows where `first_seen_at >= since`; if absent, default to last 24h
- `limit: int = 50` (max 200)
- `prefix: Optional[str]` — same prefix filter as `/search`

**Response:**
```python
class RecentEntry(BaseModel):
    media_id: Optional[str]
    prefix: Optional[str]
    show_name: Optional[str]
    file_type: str
    url: str
    first_seen_at: datetime
    remote_modified_at: Optional[datetime]

class RecentResponse(BaseModel):
    results: list[RecentEntry]
    total: int
```

**SQL shape:**
```sql
SELECT * FROM mmingest_files
WHERE  first_seen_at >= :since
  AND  (:prefix IS NULL OR prefix = :prefix)
  AND  superseded_by IS NULL
ORDER  BY first_seen_at DESC
LIMIT  :limit
```

Same superseded filter as `/search` — surface only current rows by default.

---

## Critical rules

### 1. Honor the external-content FTS5 read shape from S1A.

Migration 016's docstring is explicit: external-content FTS5 cannot have UNINDEXED display columns that don't exist on the content table. The fix is to JOIN. The S1A test `test_fts_match_join_returns_display_fields` is the regression guard.

**Your `/search` query MUST be a JOIN.** Don't try to SELECT `mmingest_sidecars_fts.media_id` — there is no such column. Read `mf.media_id` from the joined `mmingest_files`. Don't relitigate this — it's settled.

### 2. Honor the variant-selection rule in the response shape.

`/assets/{media_id}` returns `{primary, variants, superseded}`. NOT a flat list. NOT just `primary`. NOT `{primary, all_others}`. Three keys, populated per the rule. Default `primary.url` is what editors will paste into PMM; `variants[]` is what the clip-finder app will use to know parallel cuts exist; `superseded[]` is for audit / debugging.

### 3. Leave a clean seam for S3A's scope enforcement.

S3A's middleware (which is running in parallel as you work) will check `mmingest:read` scope before your endpoints fire. While S3A is still in flight, your endpoints have no scope check. **Add a no-op stub** so the seam is obvious:

```python
def _require_scope(scope: str):
    """Stub: real scope enforcement lands in S3A middleware.

    Kept as a no-op dependency so endpoint signatures don't change when S3A merges.
    """
    async def _dep(request: Request) -> None:
        # When S3A's middleware is live, request.state.consumer_scopes is set;
        # the middleware has already rejected the call with 403 if scope is missing.
        # This dep stays as a marker so endpoint signatures document required scope.
        return None
    return Depends(_dep)
```

Then declare each endpoint:
```python
@router.get("/search", ...)
async def search(... , _scope=_require_scope("mmingest:read")):
    ...
```

When S3A merges, the middleware handles authorization; this stub is a no-op marker. Mark may clean it up in a follow-up; that's fine. The important thing is your endpoint code documents the required scope.

### 4. The OLD `api/routers/ingest.py` is OFF LIMITS.

Don't bolt new endpoints onto `ingest.py`. New code goes in `api/routers/mmingest.py`. Keep the seam clean — Sprint 4 may retire `ingest.py` entirely.

### 5. AirtableClient is read-only, batched, and used SPARINGLY.

- Use `batch_search_sst_by_media_ids` for `/assets/{media_id}` — one media_id wrapped in a single-element list.
- Do NOT call Airtable in `/search` (per-result Airtable lookups would be slow and waste API quota). The `mmingest_files.airtable_record_id` column is populated by S2's indexer if the link exists; surface that pre-cached value in search if you want it (and add the column to `SearchResult`), or omit. **Decision: omit from `/search` for v1.** Keep search fast.
- Do NOT call Airtable in `/recent` for the same reason.
- Do NOT call Airtable in `/captions` or `/url`.
- AirtableClient hits the real Airtable API — your tests must mock it. Use `unittest.mock.AsyncMock` or `pytest-mock`.

### 6. Pydantic models go in `api/models/mmingest.py`, not inline.

Keep router file readable. All request/response models in the `api/models/mmingest.py` module. Import into the router.

### 7. Pagination defaults.

- `/search`: limit=25, max=100, offset=0
- `/recent`: limit=50, max=200; since default = now - 24h
- Reject out-of-range `limit` with 422

### 8. 404 vs 503 semantics.

- 404: media_id (or the requested variant) doesn't exist in `mmingest_files`
- 503: the row exists but the sidecar body isn't cached yet (indexer hasn't run for this file)

Do NOT proxy to mmingest from the API — that defeats the purpose of the indexer.

### 9. Cardigan lint stack is HARD rule.

`black .` then `ruff check --fix .`. CI runs both. Don't be the sixth sprint bitten.

### 10. Don't merge. Surface for Mark's gate.

Open the PR. Self-verify the gates. Surface to the conductor. Do NOT click merge.

---

## Verification gates (must all pass before opening the PR)

For all gates, your tests should use `fastapi.testclient.TestClient` against a fresh seeded sqlite DB (mirror the fixture pattern from `tests/api/test_mmingest_parity.py::migrated_engine`). Mock `AirtableClient` calls.

1. **`/search` happy path:** Seed `mmingest_sidecars` with `6POL0101_REV20260319.srt` body text containing "inside wisconsin politics". `GET /api/mmingest/search?q=politics` returns the expected result with non-empty `snippet`, the correct `media_id`, `prefix`, `revision_date`. BM25 ordering observable when seeding multiple rows.

2. **`/search` filters:** `?prefix=6POL` filters correctly. `?since=2026-03-01` filters correctly. `?limit=1&offset=0` returns exactly 1 result; `?limit=1&offset=1` returns the next.

3. **`/assets/{media_id}` primary-only:** Seed one `mmingest_files` row with `media_id='6POL0101'`, `variant_tag=NULL`, `superseded_by=NULL`. `GET /api/mmingest/assets/6POL0101` returns `{primary: {...}, variants: [], superseded: []}` with `primary.media_id='6POL0101'`. Airtable lookup mocked.

4. **`/assets/{media_id}` with variant:** Seed primary + `_PLEDGE` variant. Endpoint returns `primary` populated AND `variants` containing the PLEDGE entry. `superseded` empty.

5. **`/assets/{media_id}` with superseded REV:** Seed two `_REV` versions; per the S2 algorithm, the older has `superseded_by` set. Endpoint returns `primary` = newer, `superseded` containing the older.

6. **`/assets/{media_id}` 404:** Media ID not in DB → 404.

7. **`/assets/{media_id}/url` happy path:** Same seed as gate 3. Returns `{"url": "..."}` matching the primary's URL.

8. **`/assets/{media_id}/url?variant=PLEDGE`:** Same seed as gate 4. Returns the variant's URL.

9. **`/assets/{media_id}/url?variant=PLEDGE` 404:** No PLEDGE variant for that media_id → 404.

10. **`/assets/{media_id}/captions` happy path:** Seed primary + sidecar with body_text. `?format=srt` returns the body. **No mmingest network call** (assert via mocked httpx).

11. **`/assets/{media_id}/captions` 503:** Seed primary but no sidecar with that kind → 503.

12. **`/recent`:** Seed three rows with different `first_seen_at` values; query with `?since=` and verify ordering + filtering.

13. **No regression on S-1's batched upsert:** `pytest tests/test_ingest_scanner.py::TestBatchedUpsert` — 3/3 pass.

14. **No regression on S1A's parity tests:** `pytest tests/api/test_mmingest_parity.py` — 11/11 pass.

15. **No regression on S1B's tests:** `pytest tests/services/mmingest/` — all green.

16. **No regression on S2's integration tests:** `pytest tests/integration/test_mmingest_index.py` — 8/8 pass.

17. **Lint stack:** `black --check .` and `ruff check .` both clean.

18. **Live smoke test (manual, document in PR):** With the dev DB populated by an S2 indexer run pointed at one mmingest directory, hit each of the five endpoints and capture the response shapes in the PR description. Show `/search` returning a real result; show `/assets/{media_id}` returning the full variant/superseded shape for at least one real media_id; show `/recent` returning the recently-indexed files.

---

## What to do when you finish

1. Open the PR against `mriechers/cardigan` `main` from branch `sprint-3b/mmingest-search-api`.
2. PR title: `feat(mmingest): Sprint 3B — search + asset + captions + recent API endpoints`.
3. PR body must include:
   - Summary linking back to the plan and this handoff doc.
   - Verification gates checklist (each must pass).
   - Live smoke-test results for all five endpoints.
   - A "Scope-enforcement seam" section noting the `_require_scope()` stub and how S3A's middleware will activate it without further endpoint changes.
   - Reference issues #182, #183, #184 as tracked-but-not-this-PR's-job.
   - Note any decisions about the `superseded_by` filtering default (rule above; document it).
4. Mark will not merge until a separate `pr-review-toolkit:review-pr` (or `code-reviewer`) agent has run a full pass and posted findings.

---

## Coordination with the parallel S3A drone

S3A is extending `api/middleware/auth.py` and adding `api/services/auth/`. **You will not touch S3A's files; S3A will not touch yours.**

Two coordination points:

1. **The `_require_scope("mmingest:read")` stub is yours.** S3A's middleware enforces the actual scope check before your endpoints fire. The stub is a no-op marker; it stays compatible with S3A's work.
2. **Audit log writes are middleware territory, not router territory.** Your router code MUST NOT write to `mmingest_audit_log` — S3A's middleware does that. If you find yourself reaching for the audit log table, stop.

**If you finish before S3A:** open the PR anyway. Don't wait. Parallel-safe.

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

No emojis in commit messages.

---

## Reference docs (read before starting)

- `/Users/mriechers/.claude/plans/anyway-the-reason-i-noble-whistle.md` — full plan, Sprint 3B section especially
- `mriechers/cardigan` branch `main` at `1b9ae61` — your start state
- `alembic/versions/015_add_mmingest_files_table.py` — full column inventory + variant rule columns
- `alembic/versions/016_add_mmingest_sidecars_and_fts.py` — FTS5 design note; the JOIN read shape is documented in the migration docstring
- `api/services/airtable.py` — `AirtableClient.batch_search_sst_by_media_ids` signature
- `api/services/database.py` — async session/engine factories
- `api/services/mmingest/indexer.py` — S2's persistence shape; helpful for understanding how the variant lineage rows look in the DB
- `api/services/mmingest/_db.py::fts_parity_delta` — useful for a future health endpoint; NOT in this sprint
- `api/main.py` — router registration pattern (mirror exactly)
- `tests/api/test_mmingest_parity.py::migrated_engine` — fixture pattern for your tests
- `tests/integration/test_mmingest_index.py` — pattern for FastAPI-against-seeded-DB tests
- `~/Developer/the-lodge/conventions/COMMIT_CONVENTIONS.md` — attribution format
- Memory: `feedback_cardigan_lint_stack` — black + ruff both in CI

---

## Active issues you should be aware of (not your job to fix)

- **#182** — parser sync between cardigan-v4 and pbswi Sprint 0 (unrelated to API; FYI)
- **#183** — TokenBucket burst tuning (unrelated to API; FYI)
- **#184** — unknown-variant-tag log level (unrelated to API; FYI)

---

## If you get stuck

- **FTS5 query returns "no such column: T.media_id"** — you tried to declare display columns on the FTS5 table. Read migration 016's docstring; use the JOIN shape.
- **Pydantic v1 vs v2 ambiguity** — check `pyproject.toml`; Cardigan uses v2 (per recent dependency bumps). Use `model_validator`, `model_config`, `Field(...)`. No `class Config: orm_mode = True`.
- **AirtableClient is hitting real Airtable in tests** — you forgot to mock. Use `unittest.mock.AsyncMock` patched at `api.routers.mmingest.get_airtable_client` (or wherever you import).
- **S3A's middleware merges before yours** — your `_require_scope()` stub is now redundant but harmless. Leave it for Mark to clean up in a follow-up.
- **Real blocker** — STOP and report. A broken Sprint 3B blocks S4 (MCP tools that mirror these endpoints).

Good hunting. — the-conductor
