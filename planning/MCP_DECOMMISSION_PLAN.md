# MCP Server Decommission + Skill-Based Architecture Migration

**Status:** Planning — not yet started
**Author:** Mark + Claude Code (2026-05-29 design conversation)
**Related issues:** #113, #114, #164
**Related deprecated artifacts:** `docs/deprecated/INDEX.md`

## Goal

Replace cardigan's MCP server (`mcp_server/server.py`, 2,174 lines + 16 tools + 6 prompts) with a Claude Code-native architecture:

- **API expansion** to cover the gaps the MCP server fills today (SST propose/review/commit workflow, revision auto-versioning, OUTPUT file operations)
- **Skill family** (`cardigan-{api,edit,process,load,seo}`) wrapping the API
- **`cardigan-shepherd` agent** at workspace level, modeled on `pbswi-auditor` — owns pipeline orchestration across skills, not just editing
- **Tailscale tailnet** as the network trust substrate; no Cloudflare tunnel required for cardigan

## Decisions captured (from design conversation)

| Decision | Choice | Rationale |
|---|---|---|
| Concurrency-check snapshot on re-propose | Refresh snapshot (Q1-A) | Matches editor agent's "re-fetch when user updated AirTable" mental model; second propose would conflict spuriously otherwise |
| Stale staged-edits behavior | Warn at ≥24h, no hard TTL (Q2-B) | Preserves "staged edits = durable recovery state" contract; gives agent a chance to walk through old edits with user |
| Skill granularity | Multiple focused skills (Q3-B) | Mirrors `pbswi-auditor`'s family pattern; agent persona owns triage, skills own capabilities |
| Migration choreography | Clean cutover, no parallel period (Q4-B) | Single-user system; cost of two-source-of-truth period exceeds cost of one-day cutover friction |
| Network trust model | Tailscale tailnet | Lower complexity than Cloudflare Access for 3 users total; no public endpoint; no per-user service tokens for skills |
| Editorial rules location | Stay in `claude-desktop-project/EDITOR_AGENT_INSTRUCTIONS.md` | Agent references them; not in scope to reorganize the rules themselves |
| MCP server code | Deprecate, do not delete | Future Claude Desktop revival is on the table; cost of keeping is zero |
| Knowledge PDFs | Not re-copied to deprecated/ | Already duplicated at `claude-desktop-project/knowledge/`; cross-referenced from INDEX.md instead |

## Out of scope (future enhancements)

- **Claude Desktop revival.** Skill architecture is auth-agnostic; if Desktop becomes desirable later, the MCP server in deprecated state is the starting point.
- **UW campus VPS deployment.** When cardigan moves off the homelab to a VPS hosted on UW Madison infrastructure — see the dedicated "Future roadmap" section below for the questions to think about ahead of time.
- **Editorial rules reorganization.** The 977-line `EDITOR_AGENT_INSTRUCTIONS.md` could be split into program-rule files; deferred — not load-bearing for the migration.
- **PR review of `cardigan-shepherd`'s cascade patterns against `pbswi-auditor`'s lessons.** Once the agent is in use, audit whether the orchestration pattern transfers cleanly. File issues as discovered.

---

## Prerequisites

- **Tailscale install on homelab.** Per [[homelab-actual-state]] memory (2026-05-22), Tailscale is not yet installed. Need to:
  1. Install `tailscaled` on the host running cardigan API (Proxmox LXC or VM — TBD per homelab provisioning convention)
  2. Run `tailscale up`, authenticate with Google account
  3. Note the MagicDNS hostname (e.g., `cardigan.tail-XXXXXX.ts.net`)
  4. Configure tailnet ACL to allow the 3 users' devices to reach port 8100 on cardigan
- **Tailscale install on 2 collaborator laptops.** One-time per user; ~5 min each.
- **Verify Docker-deployed-era job history is preserved** (issue #164) before PR 2 lands — not blocking, but the cross-source analysis benefits from having it. (Note: Docker was technically v4 of cardigan; the current `cardigan-v4/` codebase is the same v4 architecture without the Docker wrapper.)

---

## PR 1: API expansion

**Scope:** Purely additive — adds endpoints, models, services, and schema. No user-visible behavior change (MCP server still serves the editor workflow during PR 1's lifetime, unchanged). Verify via curl after merge.

**Estimated size:** ~600-800 lines including tests.

### Files to add

| File | Purpose |
|---|---|
| `api/services/airtable_writer.py` | `AirtableSstWriter` class — PATCH records, post audit comments. Houses `WRITABLE_FIELDS` allowlist (moved from `mcp_server/server.py`). |
| `api/models/sst.py` | Pydantic models: `ProposeEditRequest`, `ProposedEdit`, `ProposedEditsResponse`, `FieldChange`, `ConflictDetail`, `CommitResponse`. |
| `api/routers/sst.py` | FastAPI router for `/sst/*` endpoints. |
| `tests/test_airtable_writer.py` | Unit tests for writer service. |
| `tests/test_sst_router.py` | Endpoint tests (mocked AirTable). |
| `tests/test_proposed_edits_db.py` | DB-level tests for the new tables. |

### Files to modify

| File | Change |
|---|---|
| `api/services/database.py` | Add `proposed_sst_edits_table` + `sst_commit_audit_table` (migration 008). Import `UniqueConstraint` from sqlalchemy. |
| `api/main.py` | Mount `sst.router` at `/api/sst`. |
| `mcp_server/server.py` | Replace local `WRITABLE_FIELDS` with `from api.services.airtable_writer import WRITABLE_FIELDS`. This is the ONLY behavior-affecting change in PR 1 — no functional difference (same dict, same source). Prevents drift during PR 1's window. |

### Endpoint catalog

All under `/api/sst/`. Tailscale provides network-level access control; **no FastAPI auth middleware** on these endpoints (relies on tailnet membership).

| Method | Path | Purpose |
|---|---|---|
| GET | `/{media_id}/metadata` | Fetch current SST fields from AirTable, keyed by media_id |
| POST | `/{media_id}/proposed-edits` | Stage a single-field edit (UPSERT — Q1-A semantics) |
| GET | `/{media_id}/proposed-edits` | List all staged edits; includes `staged_at`, `age_hours`, stale warning ≥24h (Q2-B) |
| DELETE | `/{media_id}/proposed-edits/{field}` | Remove a single staged edit |
| DELETE | `/{media_id}/proposed-edits` | Clear all staged edits for a media_id |
| POST | `/{media_id}/proposed-edits/commit` | Apply staged edits with concurrency check + audit comment + audit-log row |

### SQLite schema (migration 008)

Two tables, additive — no changes to existing tables.

`proposed_sst_edits`:
- `id` PK, `media_id` (indexed), `airtable_record_id`, `field_key`, `airtable_column`, `current_value_snapshot`, `proposed_value`, `reason`, `staged_at`, `staged_by`, `app_version`
- `UNIQUE (media_id, field_key)` — re-proposing same field is UPSERT

`sst_commit_audit`:
- `id` PK, `media_id` (indexed), `airtable_record_id`, `committed_at`, `committed_by`, `outcome` (`"success"` | `"conflict"` | `"airtable_error"` | `"limit_exceeded"`), `fields_json`, `conflict_details_json`, `airtable_comment_posted`, `error_message`, `app_version`

Full SQLAlchemy `Table(...)` definitions in the design conversation; replicate verbatim into `database.py`.

### Rate limiting

Apply `slowapi` rate limits (already imported in `api/middleware/rate_limit.py`):
- `POST /sst/{media_id}/proposed-edits/commit` — **10/hour** per IP. Bounds damage if anything goes sideways.
- `POST /sst/{media_id}/proposed-edits` — **60/hour** per IP. Generous; staging is cheap.
- Read endpoints — inherit existing defaults.

Justification: even though Tailscale removes the public-endpoint concern, rate limits are defense-in-depth against runaway loops in agent code.

### Tests

- `AirtableSstWriter.patch_record`: success + AirTable error response + network failure paths (mocked httpx)
- `AirtableSstWriter.post_comment`: success + comment failure paths
- `WRITABLE_FIELDS` allowlist: ensures only the 8 expected fields are present, no `media_id`/`status`/etc.
- DB layer: UPSERT semantics, UNIQUE constraint, audit row creation
- Endpoint tests: 404 on unknown media_id, 400 on disallowed field, 409 on concurrency conflict, 200 on success path
- Stale-warning: review endpoint includes `stale: true` + correct `age_hours` for ≥24h staged edits

### Pre-merge checklist

- [ ] `pytest` passes (including new test files)
- [ ] `ruff check` clean
- [ ] Verify via `curl` (with `TAILSCALE` ACL allowing localhost) that:
  - GET `/api/sst/{known_media_id}/metadata` returns AirTable data
  - POST `/api/sst/{known_media_id}/proposed-edits` with valid body stages an edit and returns 201
  - GET `/api/sst/{known_media_id}/proposed-edits` shows the staged edit
  - Re-POSTing the same field overwrites (Q1-A behavior)
  - POST `/api/sst/{known_media_id}/proposed-edits/commit` writes to AirTable, posts audit comment, returns 200
  - Concurrency conflict path: edit AirTable manually between propose and commit, verify commit returns 409
- [ ] MCP server still works end-to-end (imports the shared `WRITABLE_FIELDS` correctly)
- [ ] Commit message tags: `[Agent: Main Assistant]` per workspace convention

---

## PR 2: Skills + agent + MCP decommission

**Scope:** The cutover. After this PR merges, the editor workflow runs through the new skill+agent architecture; the MCP server is no longer running.

**Estimated size:** ~1500-2000 lines including the agent definition and 5 skills. Documentation-heavy.

### Files to add

#### Skills (in `cardigan-v4/.claude/skills/`)

| Skill dir | `SKILL.md` purpose |
|---|---|
| `cardigan-api/` | **Reference skill (prereq for the others).** Tailnet base URL resolution, endpoint catalog, error patterns, common request shapes. No tailored auth — relies on tailnet membership. |
| `cardigan-edit/` | SST propose/review/commit workflow, character-limit validation, stale-edit handling. Maps to MCP tools: `propose_sst_edit`, `review_proposed_edits`, `commit_sst_edits`, `validate_copy`, `get_sst_metadata`. |
| `cardigan-process/` | Submit transcript, retry phases, check queue/job status. Maps to: `submit_processing_job`, plus existing queue/job endpoints. |
| `cardigan-load/` | Project context loading, transcript fetching, file listing, output reading. Maps to: `load_project_for_editing`, `get_formatted_transcript`, `list_project_files`, `list_revisions`, `read_project_file`, `list_processed_projects`, `search_projects`, `get_project_summary`. |
| `cardigan-seo/` | SEMRush analysis, keyword reports, social/hashtag fields. Maps to: Phase 3 in current editor instructions. |

Each skill has a `SKILL.md` with frontmatter (`name`, `description`, trigger conditions), a brief overview, and the operational details (endpoint shapes, response handling, gotchas). Pattern matches `audit-pipeline`/`audit-platforms` in `pbswi/.claude/skills/`.

#### Agent (in `pbswi/.claude/agents/`)

`cardigan-shepherd.md` — workspace-level agent definition. Mirrors `pbswi-auditor`'s shape:
- Frontmatter: `name`, `description` with `<example>` blocks, `model: opus`, `color`, routing metadata (`tier`, `domains`, `capabilities`, `delegates_to`, `receives_from`)
- **Persona section:** warm/cardigan voice from existing editor instructions, plus broader pipeline-shepherd framing
- **Triage rules:** when does the shepherd invoke which skill? Decision tree similar to pbswi-auditor's
- **Cascade patterns:** the unique value-add — e.g., "user says 'work on X' → load + edit + analyze in sequence"
- **Anti-hallucination guardrails:** lifted from current editor instructions (tool verification, "never fabricate" warnings)
- **What this agent does NOT do:** boundaries (no direct AirTable MCP calls; defer to `cardigan-edit` skill; etc.)

#### Deprecation artifacts

| File | Purpose |
|---|---|
| `mcp_server/DEPRECATED.md` | Header explaining decommission. Notes: what replaced it (skills + agent + API), what would need to change to re-enable (Claude Desktop revival path), reference to deprecated INDEX.md. |
| Update `docs/deprecated/INDEX.md` | Fill in the "MCP server (decommissioned)" section with pointer to `mcp_server/`. |
| Update `claude-desktop-project/EDITOR_AGENT_INSTRUCTIONS.md` | Add deprecation header pointing to `cardigan-shepherd` agent + `cardigan-edit` skill. Body remains canonical for editorial rules. |

### Files to modify

| File | Change |
|---|---|
| `docker-compose.yml` (if MCP runs there) | Remove MCP server service. |
| `init.sh` (if it launches MCP) | Remove MCP startup. |
| `cardigan-v4/CLAUDE.md` | Update MCP references to point to the new architecture. Remove "9 tools + 6 prompts" claim from parent CLAUDE.md too (note: that line in workspace CLAUDE.md needs updating). |
| `cardigan-v4/.mcp.json` (if it references the local MCP server) | Remove MCP server entry. |

### Files NOT to modify

- `mcp_server/server.py` itself — leave the code intact for archival reference. Only add the `DEPRECATED.md` neighbor.
- `claude-desktop-project/EDITOR_AGENT_INSTRUCTIONS.md` body — only add deprecation header at top; preserve the canonical editorial rules in their current location.
- `ai-editorial-assistant/` sibling repo — already preserved, copies in `docs/deprecated/` are the cardigan-side references.

### Cardigan-shepherd agent triage rules (sketch)

Modeled on `pbswi-auditor`'s triage decision tree. Refine during implementation:

```
1. Does the user name a project (media_id) and say something edit-shaped?
   → cascade: cardigan-load (fetch context) → cardigan-edit (analyze SST + present options)

2. Does the user ask "what's ready to edit" / "what's processing" / discovery-shaped?
   → cardigan-load (list_processed_projects, list filtered by status)

3. Does the user provide a new transcript or SEMRush data?
   → cardigan-process (submit job) or cardigan-seo (SEMRush analysis)

4. Does the user request fact-checking?
   → cardigan-load (get_formatted_transcript) → assist with verification

5. Does the user ask about job status, queue, or retries?
   → cardigan-process (queue/job endpoints)

6. Ambiguous?
   → ask one tight clarifying question, don't guess (per pbswi-auditor pattern)
```

### Cascade patterns (the unique value-add)

These are what justify having an agent vs. just exposing skills directly:

- **Edit-session cascade:** user names a project → shepherd loads context → fetches SST → identifies issues (over-limit fields, factual issues) → presents options in chat. Without the agent, the user has to invoke each skill manually.
- **Submit-and-monitor cascade:** user provides a transcript → shepherd submits via `cardigan-process` → optionally polls status → once ready, offers to enter edit mode via `cardigan-edit`.
- **Fact-check-while-editing cascade:** during an edit session, if the user says "verify this quote," shepherd switches modes to fetch the formatted transcript via `cardigan-load`, verifies, returns to edit context.

These should be documented as concrete patterns in the agent's body, with examples.

### Pre-merge checklist

- [ ] All 5 skills have `SKILL.md` files that pass workspace skill conventions (frontmatter + body structure)
- [ ] `cardigan-shepherd.md` agent definition validated against `pbswi-auditor`'s structure
- [ ] Test the cardigan-shepherd agent against a real editing session — at least one full flow: discovery → load → edit → commit
- [ ] Verify tailnet access works from a 2nd device (collaborator's laptop, if available)
- [ ] MCP server stops running cleanly (no orphan processes)
- [ ] All deprecation headers are in place and link correctly
- [ ] Update `cardigan-v4/CLAUDE.md` and parent `pbswi/CLAUDE.md` to reflect the new architecture (no "9 tools + 6 prompts" references remain)
- [ ] Commit message tags: `[Agent: Main Assistant]`

---

## Post-cutover validation (one week of dogfooding)

After PR 2 merges, dogfood for ~1 week before declaring the migration complete:

- Run at least 3 real editorial sessions through the shepherd agent end-to-end
- Watch for: shepherd forgetting to call `review_proposed_edits` before commit (the MCP tool ordering used to enforce this — skill instructions need to be tight enough to prevent regression), stale-warning surfacing correctly, rate limits not triggering on normal use
- Check that `sst_commit_audit` table is populating with the expected rows per session
- File any agent-behavior issues with label `legacy-data` or `agent-discovered` for cross-reference

If serious regressions surface, the deprecated MCP server is one revert away — but the cost of NOT cutting over is "two architectures to maintain," so the bar for reverting is high. Treat it as a real bug fix instead.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Shepherd agent skips `review_proposed_edits` before commit | Tight skill instructions in `cardigan-edit/SKILL.md`; spell out the requirement and the failure mode. The MCP tool ordering used to enforce this implicitly. |
| Stale-warning not surfaced to user | Skill instructions mandate quoting the warning text from the API response. Verify in dogfooding. |
| Concurrency conflict path under-tested | PR 1 test suite covers it; verify manually during PR 1 curl checklist. |
| Tailscale install friction for 2nd/3rd users | Schedule a 15-min onboarding call with each; provide a written cheat-sheet. Not a code problem. |
| Editorial rules drift between `EDITOR_AGENT_INSTRUCTIONS.md` and shepherd agent | Shepherd agent references the rules document by path; doesn't duplicate them. If we later want to split rules into per-program files, that's a follow-up. |
| MCP server bit-rot once decommissioned | Acceptable — no SLA on the deprecated code. If Claude Desktop revival becomes real, expect to update before re-enabling. |

---

## Future roadmap: UW campus VPS deployment

**Status:** Future enhancement — not for current migration. Captured here so the questions are visible ahead of when they need answering.

**Context:** Cardigan currently runs on Mark's homelab; future deployment is likely on a VPS hosted on UW Madison campus infrastructure. WPM (Wisconsin Public Media) is part of UW Madison and already has institutional patterns for VPN-gated network access. WPM also has GPU hardware that could host local LLMs, opening a path away from full OpenRouter dependency.

**Versioning note:** The Docker-deployed era was technically v4 of cardigan. The current codebase in `cardigan-v4/` is the same v4 architecture, just no longer Docker-wrapped. What this migration produces (skill-based architecture + decommissioned MCP) is likely **v4.5 or v5** — to be tagged after a QA pass on the new deployment shape. The directory name `cardigan-v4/` stays unchanged regardless.

### What carries over unchanged

- The FastAPI app, SQLite schema, services layer, Pydantic models, AirTable integration
- The skill family (`cardigan-{api,edit,process,load,seo}`) — they're HTTP-only and re-target by changing the base URL
- `cardigan-shepherd` agent definition — markdown is portable
- Langfuse observability (externally hosted)
- The propose/review/commit workflow + WRITABLE_FIELDS allowlist

### What changes

| Concern | Homelab + Tailscale (current target) | UW VPS (future) |
|---|---|---|
| Network access control | Tailscale tailnet membership | Institutional UW VPN (UW Madison VPN client) |
| Tailscale's role | Required for all access | Likely **optional or superseded** — if users are already on the UW VPN to reach campus resources, layering Tailscale on top is redundant. Tailscale may still be useful for off-VPN access patterns; decide case-by-case. |
| Secrets management | macOS Keychain | TBD — see below |
| LLM backend | OpenRouter for all 4 phases (cheapskate/default/big-brain tiers) | Optionally local LLMs on WPM GPU hardware for some/all phases; see below |
| Backup / DR | Manual (homelab responsibility) | UW infra patterns or self-managed cron+rclone |
| On-call | Mark only | TBD — who responds when cardigan breaks during a publication deadline? |
| Deployment automation | scp / git pull on homelab | Real CI/CD pipeline (GitHub Actions → VPS) |

### Open questions to think about ahead of time

**1. Secrets management substrate.**

macOS Keychain doesn't exist on a Linux VPS. Options:

- **Environment variables loaded from a sealed file** at deploy time — simplest, works fine for a single VPS, ops responsibility is "don't commit the file"
- **HashiCorp Vault** or similar — overkill for cardigan's size, but if UW infra runs Vault already, low marginal cost to join
- **1Password Connect** or similar managed secret broker — paid, easy, good audit trail
- **UW-provided secrets infrastructure** — investigate what WPM/UW Madison patterns already exist; following the institution's convention is usually right
- **Inline encrypted with `sops` + age key** — git-trackable encrypted secrets, popular in Kubernetes shops; works without external dependencies

The right answer probably depends on what UW already runs. Worth asking the WPM infra contact before reaching for a new tool.

**2. Local LLM integration.**

WPM has GPU hardware suitable for hosting open-weight models. The questions:

- **Where does inference happen?** Three plausible shapes:
  - On the VPS itself (probably not — VPSes rarely have GPUs)
  - On a separate WPM-side LLM server, accessed over the local network
  - Hybrid: cardigan VPS calls the WPM LLM server for some phases (privacy-sensitive ones like the analyst phase running on real transcripts), OpenRouter for others (where capability matters more — synthesizing SEO metadata)
- **Which models?** Llama 3.x 70B, Qwen2.5 72B, Mistral Large, etc. — viable on a single A100 / H100 or 2x consumer GPUs. Pick based on benchmark performance against cardigan's 4-phase prompts; the existing Langfuse traces are the right testbed.
- **How does cardigan know which backend to use?** Extend `api/services/model_roster.py` and `model_roster.py`-driven config to add a local-LLM tier (e.g., `local-fast`, `local-capable`) alongside `cheapskate`/`default`/`big-brain`. Phase-level config already exists (per `docs/COST_DATA_VERSIONING.md`); add per-phase backend override.
- **Latency and reliability tradeoffs.** Local LLM is faster for short prompts, slower for long ones (no smart batching unless we add it). VPS↔LLM-server network is the new failure mode — what's the fallback if the LLM server is down? Fail-over to OpenRouter is probably right, with cost-alerting.
- **When is local LLM the right call?** Plausible defaults: privacy-sensitive content (raw transcripts before publication, especially for content under embargo) runs locally; metadata synthesis (descriptions, keywords) runs on OpenRouter for quality. The `model_roster.py` decision logic encodes this.

**3. Authentication for the API.**

If UW VPN already gates access, cardigan API may not need additional auth — same model as today's homelab (Tailscale provides identity, app trusts the network). But:

- **Audit logging** matters more in an institutional context. Per-user request attribution might be required by UW policy; this would mean wiring some IdP (Shibboleth? Google Workspace UW?) into the API.
- **The propose/commit allowlist remains the load-bearing capability constraint** regardless of network model. Don't regress it in deployment refactoring.

**4. CI/CD and deployment shape.**

- Container? Direct deployment? Systemd service?
- Single-VPS or multi-host (separate API / web / worker)?
- Database backups — managed PostgreSQL instead of SQLite, or stay on SQLite + cron-snapshot?
- Migration path from current SQLite schema if a DB switch is needed (Alembic exists in `reporter-tools/`, not yet in cardigan)

**5. Editor adoption pattern.**

If cardigan moves to VPS, the 2-3 collaborators no longer need Tailscale install — they just need UW VPN access (which they likely already have for other campus resources). That **reduces** onboarding friction relative to the Tailscale model. Worth keeping in mind: the homelab+Tailscale model is the cheapest answer for *now*; the VPS model becomes cheaper-per-user as the team grows.

### What this means for the current migration

**Nothing changes in PR 1 or PR 2.** The skill-based architecture is the right substrate for either deployment model. Document this section so the eventual VPS migration starts from clear questions, not blank-slate analysis.

**One thing worth doing during PR 2:** make sure the `cardigan-api` skill's base URL is configured via a single source of truth (env var, skill-level config) so retargeting from homelab tailnet to UW VPS hostname is a one-line change, not a skill-content edit.

---

## Open questions deferred to implementation

- **Where does the agent live in version control?** Workspace `pbswi/.claude/agents/` (per pbs-auditor precedent) seems right. Confirm during PR 2.
- **Should the 6 MCP prompts (`hello_neighbor`, `start_edit_session`, etc.) become slash-commands inside `cardigan-edit` skill, or just patterns inlined in skill body?** Defer to skill implementation — start with patterns inlined; only promote to slash-commands if there's a real invocation need.
- **Editorial rules deprecation header in `EDITOR_AGENT_INSTRUCTIONS.md` — what does it say?** Draft during PR 2; should clarify which sections are still canonical (editorial rules, program-specific guidance) vs. which are superseded (workflow instructions, tool descriptions).

---

## References

- Design conversation: 2026-05-29 (Mark + Claude Code)
- Pattern model: `pbswi/.claude/agents/pbswi-auditor.md`
- Decommissioned MCP source: `mcp_server/server.py`
- Current canonical editor instructions: `claude-desktop-project/EDITOR_AGENT_INSTRUCTIONS.md`
- Deprecated artifacts index: `docs/deprecated/INDEX.md`
- Legacy data analysis tracking: issue #114
- Docker-deployed-era job history reminder: issue #164
- Earlier fabrication fix: issue #113
