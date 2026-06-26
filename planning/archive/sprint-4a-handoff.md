# Sprint 4A Drone Handoff — mmingest MCP Tools

**Dispatched by:** the-conductor
**Date:** 2026-06-05
**Plan:** `/Users/mriechers/.claude/plans/anyway-the-reason-i-noble-whistle.md` (Sprint 4 section)
**Repo:** `mriechers/cardigan`
**Branch:** `sprint-4a/mmingest-mcp-tools` off `origin/main` (`5e0f1c6`)
**Parallel siblings:** Sprint 4B (pbswi skill refactor: `brainstorm-title-options`), Sprint 4C (pbswi skill refactor: `audit-assets`). Different repos, different files — fully parallel-safe.

**Gates merged upstream (cardigan main `5e0f1c6`):**
- S-1 — `36fc41a` (cheap fix)
- S1A — `3db4e6c` (schema)
- S1B — `afd5fee` (crawler) + `73e5ad4` (follow-up: queue race, dedup, politeness, lenient parse)
- S2 — `1b9ae61` (indexer)
- S3B — `b7a6afa` (search API, the `/api/mmingest/*` endpoints you'll mirror)
- S3A — `5e0f1c6` (consumer keys + scoped auth + audit log)

---

## Your job, in one paragraph

Add three MCP tools to `mcp_server/server.py` that let agents (including Claude Code) query the mmingest search index without an HTTP round-trip. The tools mirror Sprint 3B's HTTP endpoints (`/api/mmingest/search`, `/api/mmingest/assets/{media_id}`, `/api/mmingest/recent`) but call into Cardigan's own service layer in-process. The MCP server runs alongside the FastAPI app and shares its DB connection layer; no HTTP, no auth round-trip — same engine, same models, same Pydantic shapes.

---

## Start state (already on `origin/main`)

| Piece | Path | What it gives you |
|-------|------|-------------------|
| **MCP server** | `mcp_server/server.py` | 2174-line file with `list_tools()` returning `Tool(...)` definitions and `call_tool()` dispatching by name. Standard MCP SDK pattern. |
| **Sprint 3B router** | `api/routers/mmingest.py` | The HTTP endpoints whose shape you mirror. Read this for the exact SQL queries + Pydantic models you'll reuse. |
| **Pydantic models** | `api/models/mmingest.py` | `SearchResult`, `AssetEntry`, `AssetResponse`, `RecentEntry`, `RecentResponse`, etc. Reuse these — don't redefine. |
| **AirtableClient** | `api/services/airtable.py` | `batch_search_sst_by_media_ids` — reuse on `get_mmingest_asset` (primary only, NOT in search/recent). |
| **DB layer** | `api/services/database.py` | Async session factories. The MCP server should use the same engine the FastAPI app does. |
| **Existing MCP tool pattern** | `mcp_server/server.py` | Search the file for `name == "search_projects"` for a working example of an MCP tool that queries the DB and returns formatted markdown. Mirror that pattern. |

---

## Files to create

None. This sprint is purely additive edits to `mcp_server/server.py`.

If you find yourself wanting to extract a helper, put it in `mcp_server/server.py` alongside the existing helpers (e.g. near `async def fetch_sst_context`, `async def search_sst_by_media_id`). Don't create new modules unless you have a strong reason and surface it to the conductor first.

## Files to modify (surgical only)

| Path | Change |
|------|--------|
| `mcp_server/server.py` | Add three `Tool(...)` entries to `list_tools()`; add three `elif name == "..."` branches to `call_tool()`; add three async handler functions for the new tools. Mirror the existing pattern exactly. |
| `tests/mcp_server/test_mmingest_tools.py` (if no existing `tests/mcp_server/` exists, create this directory + `__init__.py`) | Unit tests for the three new tools using `pytest-asyncio` and a seeded sqlite fixture mirroring `tests/api/test_mmingest_parity.py::migrated_engine` |

## Files to leave alone (do not touch)

- `api/routers/mmingest.py` — Sprint 3B's surface, frozen. You consume its services, not its routes.
- `api/services/mmingest/*` — S1B/S2's surface, frozen.
- `api/middleware/auth.py` — S3A's surface, frozen. MCP runs in-process, doesn't go through the HTTP auth middleware.
- All Alembic migrations.
- Any existing MCP tools (`list_processed_projects`, `search_projects`, etc.) — don't refactor them; just add yours alongside.

---

## Tool specifications

The three tools mirror Sprint 3B's HTTP endpoints. Reuse the same SQL queries, the same Pydantic models, and the same auth-agnostic semantics (the MCP server doesn't enforce scopes — it runs in-process and trusts the caller).

### Tool 1: `search_mmingest`

```python
Tool(
    name="search_mmingest",
    description=(
        "Full-text search PBS Wisconsin's mmingest caption corpus via FTS5. "
        "Returns BM25-ranked results with snippets. Mirrors HTTP "
        "GET /api/mmingest/search but runs in-process (no HTTP, no auth round-trip). "
        "Use for finding episodes by transcript content (e.g. 'inside wisconsin politics' "
        "or 'climate change')."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "FTS5 MATCH query string. Phrases in double quotes match adjacently; bare terms are AND-ed."
            },
            "prefix": {
                "type": "string",
                "description": "Optional 4-char show prefix filter (e.g. '6POL' or '2WLI'). Exact case-insensitive match."
            },
            "since": {
                "type": "string",
                "description": "Optional ISO 8601 datetime; only return results modified at-or-after this timestamp."
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return. Default 25, max 100.",
                "default": 25,
                "minimum": 1,
                "maximum": 100
            }
        },
        "required": ["query"]
    }
)
```

**Implementation:**
1. Reuse the SQL from `api/routers/mmingest.py::search`. Same JOIN shape (`mmingest_sidecars_fts → mmingest_sidecars → mmingest_files`). Same superseded filter (`mf.superseded_by IS NULL` by default).
2. Return a markdown-formatted string (per MCP convention — tools return `list[TextContent]`). Header per result with media_id + show_name + revision_date; body with the FTS5 snippet (HTML-stripped to plain text with `<b>...</b>` highlight markers preserved for Claude to interpret).
3. If `query` is empty or whitespace, return an error TextContent.
4. If the FTS5 MATCH raises (malformed query syntax — see issue #191 for the parallel HTTP-side fix), catch and return an error TextContent like `"Error: invalid FTS5 query syntax. Use double quotes around multi-word phrases."` — do NOT propagate the SQLAlchemy exception.

### Tool 2: `get_mmingest_asset`

```python
Tool(
    name="get_mmingest_asset",
    description=(
        "Get the canonical asset record for a PBS Wisconsin Media ID, including "
        "primary URL, variants (PLEDGE/DS cuts), superseded REVs, and the linked "
        "Airtable record ID. Mirrors HTTP GET /api/mmingest/assets/{media_id} "
        "but runs in-process."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "media_id": {
                "type": "string",
                "description": "8-character Media ID (e.g. '6POL0101'). Case-insensitive."
            }
        },
        "required": ["media_id"]
    }
)
```

**Implementation:**
1. Reuse the asset-resolution logic from `api/routers/mmingest.py::get_asset`. Same `{primary, variants, superseded}` shape.
2. Call `AirtableClient.batch_search_sst_by_media_ids([media_id])` for the primary. If Airtable is unconfigured or raises, log a warning and return the response with `primary.airtable_record_id = None` (don't fail the whole call — same fallback as S3B does, per issue #193 the test for this fallback is tracked separately).
3. Format the response as markdown. Sections: `# Asset: {media_id}` → `## Primary` (URL, REV date, show, Airtable link if available) → `## Variants` (one bullet per variant_tag) → `## Superseded REVs` (one bullet per older REV).
4. 404 case (no rows for that media_id) → return an error TextContent saying so; don't raise.

### Tool 3: `list_recent_mmingest_assets`

```python
Tool(
    name="list_recent_mmingest_assets",
    description=(
        "List recently-arrived PBS Wisconsin assets on mmingest, ordered by "
        "first-seen timestamp. Mirrors HTTP GET /api/mmingest/recent but runs "
        "in-process. Use to poll for new arrivals (e.g. caption files just "
        "delivered for an episode in production)."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "since": {
                "type": "string",
                "description": (
                    "Optional ISO 8601 datetime cutoff. If absent, defaults to "
                    "24 hours ago."
                )
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return. Default 50, max 200.",
                "default": 50,
                "minimum": 1,
                "maximum": 200
            },
            "prefix": {
                "type": "string",
                "description": "Optional 4-char show prefix filter."
            }
        }
    }
)
```

**Implementation:**
1. Reuse the SQL from `api/routers/mmingest.py::recent`. Default `since = now - 24h` if absent. Same superseded filter.
2. Return markdown list: one line per file with `media_id` (or `filename` if media_id is None), `prefix`, `show_name`, `file_type`, `first_seen_at` (ISO), and the URL.
3. Empty result set → return a friendly "no new arrivals in the window" message.

---

## Critical rules

### 1. In-process call, not HTTP.

The MCP server is in the same process tree as the FastAPI app. Your tools call into the service layer (or directly construct the SQL via a `get_session()` async session), they do NOT make HTTP calls to `localhost:8100/api/mmingest/*`. Doing so would defeat the purpose, add latency, and re-do the auth dance unnecessarily.

If you find yourself reaching for `httpx.AsyncClient` to call back into Cardigan's own router, stop. Read the existing MCP tools (`search_projects`, etc.) for the pattern — they use the DB layer directly.

### 2. Reuse Sprint 3B's SQL and Pydantic models verbatim.

DO NOT re-derive the SQL queries. DO NOT redefine the Pydantic models. Import from `api.models.mmingest` (or the equivalent if S3B's router defined models inline — check the S3B PR's actual structure). If the router code is structured such that its query functions can be reused (e.g. a `_search_query(conn, q, prefix, since, limit, offset)` helper), reuse those.

If the SQL or models live inline in the router and there's no clean reuse path, you have two options:
- **Option A (preferred):** lift the query/format helpers into a new `api/services/mmingest/query.py` module, import from both the router and the MCP server. Surgical, no behavior change.
- **Option B:** duplicate the query in the MCP tool with a clear comment `# MIRROR OF api/routers/mmingest.py::search — keep in sync`. Slightly less clean but acceptable for v1.

Surface the decision in the PR description.

### 3. The MCP server does NOT enforce mmingest scopes.

The MCP server runs in-process; the caller is already trusted (you're running inside Cardigan's own runtime, not exposed over the network). Don't try to call into S3A's scope-check middleware — it's an HTTP middleware, not a service-layer construct. The audit log table (`mmingest_audit_log` from S3A) is also middleware-driven; you do NOT write to it from MCP tools.

If a future need arises to track MCP tool calls, that's a separate sprint.

### 4. Markdown formatting consistent with existing MCP tools.

Look at `name == "get_sst_metadata"` (around line 1500 of `mcp_server/server.py`) for the gold-standard format: bold section headers, code blocks for content, status emoji where applicable. Mirror that style. Don't reinvent.

### 5. Cardigan lint stack — HARD rule.

CI runs `black --check` and `ruff check` on the whole project. Run both locally before pushing:

```bash
cd /Users/mriechers/Developer/pbswi/cardigan-v4
source venv/bin/activate
black .
ruff check --fix .
black --check .   # confirm clean
ruff check .      # confirm clean
```

Every prior sprint got bitten on this. Don't be the seventh. (Issue #196 tracks pinning ruff+black versions — until that lands, the local install must match the CI install. Verify your `ruff --version` and `black --version` match what `.github/workflows/` configures.)

### 6. Don't merge. Surface for Mark's gate.

Open the PR. Self-verify gates. Surface to the conductor. Do NOT click merge.

---

## Verification gates (must all pass before opening the PR)

1. **`search_mmingest` returns the canonical regression case.** With the dev DB seeded by an S2 indexer run, calling `search_mmingest(query="inside wisconsin politics")` returns at least one result with `media_id` matching `6POL*`.

2. **`search_mmingest` filters work.** `prefix="6POL"` narrows; `since="2026-06-01T00:00:00Z"` filters by modification time; `limit=5` caps.

3. **`search_mmingest` returns the SAME results as the HTTP endpoint.** Compare against `curl -X GET "http://localhost:8100/api/mmingest/search?q=politics" -H "X-API-Key: <shared-key>"`. Ordering, count, and snippets should match (modulo formatting — markdown vs JSON).

4. **`get_mmingest_asset` happy path.** Seeded DB has a row for `6POL0101`. Tool returns `{primary, variants: [], superseded: [...]}` with the primary's URL populated and (if Airtable mocked) the airtable_record_id surfaced.

5. **`get_mmingest_asset` with variants.** Seed primary + `_PLEDGE` variant. Tool surfaces both — primary in the primary section, PLEDGE in the variants section.

6. **`get_mmingest_asset` 404 case.** Media ID not in DB → friendly error TextContent, no exception propagated.

7. **`get_mmingest_asset` Airtable failure fallback.** Mock `batch_search_sst_by_media_ids` to raise. Tool still returns the row with `airtable_record_id = None` and an annotation in the markdown that Airtable lookup failed. Don't crash. (This is the same fallback issue #193 tracks for the HTTP endpoint — apply the same defensive treatment here.)

8. **`list_recent_mmingest_assets` defaults.** No args → returns last 24h. With `since=` → returns from that timestamp forward. `limit=5` caps.

9. **`list_recent_mmingest_assets` matches HTTP `/recent`.** Same comparison as gate 3.

10. **MCP server still starts cleanly.** `python mcp_server/server.py` (or however it's launched per `pyproject.toml`) starts without import errors and reports the new tools in its capabilities. If there's a smoke-test command for the MCP server, run it.

11. **No regression on S1A's parity tests:** `pytest tests/api/test_mmingest_parity.py` — 11/11 pass.

12. **No regression on S1B's tests:** `pytest tests/services/mmingest/` — all green.

13. **No regression on S2's integration tests:** `pytest tests/integration/test_mmingest_index.py` — 8/8 pass.

14. **No regression on S3A's tests:** `pytest tests/api/test_consumer_key_auth.py tests/api/test_consumer_keys_service.py` — all green.

15. **No regression on S3B's tests:** `pytest tests/api/test_mmingest_router.py` — all green.

16. **No regression on S-1's batched upsert:** `pytest tests/test_ingest_scanner.py::TestBatchedUpsert` — 3/3 pass.

17. **Lint stack:** `black --check .` and `ruff check .` both clean on the whole project.

18. **Manual smoke from Claude Code:** restart Claude Code with the cardigan MCP server configured. Call each of the three new tools from Claude Code. Each returns a markdown response. Capture the responses in the PR description. (If you can't manually verify from Claude Code in this session, document a clear set of steps for Mark to do it before merge.)

---

## What to do when you finish

1. Open the PR against `mriechers/cardigan` `main` from branch `sprint-4a/mmingest-mcp-tools`.
2. PR title: `feat(mcp): Sprint 4A — add 3 mmingest search/asset/recent MCP tools`.
3. PR body must include:
   - Summary linking to the plan and this handoff doc.
   - Verification gates as a checklist.
   - Manual smoke results from Claude Code (or steps for Mark to run).
   - A "Code reuse" section explaining whether you took Option A (extracted query helpers) or Option B (duplicated with sync comments) and why.
   - Reference issues #182, #183, #184, #190, #191, #192, #193, #194, #195, #196 as tracked-but-not-this-PR's-job.
4. Mark will not merge until a separate `pr-review-toolkit:review-pr` (or `code-reviewer`) agent has run a full pass and posted findings.

---

## Coordination with the parallel S4B/S4C drones

S4B refactors `public-media-work/pbswi/.claude/skills/content/brainstorm-title-options/SKILL.md` to call your `search_mmingest` (or the HTTP `/api/mmingest/search`). S4C refactors `audit-assets/SKILL.md` similarly.

**Coordination points:**
1. **The skills can call your MCP tool OR the HTTP endpoint.** S3B's endpoints are already merged, so S4B/S4C have a working HTTP fallback even before your MCP tools merge. Don't block S4B/S4C; don't wait on them. Different repos, fully independent.
2. **Tool naming is your contract.** Once your PR lands, S4B/S4C can invoke `search_mmingest`, `get_mmingest_asset`, `list_recent_mmingest_assets`. If you rename a tool mid-sprint, surface to the conductor so the parallel drones don't get stranded.

**If you finish before S4B/S4C:** open the PR. Don't wait.

---

## Commit attribution

```
feat(mcp): <subject>

[Agent: the-drone]

<body>

Agent: the-drone
Machine: <hostname>

Co-Authored-By: Claude <noreply@anthropic.com>
```

No emojis in commit messages.

---

## Reference docs (read before starting)

- `/Users/mriechers/.claude/plans/anyway-the-reason-i-noble-whistle.md` — Sprint 4 section
- `mriechers/cardigan` branch `main` at `5e0f1c6` — your start state
- `mcp_server/server.py` — the file you're editing; especially `list_tools()` and the existing tool handlers like `search_projects`, `get_sst_metadata`
- `api/routers/mmingest.py` — Sprint 3B's HTTP endpoints; SQL queries to mirror
- `api/models/mmingest.py` — Pydantic models to reuse
- `api/services/mmingest/_db.py` — engine factories
- `api/services/airtable.py` — `batch_search_sst_by_media_ids` signature
- `tests/api/test_mmingest_router.py` — test patterns for the HTTP endpoints; mirror for MCP tools
- `~/Developer/the-lodge/conventions/COMMIT_CONVENTIONS.md` — attribution format
- Memory: `feedback_cardigan_lint_stack` — black + ruff both in CI

---

## Active issues you should be aware of (not your job to fix)

- **#182** — parser sync between cardigan-v4 and pbswi Sprint 0 (unrelated to MCP; FYI)
- **#183** — TokenBucket burst tuning (unrelated to MCP; FYI)
- **#184** — unknown-variant-tag log level (unrelated to MCP; FYI)
- **#190** — replace per-request AsyncEngine with shared get_session() in S3B router (will reduce duplication you might be tempted to add)
- **#191** — FTS5 syntax errors return 500 instead of 400 (HTTP side — defend the same case in your tool with a friendly error string)
- **#192** — Airtable timeout configuration on `/assets/{id}` (you inherit the same client; you might want to set a shorter timeout in your tool's call, but coordinate with #192's fix to avoid divergence)
- **#193** — test for Airtable-exception fallback path (HTTP side — apply same fallback in your tool, but the test is tracked separately for HTTP)
- **#194** — `?include_superseded=true` opt-in (HTTP side — if you add the same option to your tool's `inputSchema`, document it; if not, that's fine, mirror the default behavior)
- **#195** — adopt TwoLaneWorkQueue in indexer enqueue path (unrelated to MCP; FYI)
- **#196** — pin ruff + black versions in CI (until merged, ensure your local versions match CI's)

---

## If you get stuck

- **MCP SDK Tool definitions feel verbose** — they are. The existing tools have the same boilerplate. Copy-paste-adjust.
- **`call_tool()` dispatch chain is unwieldy at 2174 lines** — it is. Don't refactor it in this sprint. Just add your three `elif name == "..."` branches. Refactor can be a separate PR if Mark wants it.
- **Pydantic v2 import errors** — Cardigan is on v2. Use `model_validate`, `model_dump`, not `parse_obj` / `.dict()`.
- **Sprint 3B's SQL is hard to extract cleanly** — Option B (duplicate with sync comments) is acceptable. Document the choice.
- **Real blocker** — STOP and report. A broken MCP server breaks Claude Code's connection to Cardigan, which downstream blocks S4B/S4C from using the MCP path (they fall back to HTTP, which is fine, but worth surfacing).

Good hunting. — the-conductor
