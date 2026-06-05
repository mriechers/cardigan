# Sprint 3A Drone Handoff — Consumer Keys + Scoped Auth

**Dispatched by:** the-conductor
**Date:** 2026-06-05
**Plan:** `/Users/mriechers/.claude/plans/anyway-the-reason-i-noble-whistle.md` (Sprint 3A section)
**Repo:** `mriechers/cardigan`
**Branch:** `sprint-3a/consumer-keys-scoped-auth` off `origin/main` (`1b9ae61`)
**Parallel sibling:** Sprint 3B (`sprint-3b/mmingest-search-api`) — running independently; coordinate only on shared concerns called out below.

**Gates merged upstream:**
- Sprint -1 (cheap fix) — `mriechers/cardigan#176` merged `36fc41a`
- Sprint 1A (schema, includes the `consumer_keys` table you'll use) — `mriechers/cardigan#175` merged `3db4e6c`
- Sprint 1B (crawler core) — `mriechers/cardigan#177` merged `afd5fee`
- Sprint 2 (indexer integration) — `mriechers/cardigan#187` merged `1b9ae61`

---

## Your job, in one paragraph

Build per-consumer API key authentication with scope enforcement on top of the existing shared-key middleware. The S1A schema (`consumer_keys` table from migration 017) is ready and waiting. Consumers present a raw key in the `X-API-Key` header; you hash it with bcrypt, look it up in `consumer_keys`, check the requested path against the key's scopes, and either let the request through (200) or block it (403). **The existing shared-key path MUST keep working** — Cardigan's existing endpoints (`/api/jobs`, `/api/queue`, etc.) cannot break. New consumer-key path is additive. Also ship an audit log so every `/api/mmingest/*` hit records which consumer hit it.

---

## Start state (already on `origin/main`)

You're starting from `1b9ae61` with these relevant pieces in place:

| Piece | Path | What it does |
|-------|------|--------------|
| **`consumer_keys` table** | `alembic/versions/017_add_consumer_keys.py` | Schema: `id`, `key_hash` (unique idx), `label`, `scopes` (CSV TEXT default `''`), `created_at`, `last_used_at` |
| **Existing middleware** | `api/middleware/auth.py` | `APIKeyMiddleware` — shared-key check via `CARDIGAN_API_KEY` env var; exempts `EXEMPT_PATHS` and `EXEMPT_PREFIXES` (e.g. `/api/ws/`) |
| **Existing rate-limit middleware** | `api/middleware/rate_limit.py` | Reference for the middleware pattern |
| **DB layer** | `api/services/database.py` | Async SQLAlchemy session/engine factories; use these, don't roll your own |
| **Main app wiring** | `api/main.py` | Currently does `app.add_middleware(APIKeyMiddleware)`; your changes ship through this same hook |

`api/services/auth/` does NOT exist yet. You'll create it.

---

## Files to create

| Path | Purpose |
|------|---------|
| `api/services/auth/__init__.py` | Empty, plus re-export of public API |
| `api/services/auth/consumer_keys.py` | Public functions: `async def create_consumer_key(label: str, scopes: list[str]) -> tuple[str, int]` returning `(plaintext_key, consumer_id)`; `async def lookup_consumer_key(plaintext: str) -> Optional[ConsumerKeyRecord]`; `async def touch_last_used(consumer_id: int) -> None`; dataclass `ConsumerKeyRecord(id, label, scopes: frozenset[str], created_at, last_used_at)` |
| `api/services/auth/audit_log.py` | Public function: `async def write_audit_log(consumer_id: Optional[int], path: str, media_id: Optional[str], timestamp: datetime, outcome: str) -> None`. Writes to a new `mmingest_audit_log` table — you create the migration as part of this sprint (see below). |
| `scripts/create_consumer_key.py` | CLI helper. Usage: `python scripts/create_consumer_key.py --label "frontend-prod" --scopes mmingest:read,mmingest:stream`. Calls `create_consumer_key()`, prints plaintext ONCE to stdout, exits. Mirror the pattern of any existing one-off scripts already in `scripts/`. |
| `alembic/versions/018_add_mmingest_audit_log.py` | Migration: new table `mmingest_audit_log` with `id, consumer_id (FK consumer_keys.id, nullable for shared-key callers), path TEXT, media_id TEXT nullable, ts DATETIME default current_timestamp, outcome TEXT ('allowed'/'denied'/'shared_key')`. Indexes on `consumer_id` and `ts`. |
| `tests/api/test_consumer_key_auth.py` | All auth+scope tests (see verification gates) |
| `tests/api/test_consumer_keys_service.py` | Unit tests for `consumer_keys.py` helpers (hashing, lookup, touch) |

## Files to modify (surgical only)

| Path | Change |
|------|--------|
| `api/middleware/auth.py` | Extend `APIKeyMiddleware.dispatch` to: (1) keep current shared-key check unchanged for back-compat; (2) IF the provided key doesn't match `CARDIGAN_API_KEY`, attempt consumer-key lookup; (3) on consumer-key match, attach `request.state.consumer_id` and `request.state.consumer_scopes`; (4) for `/api/mmingest/*` paths, enforce the appropriate scope (`mmingest:read` for GET, `mmingest:stream` for `/stream` endpoints — note `/stream` is Phase 2; you're still adding the scope check so Phase 2 is ready); (5) write audit log entry on every `/api/mmingest/*` hit (success or 403). |
| `api/main.py` | No change needed — `APIKeyMiddleware` is already registered; you're extending it in place, not replacing it. |

## Files to leave alone (do not touch)

- `api/middleware/rate_limit.py` — out of scope.
- All `api/services/mmingest/*` — S1B/S2's surface, frozen for this sprint.
- All Alembic migrations below 018 — frozen.
- `api/services/airtable.py` — Sprint 3B's territory.
- `api/routers/mmingest.py` (DOES NOT YET EXIST — Sprint 3B creates it) — don't touch.

---

## Critical rules

### 1. Back-compat is mandatory.

The existing shared-key auth path (`CARDIGAN_API_KEY` env var → `X-API-Key` header equality check) MUST keep working. Existing Cardigan endpoints (`/api/jobs`, `/api/queue`, `/api/config`, etc.) cannot break. The order of checks matters:

```python
provided = request.headers.get("X-API-Key", "")

# Existing shared-key path — unchanged behavior
if provided == shared_key:
    # Audit log entry for /api/mmingest/* hits, outcome='shared_key'
    return await call_next(request)

# New consumer-key path
consumer = await lookup_consumer_key(provided)
if consumer is not None:
    # Scope check for /api/mmingest/* paths
    if path.startswith("/api/mmingest/"):
        required = _required_scope_for(path, method=request.method)
        if required not in consumer.scopes:
            # Audit log entry, outcome='denied'
            return JSONResponse(403, ...)
    # Attach state, audit, proceed
    request.state.consumer_id = consumer.id
    request.state.consumer_scopes = consumer.scopes
    await touch_last_used(consumer.id)
    return await call_next(request)

return JSONResponse(401, ...)
```

Test back-compat explicitly: an existing endpoint (`/api/jobs`) with the shared key in `X-API-Key` returns 200 just like it did before this sprint.

### 2. Hash keys at rest with bcrypt (or argon2).

Never store plaintext. The `create_consumer_key()` flow:

```python
plaintext = secrets.token_urlsafe(32)
key_hash = bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt()).decode()
# INSERT INTO consumer_keys (key_hash, label, scopes) VALUES (...)
# return plaintext (to operator, ONCE), and consumer_id
```

The CLI helper prints plaintext to stdout exactly once. After that, the operator has the only copy.

For lookup, you have a choice: (a) iterate all rows and bcrypt-compare each (acceptable at ~10s of keys, slow at thousands); (b) use a fast-pre-filter prefix or HMAC for indexed lookup, then bcrypt-verify the match. **Use (a) for now** — the consumer key population will be small (PMM + clip-finder + a few internal scripts). Document the choice in `consumer_keys.py` with a comment that switching to (b) is a future optimization.

`bcrypt` is already a `cardigan-v4` dependency (check `pyproject.toml`); if not, surface to Mark before adding.

### 3. Scope vocabulary.

The two scopes in scope for this sprint:

- `mmingest:read` — required for `GET /api/mmingest/search`, `GET /api/mmingest/assets/*`, `GET /api/mmingest/recent`
- `mmingest:stream` — required for `GET /api/mmingest/assets/{media_id}/stream` (Phase 2; the endpoint doesn't exist yet but enforce the scope check anyway so Phase 2 is wired in)

`consumer_keys.scopes` is a CSV TEXT column. Parse on read into a `frozenset[str]`. Store as comma-separated. Wildcards are out of scope — exact string match only.

### 4. Audit log shape.

```sql
CREATE TABLE mmingest_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    consumer_id INTEGER,  -- nullable: NULL for shared-key callers
    path TEXT NOT NULL,
    media_id TEXT,  -- nullable: only populated for /assets/{media_id}/* paths
    ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    outcome TEXT NOT NULL,  -- 'allowed' | 'denied' | 'shared_key'
    FOREIGN KEY (consumer_id) REFERENCES consumer_keys(id)
);
CREATE INDEX idx_mmingest_audit_log_consumer_id ON mmingest_audit_log(consumer_id);
CREATE INDEX idx_mmingest_audit_log_ts ON mmingest_audit_log(ts);
```

Extract `media_id` from the path by regex when applicable (`/api/mmingest/assets/{media_id}` and `/api/mmingest/assets/{media_id}/...`). Surface a helper in `audit_log.py` for the extraction so the middleware stays clean.

The audit log fires for `/api/mmingest/*` paths only. Non-mmingest paths (existing Cardigan endpoints) do NOT write audit entries; that's out of scope. The middleware should be cheap to skip when path doesn't match.

### 5. The middleware is the ONLY scope enforcement point.

Sprint 3B's router (`api/routers/mmingest.py`) will trust `request.state.consumer_id` and `request.state.consumer_scopes` if they're set. It will NOT re-check scopes inside endpoint handlers. Keep the contract clean: middleware authenticates + authorizes; routers handle business logic.

S3B's brief tells the parallel drone to leave a stub `_require_scope("mmingest:read")` that's a no-op now — once your middleware lands, that stub becomes redundant. Don't worry about S3B's stub; it'll be reconciled at merge time.

### 6. Touch `last_used_at` atomically.

After successful consumer-key auth, update `consumer_keys.last_used_at = CURRENT_TIMESTAMP` for that row. Do this asynchronously (fire-and-forget if possible) so the auth path stays fast. A small lag in `last_used_at` (e.g., a few seconds) is acceptable. Don't block the request on the UPDATE.

### 7. Lint stack is HARD rule.

CI runs both `black --check` and `ruff check`. Run both locally before pushing:

```bash
black .
ruff check --fix .
black --check .   # confirm clean
ruff check .      # confirm clean
```

S-1, S1A, S1B, and S2 all got bitten on this — don't be the fifth.

### 8. Don't merge. Surface for Mark's gate.

Open the PR. Run yourself through the verification gates below. Surface to the conductor in the standard format. Do NOT click merge.

---

## Verification gates (must all pass before opening the PR)

1. **`alembic upgrade head`** on a fresh dev DB → migration 018 lands cleanly. `PRAGMA integrity_check` clean. Then `alembic downgrade 017` removes `mmingest_audit_log` cleanly. Then re-upgrade. Round-trip clean.

2. **Back-compat smoke (CRITICAL):** with `CARDIGAN_API_KEY` set, `GET /api/jobs` with `X-API-Key: <shared-key>` returns 200, same as before this sprint. With wrong key → 401. With no key → 401. No 5xx anywhere.

3. **Consumer-key happy path:** Create a key via the CLI helper with scopes `mmingest:read`. `GET /api/mmingest/search?q=test` with `X-API-Key: <consumer-key>` returns 200. (The endpoint may not exist yet if S3B hasn't merged — assert against `/api/mmingest/recent` or whatever placeholder route gets through the middleware; if S3B's router isn't merged at PR-open time, assert against a mocked router fixture instead. Surface to the conductor if you can't construct a valid test.)

4. **Scope enforcement:** Create a key with scopes `mmingest:stream` only. `GET /api/mmingest/search` → 403. Create a key with scopes `mmingest:read` only. `GET /api/mmingest/assets/{id}/stream` → 403 (even though endpoint doesn't exist yet, the middleware decision fires before routing).

5. **Audit log:** after each of the above mmingest hits, `SELECT * FROM mmingest_audit_log` shows a row with the expected `consumer_id`, `path`, `outcome` (`allowed` / `denied` / `shared_key`), and `media_id` extracted when applicable.

6. **`last_used_at` updates:** after a successful consumer-key auth, `last_used_at` on that row is no longer NULL and is within a few seconds of now.

7. **CLI helper happy path:** `python scripts/create_consumer_key.py --label "test" --scopes mmingest:read` prints a plaintext key to stdout, inserts a row, exits 0. Re-running with `--list` (you may add this flag) shows the row WITHOUT the plaintext.

8. **No regression on S-1's batched upsert:** `pytest tests/test_ingest_scanner.py::TestBatchedUpsert` — 3/3 pass.

9. **No regression on S1A's parity tests:** `pytest tests/api/test_mmingest_parity.py` — 11/11 pass.

10. **No regression on S1B's tests:** `pytest tests/services/mmingest/` — all green.

11. **No regression on S2's integration tests:** `pytest tests/integration/test_mmingest_index.py` — 8/8 pass.

12. **Lint stack:** `black --check .` and `ruff check .` both clean on the whole project.

---

## What to do when you finish

1. Open the PR against `mriechers/cardigan` `main` from branch `sprint-3a/consumer-keys-scoped-auth`.
2. PR title: `feat(auth): Sprint 3A — consumer keys + scoped auth + audit log`.
3. PR body must include:
   - Summary linking back to the plan and this handoff doc.
   - Verification gates as a checklist (each must pass before submitting).
   - A "Back-compat" section explicitly confirming shared-key auth still works for non-mmingest paths.
   - A "Scope-vocabulary growth" section noting the path for future scopes (string match, CSV column, no wildcards).
   - Reference issues #182, #183, #184 as tracked-but-not-this-PR's-job.
   - Note the bcrypt-iterate lookup choice and the future optimization path (HMAC prefix index).
4. Mark will not merge until a separate `pr-review-toolkit:review-pr` (or `code-reviewer`) agent has run a full pass and posted findings.

---

## Coordination with the parallel S3B drone

S3B is building `api/routers/mmingest.py` at the same time. **You will not touch S3B's files; S3B will not touch yours.** Two coordination points to be aware of:

1. **Scope-check stub:** S3B may add a stub `_require_scope("mmingest:read")` decorator/dependency that's a no-op now. Once your middleware lands, that stub becomes redundant; resolution happens at merge time (whichever lands second cleans it up, or Mark cleans up in a follow-up).
2. **Audit log writes from the middleware:** S3B's endpoint code does NOT write audit log entries — that's middleware territory. If S3B's brief calls for it (it shouldn't), surface to the conductor.

**If you finish before S3B:** open the PR anyway. Don't wait. Your work is parallel-safe.

---

## Commit attribution

```
feat(auth): <subject>

[Agent: the-drone]

<body>

Agent: the-drone
Machine: <hostname>

Co-Authored-By: Claude <noreply@anthropic.com>
```

No emojis in commit messages.

---

## Reference docs (read before starting)

- `/Users/mriechers/.claude/plans/anyway-the-reason-i-noble-whistle.md` — full plan, Sprint 3A section especially
- `mriechers/cardigan` branch `main` at `1b9ae61` — your start state
- `api/middleware/auth.py` — current shared-key middleware (you extend in place)
- `api/middleware/rate_limit.py` — middleware pattern reference
- `alembic/versions/017_add_consumer_keys.py` — the schema you build against
- `api/services/database.py` — async session/engine factories
- `api/main.py` — middleware registration (no change needed)
- `~/Developer/the-lodge/conventions/COMMIT_CONVENTIONS.md` — attribution format
- Memory: `feedback_cardigan_lint_stack` — black + ruff both in CI

---

## Active issues you should be aware of (not your job to fix)

- **#182** — parser sync between cardigan-v4 and pbswi Sprint 0 Media ID skill (unrelated to auth; FYI)
- **#183** — TokenBucket burst tuning (unrelated to auth; FYI)
- **#184** — unknown-variant-tag log level (unrelated to auth; FYI)

---

## If you get stuck

- **`bcrypt` not in `pyproject.toml`** — surface to Mark before adding it. (Likely it's already there; verify first.)
- **CSV scopes feel hacky** — they are, but they match the S1A schema decision. Don't add a join table; don't add JSON. CSV TEXT with `frozenset[str]` deserialization is the contract.
- **The middleware ordering with rate_limit feels wrong** — look at `api/main.py` to see registration order; preserve it.
- **You're tempted to refactor `APIKeyMiddleware` into a class hierarchy** — don't. Extend in place. Sprint 4 may grow it; this sprint keeps the diff surgical.
- **Real blocker** — STOP and report. A broken Sprint 3A blocks S4's MCP tools (they'll need scoped auth too).

Good hunting. — the-conductor
