# Cardigan Pipeline Prompts

This directory holds the system prompts for every LLM-driven phase in Cardigan's transcript pipeline. **These files are the editorial voice of the system.** Edit them here to change what the model is told to do for any given phase.

Each file is a freestanding markdown document. The worker loads it verbatim (with a small set of runtime substitutions — see below) and sends it as the system message for that phase's LLM call.

## Phase map

| File | Phase | What the agent does |
|------|-------|---------------------|
| `analyst.md` | `analyst` | Reads the raw transcript, produces a brainstorming document — speaker map, themes, structural breakdown, items needing human review. Sets up later phases. |
| `formatter.md` | `formatter` | Transforms the raw transcript into clean, readable markdown — speaker attribution, paragraph breaks, filler removal, punctuation fixes. **Never paraphrases.** |
| `seo.md` | `seo` | Generates the SEO metadata (title, short/long description, keywords, social copy) from the formatted transcript and analyst notes. |
| `validator.md` | `validator` | QA-reviews the outputs of the prior phases against SST and the brainstorming doc; flags issues for retry or escalation. Sprint 2 replaced the older `manager` agent with this. |
| `timestamp.md` | `timestamp` | Generates chapter timestamps from the formatted transcript. Auto-triggers for content ≥10 minutes; can be manually triggered for shorter content. |
| `copy_editor.md` | `copy_editor` | Interactive copy-editing agent used through Claude Desktop / MCP, not by the worker. Not part of the auto-pipeline. **For the Claude Code dispatchable version (REST-API transport), see the copy-editor agent at the pbswi workspace root** — it was relocated there from `.claude/agents/copy_editor.md` in this repo so it registers workspace-wide. |
| `ap_style_bot.md` | (none) | Legacy/experimental prompt. Not currently invoked by the worker. Kept for reference; safe to remove if confirmed unused. |

## How to refine a prompt

1. Open the file in your editor of choice — it's plain markdown.
2. Make the change.
3. Save. **No restart needed.** The worker re-reads the file at the start of every phase, so the next job picks up the new prompt automatically.
4. Commit on a feature branch and open a PR. Prompt changes deserve the same review as code changes.

## Runtime substitutions

The worker (`api/services/worker.py:_load_agent_prompt`) substitutes a small set of placeholders before sending the prompt to the LLM:

| Placeholder | Substituted with |
|-------------|------------------|
| `{TODAY'S DATE in YYYY-MM-DD format}` | Current UTC date, e.g. `2026-05-21`. The LLM has no clock — without this, it hallucinates dates near its training cutoff. |
| `{model name you are running as}` / `{the model you are running as}` | The OpenRouter model identifier the prompt is being sent to (when the worker passes it). |

Other curly-brace blocks in the prompts (e.g. `{ACTUAL media_id from filename}`) are *instructions to the model*, not substitution targets. They're left in place for the LLM to fill in with reasoning.

## Test coverage

`tests/api/test_worker.py::TestLoadAgentPrompt` covers the substitution behavior — date sub always runs, model sub runs only when provided, placeholder is left intact when no model is given.

## History

Prompts used to live at `.claude/agents/*.md`. They were moved here on 2026-05-21 because the `.claude/` directory implied internal Claude Code tooling and made the prompts hard to discover on a fresh clone. The substantive content is unchanged.
