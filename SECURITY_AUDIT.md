# Security Audit Report

**Date:** 2026-02-06
**Scope:** Full codebase security review of Editorial Assistant v3.0
**Auditor:** Claude Code (automated)

---

## Executive Summary

This audit reviewed the entire codebase (~108 source files, ~8,200 lines of Python service code plus React frontend) for security vulnerabilities. **7 issues were found and fixed**, and **4 advisory findings** are documented below for future consideration.

---

## Fixed Vulnerabilities

### 1. CRITICAL: Path Traversal in File Upload

**File:** `api/routers/upload.py:117`
**Risk:** An attacker could upload a file with a crafted filename (e.g., `../../etc/cron.d/backdoor`) to write files outside the `transcripts/` directory, potentially achieving remote code execution.

**Root cause:** `file.filename` from the HTTP multipart upload was used directly in `TRANSCRIPTS_DIR / file.filename` without any sanitization.

**Fix:** Added `sanitize_upload_filename()` that:
- Strips directory components using `PurePosixPath.name`
- Removes null bytes, `..` sequences, and leading dots
- Restricts to safe characters (alphanumeric, hyphens, underscores, spaces, dots)
- Added a belt-and-suspenders `is_relative_to()` check on the resolved path

### 2. HIGH: Command Injection via `shell=True`

**File:** `api/routers/system.py:91`
**Risk:** `subprocess.Popen(command, shell=True, ...)` interprets the command through a shell. While the commands were hardcoded in this version, this pattern is dangerous — any future change that introduces user input into the command string would enable remote code execution.

**Fix:** Replaced with:
- An `ALLOWED_COMPONENTS` allowlist that maps component keys to pre-defined command lists
- `subprocess.Popen(command_list, ...)` (list form, no `shell=True`)
- Only the two known components (`run_worker.py`, `watch_transcripts.py`) can be started

### 3. HIGH: Server-Side Request Forgery (SSRF) in Ingest Scanner

**File:** `api/routers/ingest.py:266-316`
**Risk:** The `/api/ingest/scan` endpoint accepted a `base_url` query parameter that was passed directly to `IngestScanner`, which makes HTTP requests to that URL. An attacker could supply internal network addresses (e.g., `http://169.254.169.254/` for cloud metadata, `http://localhost:8000/api/...` for internal APIs) to probe the internal network.

**Fix:** Added an `ALLOWED_INGEST_HOSTS` allowlist (`mmingest.pbswi.wisc.edu`) and URL parsing validation. Requests to any other host are rejected with a 400 error.

### 4. MEDIUM: Error Message Information Disclosure

**Files:** `api/routers/chat_prototype.py:96`, `api/routers/ingest.py:316,545,572`, `api/routers/upload.py:184`
**Risk:** Exception details (including internal paths, stack traces, database errors, and API error messages) were passed directly to HTTP responses via `detail=str(e)`. This leaks implementation details that help attackers understand the system.

**Fix:** Replaced all `detail=str(e)` patterns with generic error messages. Full exception details are still logged server-side for debugging.

### 5. MEDIUM: Airtable Formula Injection

**Files:** `api/services/airtable.py:103,158`, `mcp_server/server.py:324`
**Risk:** Media ID values were interpolated directly into Airtable `filterByFormula` strings without escaping. A media ID containing `'` could alter the formula logic. While this is a read-only query, it could still be used to extract unintended data.

**Fix:** Added `_escape_airtable_formula_value()` that escapes backslashes and single quotes before interpolation into formula strings. Applied to both single and batch lookup methods.

### 6. MEDIUM: Gemini API Key Exposed in URL

**File:** `api/services/llm.py:853`
**Risk:** The Gemini API key was passed as a URL query parameter (`?key=<api_key>`). URL query parameters appear in:
- HTTP server access logs
- Proxy logs
- Error messages and stack traces
- Browser history (if applicable)
- Network monitoring tools

**Fix:** Moved the API key to the `x-goog-api-key` HTTP header, which is Google's supported authentication header for the Gemini API.

### 7. MEDIUM: Missing Security Headers and Overly Permissive CORS

**File:** `api/main.py:83-94`
**Risk:**
- `allow_methods=["*"]` and `allow_headers=["*"]` reduced the effectiveness of CORS protections
- No security headers were set (X-Content-Type-Options, X-Frame-Options, etc.)

**Fix:**
- Tightened CORS to explicit method and header lists
- Added security headers middleware: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `X-XSS-Protection`, `Referrer-Policy`, `Permissions-Policy`

---

## Advisory Findings (Not Fixed — Require Architecture Decisions)

### A1. HIGH: No Authentication or Authorization

**Impact:** The entire API is unauthenticated. All endpoints — including system management (restart/stop workers), configuration changes, file uploads, and chat (which costs money via LLM calls) — are accessible to anyone who can reach the server.

**Context:** The API is exposed to the internet via Cloudflare Tunnel (`config/cloudflared.yml`). While Cloudflare Access can provide authentication at the tunnel level, no application-level auth exists as a defense-in-depth measure.

**Recommendation:** Implement at least one of:
1. **Cloudflare Access** (zero-trust) with JWT validation in the API
2. **API key authentication** middleware for all non-health endpoints
3. **OAuth2/OIDC** if multi-user access is planned

Priority: System management endpoints (`/api/system/*`) should be protected first.

### A2. MEDIUM: No Rate Limiting

**Impact:** No rate limiting on any endpoint. An attacker could:
- Exhaust LLM API budgets via rapid chat messages
- Cause denial of service via upload flooding
- Trigger excessive ingest server scanning

**Recommendation:** Add `slowapi` or similar rate limiting middleware. Critical endpoints to protect first: `/api/chat/message`, `/api/upload/transcripts`, `/api/ingest/scan`.

### A3. MEDIUM: WebSocket Without Authentication

**File:** `api/routers/websocket.py:101`
**Impact:** The WebSocket endpoint accepts all connections without authentication. Anyone can connect and receive real-time job status updates.

**Recommendation:** Add token-based authentication to WebSocket connections (e.g., via query parameter token validated against a session).

### A4. LOW: Hardcoded sys.path for Shared Module

**Files:** `api/main.py:13`, `api/services/airtable.py:17`, `mcp_server/server.py:40`
**Impact:** `sys.path.insert(0, str(Path.home() / "Developer/the-lodge/scripts"))` inserts a hardcoded external path. If this directory is writable by another user or process, it could be used to inject malicious code via a fake `keychain_secrets.py`.

**Recommendation:** Use environment variables or a proper package installation (`pip install -e ../the-lodge`) instead of sys.path manipulation.

---

## Positive Security Observations

The codebase has several good security practices already in place:

1. **Airtable write protection** - Strict read-only policy with a single controlled exception for screengrab attachment (additive-only, audit-logged, idempotent)
2. **LLM cost caps** - Run cost caps, per-token cost limits, and model allowlists prevent runaway spending
3. **Path traversal checks in MCP server** - `mcp_server/server.py:1122-1124` correctly uses `resolve().relative_to()` to validate file paths
4. **Output file allowlist** - `api/routers/jobs.py:348-379` uses both an extension allowlist and `is_relative_to` check
5. **Parameterized SQL queries** - All database queries use SQLAlchemy's parameterized `text()` with `:param` binding, preventing SQL injection
6. **Pydantic validation** - Request models use Pydantic for type validation and constraints
7. **Secrets via Keychain** - Production secrets are stored in macOS Keychain, not in code or `.env` files
8. **`.gitignore` coverage** - `.env`, `dashboard.db`, `transcripts/`, `OUTPUT/`, and `logs/` are all gitignored

---

## Files Modified

| File | Changes |
|------|---------|
| `api/routers/upload.py` | Added `sanitize_upload_filename()`, path traversal protection, generic error messages |
| `api/routers/system.py` | Replaced `shell=True` with allowlist + list-form subprocess |
| `api/routers/chat_prototype.py` | Generic error messages instead of `str(e)` |
| `api/routers/ingest.py` | SSRF host allowlist, generic error messages |
| `api/services/airtable.py` | Formula value escaping for Airtable queries |
| `api/services/llm.py` | Gemini API key moved from URL to header |
| `api/main.py` | Security headers middleware, tightened CORS methods/headers |
| `mcp_server/server.py` | Formula value escaping for Airtable query |
