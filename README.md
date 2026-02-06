# The Metadata Neighborhood

**PBS Wisconsin Digital Editorial Assistant v3.0**

A production-ready system for processing video transcripts and generating SEO-optimized metadata (titles, descriptions, keywords) for streaming platforms.

---

## Overview

The Metadata Neighborhood transforms raw video transcripts into polished, SEO-ready metadata with minimal human intervention. The system combines automated LLM processing with a friendly copy-editor interface powered by **Cardigan**, our Mister Rogers-inspired AI assistant.

### Key Features

- **API-first architecture** — FastAPI backend enables multiple interfaces
- **Real-time monitoring** — React dashboard with WebSocket live updates
- **Multi-model routing** — Cost-optimized LLM selection (Gemini, GPT-4o, Claude)
- **Claude Desktop integration** — MCP tools for human-in-the-loop editing
- **Batch processing** — Drag-and-drop upload or folder watching
- **Airtable sync** — Automatic SST record linking (read-only)

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    THE METADATA NEIGHBORHOOD                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐        │
│   │   Claude    │    │    Web      │    │  Transcript │        │
│   │   Desktop   │    │  Dashboard  │    │   Watcher   │        │
│   │ (Cardigan)  │    │   :3000     │    │             │        │
│   └──────┬──────┘    └──────┬──────┘    └──────┬──────┘        │
│          │                  │                   │               │
│          │     ┌────────────┴───────────────────┘               │
│          │     │                                                │
│          ▼     ▼                                                │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │              FastAPI Server (:8000)                      │  │
│   │   /api/queue  /api/jobs  /api/ws/jobs  /api/upload      │  │
│   └───────────────────────────┬─────────────────────────────┘  │
│                               │                                 │
│          ┌────────────────────┼────────────────────┐           │
│          │                    │                    │            │
│   ┌──────▼──────┐      ┌─────▼─────┐      ┌──────▼──────┐     │
│   │   Worker    │      │  SQLite   │      │ OpenRouter  │     │
│   │ (Job Queue) │      │    DB     │      │  LLM API    │     │
│   └─────────────┘      └───────────┘      └─────────────┘     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+ (for web dashboard)
- OpenRouter API key

### Installation

```bash
# Clone the repository
git clone https://github.com/MarkOnFire/ai-editorial-assistant-v3.git
cd ai-editorial-assistant-v3

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Install web dashboard dependencies
cd web && npm install && cd ..

# Configure environment
cp .env.example .env
# Edit .env with your OPENROUTER_API_KEY

# Run database migrations
./venv/bin/alembic upgrade head
```

### Running the System

```bash
# Start API server + worker
./scripts/start.sh

# Start web dashboard (in separate terminal)
cd web && npm run dev

# Check status
./scripts/status.sh

# Stop everything
./scripts/stop.sh
```

### Access Points

| URL | Description |
|-----|-------------|
| http://localhost:8000 | API server |
| http://localhost:3000 | Web dashboard |
| http://metadata.neighborhood:8000 | API (after running `./scripts/setup-local-domain.sh`) |

See [docs/QUICK_START.md](docs/QUICK_START.md) for detailed setup instructions.

---

## Web Dashboard

The React-based dashboard provides real-time monitoring and queue management:

- **Dashboard** — Queue stats, recent jobs, system health
- **Queue** — Job list with filtering, search, bulk actions, transcript upload
- **Projects** — Browse completed outputs by project
- **Settings** — Agent configuration, routing rules, accessibility preferences
- **System** — API health, worker status, log viewer

### Screenshots

*Dashboard and Queue pages showing real-time job monitoring*

---

## Meet Cardigan

**Cardigan** is the friendly copy editor who lives in The Metadata Neighborhood. Integrated with Claude Desktop via MCP, Cardigan helps polish AI-generated metadata with warmth and care.

Cardigan speaks like Mister Rogers — patient, encouraging, and genuinely delighted to help you create the best possible descriptions for your content.

### Setup for Claude Desktop

See [docs/CLAUDE_DESKTOP_SETUP.md](docs/CLAUDE_DESKTOP_SETUP.md) for MCP server configuration.

---

## Processing Pipeline

Each transcript passes through a configurable agent pipeline:

1. **Analyst** — Extracts key themes, quotes, and metadata from transcript
2. **SEO Specialist** — Generates keyword analysis and search optimization
3. **Formatter** — Produces final title, description, and tags
4. **Timestamp** — (Optional) Creates chapter markers

### Cost Optimization

The system routes requests to cost-appropriate models:

| Task | Default Model | Fallback |
|------|--------------|----------|
| Analysis | gemini-2.0-flash | gpt-4o-mini |
| SEO | gemini-2.0-flash | gpt-4o-mini |
| Formatting | gpt-4o-mini | gemini-2.0-flash |

---

## Agent Definitions

Each agent in the pipeline is defined by a system prompt document. These are the canonical references for agent behavior and capabilities.

### Pipeline Agents

| Agent | Definition | Role |
|-------|------------|------|
| Analyst | [`.claude/agents/analyst.md`](.claude/agents/analyst.md) | Extracts key themes, speakers, and metadata from raw transcripts |
| Formatter | [`.claude/agents/formatter.md`](.claude/agents/formatter.md) | Transforms timecoded transcripts into clean, readable markdown |
| SEO Specialist | [`.claude/agents/seo.md`](.claude/agents/seo.md) | Generates search-optimized titles, descriptions, tags, and keywords |
| Timestamp | [`.claude/agents/timestamp.md`](.claude/agents/timestamp.md) | Creates chapter markers and timestamps for video content |
| Manager (QA) | [`.claude/agents/manager.md`](.claude/agents/manager.md) | Final quality assurance checkpoint reviewing all agent outputs |
| Copy Editor | [`.claude/agents/copy_editor.md`](.claude/agents/copy_editor.md) | Reviews and refines content for clarity, grammar, and PBS style |
| AP Style Bot | [`.claude/agents/ap_style_bot.md`](.claude/agents/ap_style_bot.md) | Ensures UI text and interface copy follows AP Style Guidelines |

### Claude Desktop / Cardigan

| Document | Description |
|----------|-------------|
| [EDITOR_AGENT_INSTRUCTIONS.md](claude-desktop-project/EDITOR_AGENT_INSTRUCTIONS.md) | Canonical editor prompt — authoritative source for all editor agent behavior |
| [GEMINI.md](claude-desktop-project/GEMINI.md) | Gemini substitute editor persona configuration |
| [GEMINI_FULL_INSTRUCTIONS.md](claude-desktop-project/GEMINI_FULL_INSTRUCTIONS.md) | Extended Gemini-specific instructions layered on canonical source |

---

## Accessibility

The web dashboard implements accessibility features targeting **WCAG 2.1 Level AA** compliance.

### Implemented Features

| Feature | WCAG Criteria | Status |
|---------|---------------|--------|
| Skip navigation link | 2.4.1 Bypass Blocks | ✅ |
| Keyboard navigation | 2.1.1 Keyboard | ✅ |
| Visible focus indicators | 2.4.7 Focus Visible | ✅ |
| Focus trap in modals | 2.4.3 Focus Order | ✅ |
| ARIA labels | 4.1.2 Name, Role, Value | ✅ |
| Semantic HTML structure | 1.3.1 Info and Relationships | ✅ |
| Screen reader text | 1.3.1 | ✅ |
| Reduced motion (system + user) | 2.3.3 Animation | ✅ |
| Text resizing (up to 125%) | 1.4.4 Resize Text | ✅ |
| High contrast mode | 1.4.3 Contrast | ✅ |

### User Preferences

The Settings > Accessibility tab provides:

- **Reduce Motion** — Disables animations and transitions
- **Text Size** — Default, Large (18px), or Larger (20px)
- **High Contrast** — Enhanced color contrast for visibility

Preferences persist across sessions and respect system-level accessibility settings.

### Known Limitations

| Issue | WCAG Criteria | Priority |
|-------|---------------|----------|
| Color contrast not fully audited | 1.4.3 | Medium |
| Toast auto-dismiss timing | 2.2.1 | Medium |
| Form error announcements | 4.1.3 | Low |

**Note:** This assessment is self-reported. A professional accessibility audit is recommended for certification.

---

## API Reference

### Core Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/queue/` | List jobs with pagination/filtering |
| GET | `/api/queue/stats` | Queue statistics |
| POST | `/api/queue` | Create new job |
| GET | `/api/jobs/{id}` | Get job details |
| PATCH | `/api/jobs/{id}` | Update job |
| DELETE | `/api/jobs/{id}` | Delete job |
| POST | `/api/upload/transcripts` | Bulk upload transcripts |
| WS | `/api/ws/jobs` | Real-time job updates |

### WebSocket Events

```json
{
  "type": "job_created|job_started|job_completed|job_failed",
  "job": { /* full job object */ }
}
```

See [docs/WEBSOCKET_IMPLEMENTATION.md](docs/WEBSOCKET_IMPLEMENTATION.md) for details.

---

## Development

### Project Structure

```
ai-editorial-assistant-v3/
├── api/                        # FastAPI application
│   ├── main.py                 # App entry point
│   ├── routers/                # API endpoints
│   ├── models/                 # Pydantic schemas
│   └── services/               # Business logic
├── web/                        # React dashboard
│   └── src/
│       ├── components/         # UI components
│       ├── pages/              # Route pages
│       ├── hooks/              # Custom React hooks
│       └── context/            # React contexts
├── mcp_server/                 # Claude Desktop MCP integration
├── claude-desktop-project/     # Claude Desktop project config
│   ├── EDITOR_AGENT_INSTRUCTIONS.md  # Canonical editor prompt
│   ├── knowledge/              # Project knowledge files
│   └── templates/              # Output document templates
├── .claude/agents/             # LLM agent system prompts
├── config/                     # Configuration files
├── scripts/                    # Utility scripts
├── docs/                       # Documentation
└── tests/                      # Test suite
```

### Running Tests

```bash
# Python tests
./venv/bin/pytest

# TypeScript build check
cd web && npm run build
```

### Development Session

```bash
# Initialize session (loads context)
./init.sh

# Check current progress
cat planning/claude-progress.txt
cat feature_list.json | jq '.[] | select(.status == "pending")'
```

See [CLAUDE.md](CLAUDE.md) for AI development guidelines.

---

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | API key for LLM requests |
| `AIRTABLE_API_KEY` | No | Read-only access for SST lookup |
| `DATABASE_URL` | No | SQLite path (default: `./dashboard.db`) |

### LLM Configuration

Edit `config/llm-config.json` to customize:

- Model routing preferences
- Agent prompts and phases
- Worker concurrency settings
- Cost thresholds

---

## Documentation

| Document | Description |
|----------|-------------|
| [QUICK_START.md](docs/QUICK_START.md) | 5-minute setup guide |
| [CLAUDE_DESKTOP_SETUP.md](docs/CLAUDE_DESKTOP_SETUP.md) | MCP integration setup |
| [WEBSOCKET_IMPLEMENTATION.md](docs/WEBSOCKET_IMPLEMENTATION.md) | Real-time updates architecture |
| [DESIGN_3.5.md](planning/DESIGN_3.5.md) | Current design specification |
| [ROADMAP_v3.1.md](docs/ROADMAP_v3.1.md) | Development roadmap |
| [WEB_UI_GUIDE.md](docs/WEB_UI_GUIDE.md) | Web dashboard user guide |

---

## Version History

### v3.0 (January 2025)

- Complete architectural rewrite from v2.0
- FastAPI backend with SQLite database
- React web dashboard with real-time updates
- WebSocket live job monitoring
- Bulk transcript upload with drag-and-drop
- Accessibility preferences (reduce motion, text size, high contrast)
- Claude Desktop MCP integration (Cardigan)
- Multi-model LLM routing via OpenRouter

### v2.0 (2024)

- CLI-based processing with TUI visualizer
- MCP tools for Claude Desktop
- JSON file-based state management

---

## License

Internal PBS Wisconsin tool — not for distribution.

---

*Welcome to The Metadata Neighborhood. Cardigan is glad you're here.* 🏘️
