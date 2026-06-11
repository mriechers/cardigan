# CLAUDE.md

## Repository Purpose

PBS Wisconsin Editorial Assistant — a Claude Desktop project configuration for editing video metadata (titles, descriptions, keywords) using AP Style and PBS Wisconsin editorial standards.

This is **not an application** — it's a system prompt and knowledge files designed to be loaded into a Claude Desktop project.

## Structure

```
agent-instructions/EDITOR_AGENT_INSTRUCTIONS.md  — System prompt (paste into Claude Desktop)
knowledge/examples_and_styleguides/               — Reference files (upload to Claude Desktop)
```

## Development Notes

- The system prompt is the core deliverable — changes here change agent behavior
- Knowledge files are uploaded to Claude Desktop as project knowledge
- No build steps, no dependencies, no runtime code

## Git Commit Convention

AI commits should include `[Agent: <name>]` after the subject line per workspace conventions.
