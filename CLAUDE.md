# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Non-Claude Agents: Workspace Isolation Rules

**If you are NOT Claude Code** (e.g., Gemini CLI, Codex, Copilot, Cursor, or any other LLM agent), you MUST follow these isolation rules to avoid interfering with Claude agent work:

1. **Always work in a git worktree.** Never modify files on the current branch directly. Create a worktree before making any changes:
   ```bash
   git worktree add .worktrees/<your-agent-name>-<feature> -b <your-agent-name>/<feature-description>
   ```
2. **Always commit to a feature branch.** Your branch name must include your agent identifier (e.g., `gemini/fix-typo`, `codex/add-tests`, `copilot/refactor-utils`). Never commit to `main` or any branch you did not create.
3. **Never force-push, rebase, or modify branches you did not create.** Treat all existing branches as read-only references.
4. **Clean up after yourself.** When your work is complete, leave the worktree in place for human review. Do not merge your own branches.
5. **Follow all other rules in this file.** The conventions below (commit format, secrets management, attribution, etc.) apply to all agents equally.

> Claude Code agents are exempt from the worktree requirement because they are the primary agents for this workspace and coordinate directly with the user.


## Repository Purpose

Cardigan - A database-backed, API-first system for processing video transcripts and generating SEO-optimized metadata for PBS Wisconsin streaming platforms.

**Key differences from v2.0:**
- FastAPI-based API layer (not direct script execution)
- SQLite database as single source of truth
- React web dashboard for monitoring
- Claude Desktop for copy-editor workflow (MCP integration)

## Key Commands

### Development

```bash
# Initialize development session
./init.sh

# Start API server (once implemented)
uvicorn api.main:app --reload

# Run tests
pytest

# Start web dev server (once implemented)
cd web && npm run dev
```

### Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Architecture

```
editorial-assistant-v3/
├── api/                        # FastAPI application
│   ├── main.py                 # App entry point
│   ├── routers/                # API endpoints
│   ├── models/                 # Pydantic schemas
│   └── services/               # Business logic
├── web/                        # React dashboard
│   └── src/
├── claude-desktop-project/     # Claude Desktop project config
│   ├── EDITOR_AGENT_INSTRUCTIONS.md  # Canonical editor prompt
│   ├── knowledge/              # Project knowledge files
│   └── templates/              # Output document templates
├── .claude/
│   ├── agents/                 # LLM agent system prompts
│   ├── templates/              # Output document templates
│   └── commands/               # Slash command definitions
├── config/                     # Configuration files
├── transcripts/                # Input files (gitignored)
├── OUTPUT/                     # Processed outputs (gitignored)
├── tests/                      # Test suite
├── docs/                       # Documentation
├── feature_list.json           # Development task queue
└── planning/                   # Historical planning docs
    ├── claude-progress.txt     # Progress tracking
    ├── DESIGN_3.5.md           # Current design specification
    └── DESIGN_4.0.md           # Future vision (planning only)
```

## Long-Running Development Harness

Start every session with `./init.sh`, then read `planning/claude-progress.txt` and `feature_list.json` to select the next feature.

### Workflow

1. **Initializer**: Run `./init.sh` to load context
2. **Select feature**: Pick next `pending` task from `feature_list.json`
3. **Update status**: Mark as `in_progress`
4. **Implement**: Complete the feature with tests
5. **Verify**: Run tests, ensure exit criteria met
6. **Update tracking**: Mark `completed`, update `planning/claude-progress.txt`
7. **Commit**: Create attributed commit

### Agent Assignment

Tasks in `feature_list.json` have an `agent` field:
- `orchestrator`: Claude Code handles directly (complex, multi-file)
- `cli-agent/gemini`: Delegate to Gemini CLI for boilerplate
- `cli-agent/claude`: Delegate to Claude CLI for documentation

## Git Commit Convention

**See**: `/Users/mriechers/Developer/the-lodge/conventions/COMMIT_CONVENTIONS.md`

AI commits should include agent attribution:
```
feat: Add new feature

[Agent: Main Assistant]

Detailed description...
```

## Design Reference

- **Current:** `planning/DESIGN_3.5.md` — v3.5 architecture (embedded chat, ingest pipeline, screengrabs)
- **Future:** `planning/DESIGN_4.0.md` — v4.0 vision (Docker, plugin system, deferred features)
- **Archived:** `planning/archive/DESIGN_v3.0.md` — original v3.0 design (historical)

## Current Sprint

**Sprint 2.1: Foundation & Infrastructure Reliability**

See `feature_list.json` for task queue and `planning/claude-progress.txt` for status.

## Airtable Integration (CRITICAL)

**READ-ONLY ACCESS ONLY.** AI agents must NEVER write to Airtable.

- Agents may READ Airtable data (SST records, metadata) to inform their work
- Agents must NEVER use `create_record`, `update_records`, `delete_records`, or any write operations
- All Airtable edits are made manually by the human user
- If a user asks an agent to update Airtable, the agent must decline and explain this policy

This constraint exists for data integrity and safety. The Airtable Single Source of Truth (SST) is the canonical metadata store for all PBS Wisconsin projects and must only be modified by authorized humans.

### Quick Reference

**See `docs/AIRTABLE_CHEATSHEET.md`** for token-efficient AirTable lookups:
- Direct table IDs (skip `list_tables` calls)
- Key fields for editorial workflows
- Ready-to-use filter formulas
- Program-specific query patterns

**Key Table IDs:**
| Table | ID |
|-------|-----|
| Single Source of Truth | `tblTKFOwTvK7xw1H5` |
| Projects | `tblU9LfZeVNicdB5e` |
| Segments | `tblb6x1BhkdhKrmT6` |
| Contacts | `tblJc6JpKVcmwg0XV` |
| Staff | `tblEjbbFzmpGZgbXF` |

**Base ID:** `appZ2HGwhiifQToB6`

## Notes for Claude Code

1. **Check feature_list.json** before starting work
2. **Update progress** after completing features
3. **Run tests** before marking complete
4. **Don't break the API contract** - OpenAPI spec is the source of truth (once defined)
5. **Log feedback** - Append issues to `AGENT-FEEDBACK.md` if created
6. **NEVER write to Airtable** - Read-only access for all AI agents
