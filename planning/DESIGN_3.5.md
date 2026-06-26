# Editorial Assistant v3.5 - Feature Expansion Design Document

**Goal:** Expand functionality and polish the user experience while maintaining local/single-user focus. Complete the feature set before v4.0's remote deployment work.

**Status:** Planning document
**Last Updated:** January 2026

---

## Executive Summary

V3.5 focuses on **feature richness and workflow optimization** without the complexity of remote deployment or multi-user authentication. This release completes the vision of a powerful local editorial assistant before the architectural changes required for v4.0.

**Key Themes:**
1. **Interactive Editing** - Embedded chat experience in the web dashboard
2. **Workflow Integration** - Remote ingest monitoring, Content Calendar automation
3. **Observability** - Langfuse for LLM cost/quality tracking
4. **Performance** - Large transcript handling improvements
5. **Extensibility** - Plugin system foundation
6. **Polish** - UX improvements, test coverage, project rename

---

## Part 1: Embedded Web Chat (HIGH PRIORITY)

### 1.1 Overview

Build a chat interface directly into the web dashboard for copy-editor workflow, eliminating the need for a separate Claude Desktop window. This is the marquee feature of v3.5.

### 1.2 Requirements

| Component | Description |
|-----------|-------------|
| **WebSocket messaging** | Real-time bidirectional communication with LLM backend |
| **Session persistence** | Chat history stored per project, resumable conversations |
| **File attachments** | Upload screenshots, draft copy, reference documents |
| **Artifact rendering** | Inline display of revision documents, brainstorming output |
| **Context auto-loading** | Automatically inject project context (transcript, SST metadata, brainstorming) |
| **Multi-turn conversations** | Maintain coherent conversation state across edits |
| **Token streaming** | Display responses as they generate for better UX |

### 1.3 Technical Approach

**Architecture Decision: WebSocket-First**

```
User Action → WebSocket Message → Chat Router → LLM Service → Streaming Response
                                      ↓
                              Context Builder
                              (loads project, SST, history)
```

**Backend Components:**
```
api/
  routers/
    chat.py              # WebSocket endpoint, REST fallback
  services/
    chat_session.py      # Session management, context building
    chat_llm.py          # LLM abstraction for chat
  models/
    chat.py              # ChatSession, ChatMessage Pydantic models

alembic/versions/
    XXX_add_chat_tables.py  # chat_sessions, chat_messages tables
```

**Frontend Components:**
```
web/src/
  components/
    chat/
      ChatPanel.tsx       # Main chat container
      ChatMessage.tsx     # Individual message rendering
      ChatInput.tsx       # Message input with file upload
      ChatContext.tsx     # Project context sidebar
      ArtifactViewer.tsx  # Inline revision/report display
  hooks/
    useChat.ts            # WebSocket management, message state
    useChatContext.ts     # Project context loading
```

**Database Schema:**
```sql
CREATE TABLE chat_sessions (
    id TEXT PRIMARY KEY,
    project_name TEXT,
    title TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME,
    message_count INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    total_cost REAL DEFAULT 0,
    status TEXT DEFAULT 'active'
);

CREATE TABLE chat_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES chat_sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    attachments TEXT,
    model TEXT,
    tokens INTEGER,
    cost REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 1.4 Integration Points

- **MCP Server Tools**: Reuse `load_project_for_editing()`, `get_formatted_transcript()`, `save_revision()`, `get_sst_metadata()` functions
- **Existing WebSocket**: Follow patterns from job status WebSocket (`api/routers/websocket.py`)
- **LLM Client**: Adapt `api/services/llm.py` for streaming
- **Agent Instructions**: Use Cardigan personality from `claude-desktop-project/EDITOR_AGENT_INSTRUCTIONS.md`

### 1.5 Prototype Strategy

See `planning/archive/PROTOTYPE_EMBEDDED_CHAT.md` (archived) for detailed 1-sprint prototype specification.

### 1.6 Estimated Effort

| Component | Complexity | Days |
|-----------|------------|------|
| Database schema + migrations | Low | 2 |
| Chat session service | Medium | 3 |
| LLM streaming adapter | Medium | 3 |
| WebSocket chat endpoint | Medium | 3 |
| Chat UI components | High | 5-7 |
| Context injection | Medium | 2 |
| Artifact rendering | Medium | 3 |
| File attachments | Medium | 3 |
| Cost tracking | Low | 1 |
| Testing | Medium | 3 |
| **Total** | | **28-32 days** (~3-4 sprints) |

---

## Part 2: Remote Ingest Watcher (HIGH PRIORITY)

### 2.1 Overview

Monitor PBS Wisconsin's ingest server for new transcripts and screengrabs with one-click queueing from the dashboard.

### 2.2 Current Status

Fully scoped in `docs/FEATURE_REMOTE_INGEST_WATCHER.md` with Sprint 11.1 containing 21 tasks in `feature_list.json`.

### 2.3 Key Components

| Component | Description |
|-----------|-------------|
| **IngestScanner** | HTTP polling service for mmingest.pbswi.wisc.edu |
| **ScreengrabAttacher** | Controlled Airtable write for thumbnail attachment |
| **Dashboard Panel** | New files display with one-click queueing |
| **Background Polling** | Async task for periodic server checks |

### 2.4 Safety Architecture

The screengrab attachment is a **controlled Airtable write exception**:
- Scope-limited to SST table, Screengrab URLs field only
- Append-only operations (field update, not record creation/deletion)
- Explicit user confirmation before any write
- Full audit logging

### 2.5 Estimated Effort

- Transcript monitoring: 2 sprints
- Screengrab attachment: 1 sprint
- **Total: 3 sprints**

---

## Part 3: Langfuse Observability (MEDIUM PRIORITY)

### 3.1 Overview

Production-grade LLM observability for cost tracking, trace analysis, and prompt versioning. Valuable for understanding spend patterns before v4.0 remote deployment.

### 3.2 Local Deployment

Run Langfuse self-hosted via Docker Compose:
- No external data dependency
- Full control over telemetry
- Works entirely on local machine

```yaml
# docker-compose.langfuse.yml
services:
  langfuse:
    image: langfuse/langfuse:latest
    ports:
      - "3100:3000"
    environment:
      - DATABASE_URL=postgresql://...
      - NEXTAUTH_SECRET=...
```

### 3.3 Integration Points

```python
from langfuse import Langfuse

langfuse = Langfuse(
    public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
    secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
    host=os.getenv("LANGFUSE_HOST", "http://localhost:3100")
)

# Wrap LLM calls
trace = langfuse.trace(name="editorial-job", metadata={"job_id": job.id})
span = trace.span(name=f"llm-{phase}", model=model_name, usage={...})
```

### 3.4 Features Enabled

- Per-job cost tracking with detailed breakdown
- Trace visualization for debugging
- Prompt version comparison
- Cost aggregation by project/phase/model

### 3.5 Estimated Effort: 1 sprint

---

## Part 4: Content Calendar Integration (MEDIUM PRIORITY)

### 4.1 Overview

Bulk scheduling interface for creating Content Calendar entries from processed projects.

### 4.2 Requirements

| Feature | Description |
|---------|-------------|
| **Video list parsing** | Input list of videos with target days |
| **Media ID lookup** | Resolve video names to Airtable records |
| **Platform detection** | Determine YouTube/Facebook/Twitter targets |
| **Entry creation** | Create All Tasks records with dates |
| **Confirmation UI** | Preview before any writes |

### 4.3 Safety Architecture

This is another **controlled Airtable write exception**:
- Scope-limited to Content Calendar/All Tasks table only
- Append-only operations (CREATE, never UPDATE/DELETE on existing)
- Explicit user confirmation before any write
- Full audit logging

### 4.4 Estimated Effort: 1-2 sprints

---

## Part 5: Large Transcript Optimization (LOWER PRIORITY)

### 5.1 Overview

Intelligent chunking and parallel processing for long-form content (90+ minute videos, multi-part series).

### 5.2 Strategy

| Feature | Description |
|---------|-------------|
| **Size estimation** | Calculate transcript token count before processing |
| **Smart chunking** | Split at natural boundaries (speaker change, paragraph) |
| **Overlap handling** | Maintain context continuity, dedupe on merge |
| **Parallel execution** | Process chunks concurrently (configurable workers) |
| **Progress tracking** | Per-chunk progress in job phases |

### 5.3 Configuration

```python
CHUNK_CONFIG = {
    "threshold_chars": 100_000,
    "chunk_size": 50_000,
    "overlap_chars": 2_000,
    "max_parallel": 3,
    "merge_strategy": "dedupe_overlap",
    "split_boundaries": ["speaker_change", "paragraph", "sentence"]
}
```

### 5.4 Estimated Effort: 1-2 sprints

---

## Part 6: Plugin System Foundation (LOWER PRIORITY)

### 6.1 Overview

Enable custom agents and integrations via plugin architecture, preparing for ecosystem growth.

### 6.2 Plugin Types

| Type | Description | Example |
|------|-------------|---------|
| **Agent plugins** | Custom processing phases | Social media copy generator |
| **Output formatters** | Transform outputs | CMS-specific XML export |
| **Integrations** | External services | Slack notifications |
| **Model providers** | Add LLM backends | Local Ollama support |

### 6.3 Plugin API

```python
# plugin.yaml
name: social-media-agent
version: 1.0.0
type: agent
hooks:
  - phase: after_seo
    handler: generate_social_copy

# Implementation
from editorial_assistant.plugins import AgentPlugin

class SocialMediaAgent(AgentPlugin):
    def process(self, context: ProjectContext) -> PluginResult:
        # Generate TikTok/Instagram copy
        ...
```

### 6.4 Estimated Effort: 2 sprints

---

## Part 7: Google Docs Export (LOWER PRIORITY)

### 7.1 Overview

One-way export of revision documents to Google Docs for collaborative human editing.

### 7.2 Scope Limitation

V3.5 targets **export only**. Full real-time collaboration (import changes back, CRDT sync) is deferred to v5.0+.

### 7.3 Implementation

- Google Docs API integration
- OAuth flow for user authorization
- Markdown → Google Docs format conversion
- Export button on revision documents

### 7.4 Estimated Effort: 1 sprint

---

## Part 8: Cleanup and Polish

### 8.1 Incomplete UX Work (Sprint 8.x carryover)

| Task | Description |
|------|-------------|
| 8.2.3 | Replace native dialogs in Queue.tsx |
| 8.2.6 | Replace loading text with skeletons |
| 8.2.7 | Add action feedback to JobDetail |
| 8.3.x | Navigation improvements |
| 8.5.x | Accessibility preferences |

### 8.2 Test Coverage (Sprint 9.x carryover)

| Task | Description |
|------|-------------|
| 9.2.2 | Add tests for API endpoints |
| 9.2.3 | Add tests for LLMClient |
| 9.2.4 | Add tests for watch_transcripts.py |
| 9.3.1 | Second round code review |

### 8.3 Artifact Feedback Mechanism

User feedback on automated outputs from the Projects screen. Helps track which generations need improvement.

### 8.4 Project Rename

Rename from `ai-editorial-assistant-v3` to `cardigan-editorial-assistant`:
- Update package names, imports
- Update documentation references
- Update git remote if needed
- Coordinate with any external references

---

## Priority Matrix

| Feature | User Value | Complexity | Effort | Priority |
|---------|------------|------------|--------|----------|
| Embedded Web Chat | Very High | High | 3-4 sprints | P1 |
| Remote Ingest Watcher | High | Medium | 3 sprints | P1 |
| Langfuse Observability | Medium | Low | 1 sprint | P2 |
| Content Calendar | High | Medium | 1-2 sprints | P2 |
| UX/Test Cleanup | Medium | Low | 1-2 sprints | P2 |
| Large Transcript Opt. | Low | Medium | 1-2 sprints | P3 |
| Plugin System | Medium | High | 2 sprints | P3 |
| Google Docs Export | Medium | Medium | 1 sprint | P3 |
| Project Rename | Low | Low | 0.5 sprint | P3 |

---

## Recommended V3.5 Roadmap

### Phase 1: Foundation & Validation (Sprints 11-12)
- Complete Sprint 11.1: Remote Ingest Watcher (transcripts)
- Chat prototype (1 sprint) - validate approach
- Sprint 8.x/9.x cleanup work

### Phase 2: Core Features (Sprints 13-16)
- Full Embedded Web Chat implementation
- Remote Ingest Watcher (screengrabs)
- Langfuse integration

### Phase 3: Workflow Completion (Sprints 17-18)
- Content Calendar integration
- Large transcript optimization

### Phase 4: Polish & Ecosystem (Sprints 19-20)
- Plugin system foundation
- Google Docs export
- Project rename to cardigan-editorial-assistant
- Final documentation updates

---

## Dependencies

Before V3.5 development:
1. V3.0 stable and in daily use (achieved)
2. Sprint 8/9 cleanup in progress
3. Clear understanding of chat UI patterns from prototype

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Chat response latency | <3s average |
| Ingest discovery time | <15 minutes from upload |
| Cost tracking accuracy | Within 2% of actual |
| New file queue rate | <10 seconds per file |
| Test coverage | >70% for core services |

---

## Transition to V4.0

V3.5 completion criteria for V4.0 readiness:
1. All P1/P2 features complete and stable
2. Embedded chat in production use
3. Langfuse cost tracking operational
4. Project renamed to cardigan-editorial-assistant
5. Documentation updated for new features

V4.0 will then focus exclusively on:
- Remote deployment (VM, cloud)
- Docker containerization
- Authentication & multi-user
- Security hardening
- Cost analysis (cloud vs self-hosted)

---

---

## Appendix A: Observability Dashboard Enhancements

*Added: 2026-01-15 — Extends Part 3 (Langfuse Observability) with actionable analytics*

### A.1 Overview

Build a suite of data visualizations focused on **identifying underperforming models**, **optimizing costs**, and **flagging workflow inefficiencies**. These complement the basic Langfuse integration with domain-specific insights for editorial workflow optimization.

### A.2 Priority 1: Cost Optimization Visualizations

#### A.2.1 Escalation Cost Waste Analysis

**Purpose:** Quantify money spent on failed lower-tier attempts before successful escalation. Answers: "Should we skip cheapskate tier for certain scenarios?"

| Component | Description |
|-----------|-------------|
| **Wasted cost metric** | Sum of costs for all failed attempts in jobs that eventually succeeded at a higher tier |
| **Breakdown dimensions** | By phase, by transcript duration bucket, by time period |
| **Threshold recommendation** | ML/heuristic suggesting optimal starting tier per phase+duration |
| **Savings projection** | "Starting analyst at tier 1 for >25min transcripts would save $X/month" |

**Data source:** `session_stats` table — join failed attempts with eventual success

**API endpoint:** `GET /api/analytics/escalation-waste?days=30&phase=analyst`

**UI component:** `EscalationWasteWidget.tsx`
- Bar chart: Wasted $ by phase
- Trend line: Waste over time
- Recommendation cards with projected savings

**Estimated effort:** 3-4 days

---

#### A.2.2 Cost Per Successful Job Trend

**Purpose:** Track cost efficiency over time, detect anomalies, project budget.

| Component | Description |
|-----------|-------------|
| **Cost/job metric** | Total cost ÷ successful jobs, rolling average |
| **Anomaly detection** | Flag days where cost/job exceeds 2σ from baseline |
| **Budget tracking** | Monthly burn rate, projected end-of-month spend |
| **Alert threshold** | Configurable warning when cost/job exceeds target |

**Data source:** `session_stats` + `jobs` tables

**API endpoint:** `GET /api/analytics/cost-trend?days=90&granularity=daily`

**UI component:** `CostTrendWidget.tsx`
- Line chart with anomaly highlighting
- Budget gauge (current vs projected vs limit)
- Sparkline for quick dashboard view

**Estimated effort:** 2-3 days

---

#### A.2.3 Cost Attribution Sankey Diagram

**Purpose:** Visualize where money flows: Job → Phase → Tier → Model → Cost

| Component | Description |
|-----------|-------------|
| **Flow visualization** | Sankey diagram showing cost distribution |
| **Drill-down** | Click any segment to filter/expand |
| **Comparison mode** | Side-by-side: This month vs last month |
| **Export** | Download attribution data as CSV |

**Data source:** `session_stats` aggregations

**API endpoint:** `GET /api/analytics/cost-attribution?days=30`

**UI component:** `CostAttributionWidget.tsx`
- D3.js or Recharts Sankey implementation
- Toggle between: by-phase, by-model, by-tier views

**Estimated effort:** 4-5 days (Sankey is complex)

---

### A.3 Priority 2: Model Health Visualizations

#### A.3.1 Model Reliability Matrix

**Purpose:** Heatmap showing success rate for each Model × Phase combination. Instantly spot bad model/task pairings.

| Component | Description |
|-----------|-------------|
| **Matrix data** | Rows = models, Columns = phases, Cells = success rate % |
| **Color scale** | Green (>98%) → Yellow (90-98%) → Red (<90%) |
| **Sample size indicator** | Cell opacity or size indicates confidence (n=5 vs n=500) |
| **Drill-down** | Click cell to see failure details |

**Data source:** `session_stats` grouped by model, phase

**API endpoint:** `GET /api/analytics/model-matrix?days=30&min_samples=5`

**UI component:** `ModelReliabilityMatrix.tsx`
- Interactive heatmap with tooltips
- Filter by tier, time period
- Highlight cells below threshold

**Estimated effort:** 3-4 days

---

#### A.3.2 Model Response Time Distribution

**Purpose:** Box plots showing latency distribution per model. Flag slow AND expensive models.

| Component | Description |
|-----------|-------------|
| **Latency percentiles** | p50, p75, p90, p99 response times per model |
| **Trend detection** | Alert if model latency trending upward |
| **Cost overlay** | Show cost/token alongside latency |
| **Timeout tracking** | % of requests that hit timeout threshold |

**Data source:** `session_stats.data->duration_ms` (needs schema addition if not present)

**API endpoint:** `GET /api/analytics/model-latency?days=30`

**UI component:** `ModelLatencyWidget.tsx`
- Box plot or violin plot per model
- Sortable by p50, p99, or cost
- Trend sparklines

**Estimated effort:** 3-4 days

---

#### A.3.3 Error Pattern Analysis

**Purpose:** Categorize and visualize failure modes by model. Distinguish "model is bad" from "our prompt is bad."

| Component | Description |
|-----------|-------------|
| **Error categorization** | Timeout, 400 error, rate limit, malformed JSON, validation failure, content filter |
| **Pattern detection** | "DeepSeek times out 40% of the time" vs "Mistral returns invalid JSON on SEO phase" |
| **Root cause hints** | Link error patterns to likely causes |
| **Historical comparison** | Did this model used to work better? |

**Data source:** `session_stats` error messages, requires error parsing/categorization

**API endpoint:** `GET /api/analytics/error-patterns?days=30&model=all`

**UI component:** `ErrorPatternWidget.tsx`
- Stacked bar chart: error types by model
- Treemap: error distribution
- Detail table with example errors

**Estimated effort:** 4-5 days (error parsing logic is non-trivial)

---

### A.4 Priority 3: Workflow Efficiency Visualizations

#### A.4.1 Retry Funnel

**Purpose:** Funnel showing attempt distribution across tiers. Identify phases where tier 0 is pointless overhead.

| Component | Description |
|-----------|-------------|
| **Funnel stages** | Total Attempts → Tier 0 Success → Tier 1 Success → Tier 2 Success → Failed |
| **Per-phase view** | Separate funnel for each phase |
| **Conversion rates** | % that succeed at each tier |
| **Recommendation** | "Analyst phase: 5% succeed at tier 0, consider starting at tier 1" |

**Data source:** `session_stats` attempt sequences per job+phase

**API endpoint:** `GET /api/analytics/retry-funnel?days=30&phase=all`

**UI component:** `RetryFunnelWidget.tsx`
- Funnel visualization per phase
- Comparison mode: phase A vs phase B
- Efficiency score

**Estimated effort:** 3-4 days

---

#### A.4.2 Job Duration Outliers

**Purpose:** Scatter plot identifying jobs that took unexpectedly long. Drill down to find bottleneck phase.

| Component | Description |
|-----------|-------------|
| **Expected vs actual** | X = transcript duration, Y = processing time |
| **Outlier detection** | Highlight jobs >2σ from regression line |
| **Phase breakdown** | On click: show time spent per phase |
| **Root cause** | Link to specific slow model call or retry sequence |

**Data source:** `jobs` + `session_stats` timestamps

**API endpoint:** `GET /api/analytics/duration-outliers?days=30&threshold=2`

**UI component:** `DurationOutliersWidget.tsx`
- Scatter plot with outlier highlighting
- Click-to-inspect detail panel
- Export outlier list

**Estimated effort:** 3-4 days

---

#### A.4.3 Queue Health Monitor

**Purpose:** Track queue throughput, stuck jobs, and processing patterns.

| Component | Description |
|-----------|-------------|
| **Stuck job detection** | Jobs in `in_progress` for >30 minutes |
| **Throughput trend** | Jobs completed per hour/day |
| **Queue depth history** | How long jobs wait before processing |
| **Pattern detection** | "Jobs queued Friday evening take 3x longer" |

**Data source:** `jobs` table timestamps

**API endpoint:** `GET /api/analytics/queue-health?days=7`

**UI component:** `QueueHealthWidget.tsx`
- Current stuck jobs alert
- Throughput sparkline
- Wait time distribution

**Estimated effort:** 2-3 days

---

### A.5 Implementation Strategy

#### Phase 1: Foundation (1 sprint)
- [ ] Create `api/routers/analytics.py` with base endpoints
- [ ] Implement A.2.1 (Escalation Waste) — highest ROI
- [ ] Implement A.3.1 (Model Reliability Matrix) — quick wins identification
- [ ] Add analytics section to System.tsx dashboard

#### Phase 2: Cost Focus (1 sprint)
- [ ] Implement A.2.2 (Cost Trend)
- [ ] Implement A.2.3 (Cost Attribution Sankey)
- [ ] Add budget alerting infrastructure

#### Phase 3: Deep Diagnostics (1 sprint)
- [ ] Implement A.3.2 (Model Latency)
- [ ] Implement A.3.3 (Error Patterns)
- [ ] Implement A.4.1 (Retry Funnel)

#### Phase 4: Workflow Optimization (1 sprint)
- [ ] Implement A.4.2 (Duration Outliers)
- [ ] Implement A.4.3 (Queue Health)
- [ ] Add automated recommendations engine

### A.6 Data Requirements

| Visualization | Required Data | Currently Captured? |
|---------------|---------------|---------------------|
| Escalation Waste | Failed attempt costs | ✅ Yes (session_stats) |
| Cost Trend | Per-job total cost | ✅ Yes |
| Cost Attribution | Phase/model/tier costs | ✅ Yes |
| Model Matrix | Success/fail by model+phase | ✅ Yes |
| Model Latency | Response time per call | ⚠️ Partial (needs duration_ms in session_stats) |
| Error Patterns | Error messages/types | ⚠️ Partial (needs structured error logging) |
| Retry Funnel | Attempt sequences | ✅ Yes |
| Duration Outliers | Job timestamps | ✅ Yes |
| Queue Health | Queue/start/end times | ✅ Yes |

### A.7 Schema Additions

```sql
-- Add to session_stats.data JSON (already flexible schema)
-- Ensure these fields are populated:
{
  "duration_ms": 1234,           -- LLM response time
  "error_category": "timeout",   -- Categorized error type
  "error_message": "...",        -- Raw error for analysis
  "attempt_number": 1,           -- Which attempt in sequence
  "escalated_from": 0            -- Previous tier if escalated
}

-- Optional: Materialized view for fast analytics
CREATE VIEW analytics_daily_summary AS
SELECT
  date(timestamp) as day,
  json_extract(data, '$.phase') as phase,
  json_extract(data, '$.model') as model,
  json_extract(data, '$.tier') as tier,
  COUNT(*) as total_calls,
  SUM(CASE WHEN event_type = 'phase_completed' THEN 1 ELSE 0 END) as successes,
  SUM(CASE WHEN event_type = 'phase_failed' THEN 1 ELSE 0 END) as failures,
  SUM(json_extract(data, '$.cost')) as total_cost,
  AVG(json_extract(data, '$.duration_ms')) as avg_latency
FROM session_stats
GROUP BY day, phase, model, tier;
```

### A.8 Estimated Total Effort

| Phase | Widgets | Effort |
|-------|---------|--------|
| Phase 1: Foundation | Escalation Waste, Model Matrix | 1 sprint |
| Phase 2: Cost Focus | Cost Trend, Sankey | 1 sprint |
| Phase 3: Diagnostics | Latency, Errors, Funnel | 1 sprint |
| Phase 4: Workflow | Outliers, Queue Health | 1 sprint |
| **Total** | 9 widgets | **4 sprints** |

### A.9 Success Metrics

| Metric | Target |
|--------|--------|
| Identify cost savings | >$10/month in first 30 days |
| Model issues detected | Catch failing models within 24 hours |
| Dashboard load time | <2s for all analytics widgets |
| Actionable recommendations | ≥1 per week based on data |

---

## Document History

| Date | Author | Changes |
|------|--------|---------|
| 2026-01-14 | Claude Code | Initial creation from V4.0 feature extraction |
| 2026-01-15 | Claude Code | Added Appendix A: Observability Dashboard Enhancements |
