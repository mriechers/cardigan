# PBS Wisconsin Editorial Assistant

A Claude Desktop project for editing PBS Wisconsin video metadata — titles, descriptions, and keywords for streaming platforms.

## What This Is

This is a **Claude Desktop project configuration**, not a standalone application. It provides:

- **Agent instructions** — A detailed system prompt that turns Claude into a PBS Wisconsin copy-editing specialist
- **Knowledge files** — AP Style guide, transcript style guide, media ID conventions, and organizational AI guidelines

## Setup

### 1. Create a Claude Desktop Project

1. Open [Claude Desktop](https://claude.ai) or the Claude desktop app
2. Go to **Projects** (left sidebar)
3. Click **Create Project**
4. Name it something like "PBS Wisconsin Editorial Assistant"

### 2. Add the System Prompt

1. Open the project settings
2. Find **Custom Instructions** (or **Project Instructions**)
3. Copy the entire contents of [`agent-instructions/EDITOR_AGENT_INSTRUCTIONS.md`](agent-instructions/EDITOR_AGENT_INSTRUCTIONS.md)
4. Paste it into the project instructions field
5. Save

### 3. Upload Knowledge Files

Upload these files from the `knowledge/examples_and_styleguides/` folder to the project's **Knowledge** section:

| File | Purpose |
|------|---------|
| `ap_styleguide.pdf` | AP Style reference for editorial rules |
| `Transcript Style Guide.pdf` | PBS Wisconsin transcript formatting standards |
| `WPM Generative AI Guidelines.pdf` | Organizational AI use policy |
| `Media ID Prefixes.md` | PBS Wisconsin media naming conventions |
| `Media Manager timestamp sample.png` | Example timestamp format (Media Manager) |
| `YouTube timestamp sample.png` | Example timestamp format (YouTube) |

### 4. Start Using It

Open a new conversation in the project and try:

- **Paste a transcript** — The agent will analyze it and suggest titles, descriptions, and keywords
- **Paste draft copy** — The agent will review it against editorial rules and suggest improvements
- **Upload a screenshot** — The agent will read and analyze visible metadata
- **Provide SEMRush data** — The agent will integrate keyword research into recommendations

## How It Works

The agent applies PBS Wisconsin editorial standards through conversational editing:

- **AP Style** compliance with PBS Wisconsin house style adjustments
- **Program-specific rules** for University Place, Here and Now, Wisconsin Life, Garden Wanderings, The Look Back, and Digital Shorts
- **Prohibited language** detection (no "watch as", "discover", "amazing", etc.)
- **Character count** validation for all metadata fields
- **SEO keyword** generation using direct and logical/implied methods
- **Title/description pairing** coherence checks

## Repository Structure

```
editorial-assistant/
├── README.md                          # This file
├── CLAUDE.md                          # Claude Code instructions (for development)
├── agent-instructions/
│   └── EDITOR_AGENT_INSTRUCTIONS.md   # System prompt — paste into Claude Desktop
└── knowledge/
    └── examples_and_styleguides/
        ├── ap_styleguide.pdf
        ├── Transcript Style Guide.pdf
        ├── WPM Generative AI Guidelines.pdf
        ├── Media ID Prefixes.md
        ├── Media Manager timestamp sample.png
        └── YouTube timestamp sample.png
```

## Updating

To update the agent's behavior:

1. Edit `agent-instructions/EDITOR_AGENT_INSTRUCTIONS.md`
2. Copy the updated content to your Claude Desktop project instructions
3. New conversations will use the updated instructions
