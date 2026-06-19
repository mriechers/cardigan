# Legacy Inventory — v4.2 maintenance (Epic M discovery)

**Date:** 2026-06-19 · **Tracks:** Epic M (#234) · **Purpose:** evidence base
for the Phase-2 legacy-reduction series (Sprints 10–12). This is a discovery
pass, not a removal mandate. **Governing principle: this codebase has no
dead-code corpses (zero `DEPRECATED` markers in active code) — simplification
here is *consolidation*, not *deletion*. Every cut must trace to a live
replacement first.**

## 1. Two parallel ingest systems (HEADLINE — gated)

Two independent subsystems, each with its **own scheduler**:

| System | Files | Purpose |
|--------|-------|---------|
| **Legacy ingest** | `api/services/ingest_scanner.py`, `ingest_scheduler.py`, `ingest_config.py`, `api/routers/ingest.py`, `api/models/ingest.py` | Transcript discovery for the SEO pipeline (Sprint-11 era) |
| **mmingest** | `api/services/mmingest/{crawler,indexer,parsers,scheduler,sidecar_fetcher}.py`, `api/routers/mmingest.py`, `api/models/mmingest.py` | Caption sidecar corpus for full-text search (Sprints 1A–5) |

They appear **separate-purpose** (transcript discovery vs caption search), so
this is **not** a "retire one" — but #218 now wants the legacy scanner on a
cron, and mmingest already has a scheduler. **Sprint 10 candidate:** unify the
*scheduling mechanism* (one scheduler abstraction) even if the two pipelines
stay distinct. **Hard gate:** confirm the separation-of-purpose before any
shared-abstraction work; do not delete either system on assumption. If they
turn out to overlap, this becomes a larger consolidation than scoped.

## 2. Preset-tier abstraction → dead-weight after Epic L

`phase_backends` and the cheapskate/default/big-brain ladder: **15 references
in `api/`**. Per #69, `phase_backends` is already effectively fallback-only.
Once **Epic L (#233)** lands direct per-phase model selection, the abstraction
and its config keys become removable. **Sprint 11 candidate** (strictly after
Epic L ships its replacement).

## 3. Documentation / planning sprawl

| Location | Count | Note |
|----------|-------|------|
| `planning/*.md` (top level) | 21 | Many are completed sprint plans / handoffs — archival candidates |
| `planning/archive/` | 6 | Already archived |
| `planning/sprints/archive/` | 3 | Already archived |
| `docs/deprecated/` | 4 | Already consolidated (v2-era instructions, MCP decommission plan) |

**Sprint 12 candidate:** sweep the 21 top-level `planning/` docs — move
completed/superseded ones into `planning/archive/`, reconcile agent-reference
drift (#83). Also two untracked local drafts (`planning/2026-06-10-it-hosting-ask.md`,
`planning/sprint-3b-handoff.md`) — decide tracked vs local.

## 4. MCP server decommission (in flight)

`mcp_server/` is still present (**2,526 LOC**). #114 references an "MCP server
decommission + skill-based architecture migration." **Status unclear** — if the
skill-based architecture has replaced it, `mcp_server/` is a large retirement
candidate; if still consumed, it stays. **Action:** confirm consumers before
any removal (this is the #1 "trace to a live replacement" case). Tracked under
**Epic J (#231)** investigation + flagged here for Phase 2.

## 5. Small dead config (low-risk, high-confidence)

- **Watchtower label remnant** — `docker-compose.prod.yml` still carries
  `com.centurylinklabs.watchtower.enable=true`, but #216 replaced Watchtower
  with push-based deploy. Dead label. *(Left in the v4.2.0 release PR to keep
  scope tight; bundle with #180 LXC-compose reconciliation.)*
- **Stock nginx default.conf** (#181) — `nginx.conf` present; #181 flags a dead
  stock default in the web image. Verify and remove.
- **`__pycache__/*.pyc`** — ✅ already untracked in #207 (v4.2.0 release).

## 6. Component duplication (UI)

- `Help.tsx` → shared `ProseContainer` (#111) — one of several places where a
  shared component already exists but isn't adopted. Consolidation, not deletion.

---

### Phase-2 sprint mapping

- **Sprint 10** — ingest scheduler unification (§1, gated).
- **Sprint 11** — preset-tier dead-weight removal (§2, after Epic L) + small
  dead config (§5) + UI component consolidation (§6).
- **Sprint 12** — doc/archive consolidation (§3) + MCP decommission decision (§4).

Each removal logged with its live-replacement justification. No silent
truncation: anything deferred for safety is noted, not dropped.
