# Cardigan — Remaining Work (next-agent handoff)

**As of 2026-06-22.** This is the single entry point for picking up the v4.2
maintenance effort. It supersedes the *status* in
`planning/v4.2-maintenance-sprint-plan.md` (that doc's sprint sequencing is
still valid; this one has the current state). Epic L's deep plan lives in
`planning/epic-l-consolidation-plan.md`; the Phase-2 simplification in
`planning/LEGACY_INVENTORY.md`.

## Orientation

v4.2.0 is released. The backlog is organized as **13 epic issues (#222–#234)**,
each with native sub-issues. Six sprints' worth of work shipped (6 PRs merged,
~25 issues closed). The remaining work has hit a **mode boundary**: what's left
is either cold-start backend epics, browser-verified frontend, or a refactor
with a cost rewire — clustered below so you can pick the right one for your
session.

## ⚠️ Read before touching any issue

1. **Verify-already-fixed FIRST.** ~half of this backlog was *already fixed in a
   later sprint but never closed*. This session closed **18** such/finished
   issues. Before working any issue, `grep` the cited file/symbol on `main` —
   it may already be done. (Closed-as-already-resolved so far: #184, #202, #71,
   #72, #73, #142, #115, #116, #64, #199, #103, …)
2. **GitHub closing-keyword gotcha.** `Closes #1, #2, #3` only auto-closes the
   **first** issue. Repeat the keyword: `Closes #1, closes #2, closes #3` — or
   close the rest manually. (This session's multi-issue PRs left 7 issues open
   that were actually done; now reconciled.)

## Process conventions (don't relearn these)

- **Isolated worktree per unit of work.** `git worktree add ../cardigan-v4-<x>
  -b <branch> origin/main`; run **everything** from inside it; never `cd` into
  the primary checkout (the user runs parallel sessions there). Leave the
  worktree for review; clean up your own merged branch after.
- **CI is strict-up-to-date** (ruleset, 6 required checks incl. Docker Smoke +
  Version Consistency). Every PR must `gh pr update-branch` then re-pass before
  merge, so merges **serialize**. A full CI cycle is ~1–2 min.
- **Lint:** `ruff check .` + `black --check .` from repo root (CI-equivalent).
  `alembic/versions` and `tests*` are **excluded** in `pyproject.toml` — lint
  them directly and you'll see false positives.
- **TDD** the testable fixes (RED→GREEN). Reuse venv at
  `../cardigan-v4/venv` (black is pinned-equivalent 26.5.1 there).
- **Versioning:** git tag is SoT; bump `web/package.json` + compose
  `CARDIGAN_VERSION` fallback together (see `docs/VERSIONING.md` + memory
  `cardigan-versioning`).

## Cluster 1 — Ready backend sprints (cold-start friendly)

Pure backend, testable, no design blockers. Best picks for a fresh agent.

| Epic | Open issues | Lead / notes | Entry points |
|------|-------------|--------------|--------------|
| **I — Test/CI health** (#230) | #196, #102, #91, #78, #198, #200, #220 | **#196 first** (pin ruff/black — CI rotted #210 green→red on an unpinned black bump; real). #102 hangs; #200 triages ~31 env-dependent failures | `.github/workflows/ci.yml`, `tests/` |
| **H — Ingest scanner (legacy)** (#229) | #168, #218, #75, #106, #108, #129, #165 | **#168 first** (data-corruption, high). Distinct from mmingest — see the two-ingest-system gate in LEGACY_INVENTORY | `api/services/ingest_scanner.py`, `ingest_scheduler.py` |
| **F — Cost / observability** (#227) | #119, #118, #121, #120, #80, #117 | Mostly small (app_version plumbing, secrets-module hygiene, cost-doc). #119 get_app_version refactor | `api/services/database.py`, `llm.py` |
| **A — mmingest remainder** (#222) | #182, #190, #194, #195, #204, #205 | #190 (shared get_session — needs test-harness rework, see #238 deferral note), rest are soak/parser polish | `api/routers/mmingest.py`, `api/services/mmingest/` |
| **B — Deployment remainder** (#223) | #36, #37, #178, #180, #212, #213 | #213 (REMOTE_ACCESS doc stale), #178 (OCI labels). **#158/#179 are NOT here** — see Cluster 2 caveat | `Dockerfile.*`, `docker-compose*.yml`, `docs/` |
| **J — Legacy/investigation** (#231) | #28, #46, #83, #84, #114, #164 | Reference/archive; research-grade, low urgency | `docs/deprecated/`, `mcp_server/` |
| **K — Future / v5** (#232) | #20, #58, #59, #137, #163 | Dev/prod split, remote-hosting prep; **v5 horizon**, not v4.2 | — |

**Cross-cluster caveat:** #158 (health fields null) and #179 (process detection)
live in Epic B but are **blocked on a design decision** — both stem from
in-memory per-container `LLMRouter` state invisible to the API container. Design
them together (DB-heartbeat truth vs. a `CARDIGAN_DEPLOYMENT=docker` flag)
before coding. Don't pick them as a quick win.

## Cluster 2 — Frontend (needs browser verification)

Do these in a session with chrome-devtools / mcp-safari + the
frontend-design/impeccable skills. Don't edit blind.

- **D — Model routing UX** (#225): #144 (model-timeline viz), #219 (restart
  progress indicator), #140/#141 (verify — may be stale).
- **G — UI/UX/design** (#228): #145, #157, #159, #160, #63, #62, #111, #112,
  #96, #4, #76 — tokens, chat redesign, render bugs, queue UX.
- **#69 + #92** (in Epic L/D): the single-model dropdown + roster narrowing
  pair naturally with this cluster (they shape Settings).

## Cluster 3 — Epic L increment 2 (refactor) — #233

Open: #61, #69, #92. **Increment 1 done** (dead escalation machinery removed,
#103 closed). The rest needs a **cost rewire** (tier backends carry
`cost_per_project`; direct model selection must derive cost from the model) +
config migration + the deferred dropdown frontend. **Fully sequenced in
`planning/epic-l-consolidation-plan.md` — cost-first, with test anchors and the
homelab-config shim caveat.** Dedicated session.

## Cluster 4 — Phase 2 simplification (Epic M) — #234

Tracking epic, no sub-issues yet. **Discovery done in
`planning/LEGACY_INVENTORY.md`**: ingest-scheduler unification (gated),
phase_backends dead-weight (after Epic L), doc/archive sweep, MCP decommission
decision. Start each with the evidence in that doc.

## Editorial prompt-tuning note (Epic E, #226)

Open: #10, #113, #125, #128, #130, #131, #133, #134, #135, #136. The **code**
items (#126 duration, #132 length) shipped. The rest are mostly **formatter
house-style prompt rules** that change *LLM output* — **not unit-testable**.
Do them as **one editor-reviewed batch against real episode runs**, not blind
markdown edits. #131 (deterministic header from filename+SST) is the next
high-value *code* item here.

## Done this session (context)

v4.2.0 release (#221) · epic restructure (#236) · Sprint 1 mmingest (#238) ·
Sprint 2 migrations/deploy (#240) · Sprint 3 duration/SEO (#244) · Epic L
increment 1 (#245). Epic **C (#224) fully closed**.
