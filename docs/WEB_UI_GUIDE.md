# Web Dashboard User Guide

The PBS Wisconsin Editorial Assistant dashboard provides a web interface for managing transcript processing, reviewing outputs, and configuring the system.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Ready for Work](#ready-for-work)
3. [Queue](#queue)
4. [Job Detail](#job-detail)
5. [Projects](#projects)
6. [Settings](#settings)
7. [Keyboard Shortcuts](#keyboard-shortcuts)
8. [Accessibility](#accessibility)
9. [Troubleshooting](#troubleshooting)
10. [API Reference](#api-reference)

---

## Getting Started

### Accessing the Dashboard

The dashboard is available at:

- **Local:** `http://metadata.neighborhood:3000` (requires `./scripts/start.sh`)
- **Remote:** `https://cardigan.bymarkriechers.com` (via Cloudflare Tunnel, requires `ENABLE_TUNNEL=true` in `.env`)

The API server must be running on port 8000 for the dashboard to function. Both are started together by `./scripts/start.sh`. For remote access setup, see [Remote Access](REMOTE_ACCESS.md).

### First-Time Orientation

When you first open the dashboard, you'll land on the **Queue** — your primary workspace. The navigation bar at the top provides access to all sections:

- **Ready for Work** — Discover and queue new transcripts
- **Queue** — Manage all jobs (pending, processing, completed, failed)
- **Projects** — Browse completed work and review outputs
- **Settings** — Configure agents, routing, system components, and preferences
- **Help** — This guide, troubleshooting, and API reference

The dashboard updates in real time via WebSocket. If the WebSocket connection drops, it falls back to periodic polling so you never lose visibility into job status.

---

## Ready for Work

This page lets you discover transcript files from the ingest server and queue them for processing.

### Scanning for Files

Click **Check for New Files** to scan the ingest server for available transcripts. The page displays the last scan timestamp so you know how fresh the results are.

### Filtering Files

- **Search**: Type a filename or Media ID to filter the list (updates as you type)
- **Date Range**: Filter by how recently files were added (7, 14, 30, 60, or 90 days)
- **Clear Filters**: When filters are active, a summary appears with a button to clear them

### Queueing Files

- **Queue one**: Click the green **Queue** button next to any file
- **Queue many**: Check the boxes next to multiple files, then click **Queue Selected**
- **Select all**: Use the header checkbox to select all visible files at once
- **Ignore**: Click **Ignore** to remove a file from the list (it won't appear again until the next scan)

---

## Queue

The Queue page is your command center for managing all jobs.

### Status Filters

Tabs across the top let you filter by status. Each tab shows a live count:

- **All** — Every job
- **Pending** — Waiting to start
- **Processing** — Currently running
- **Completed** — Finished successfully
- **Failed** — Encountered errors
- **Cancelled** — Manually stopped

### Searching

Use the search bar to find jobs by transcript filename. The search updates as you type (with a short delay to avoid excessive queries). Search terms are saved in the URL, so you can bookmark or share filtered views.

### Uploading Transcripts

Click the **+ Upload** button to open the transcript uploader. You can drag and drop `.srt` files or click to browse. Uploaded transcripts are automatically queued for processing.

### Job Actions

| Action | Available When | What It Does |
|--------|----------------|--------------|
| **Prioritize** | Pending | Moves the job to the top of the queue |
| **Cancel** | Pending | Stops the job before it starts (asks for confirmation) |
| **View Details** | Any status | Opens the full Job Detail page |

### Bulk Actions

- **Clear Failed/Cancelled**: Removes all failed and cancelled jobs from the queue

### Pagination

Jobs are displayed 50 per page. Use the pagination controls at the bottom to navigate through larger queues.

---

## Job Detail

Click any job to see its full processing details.

### Job Header

The header shows:
- Job ID and transcript filename
- Current status with color-coded badge
- Priority level
- Creation and completion timestamps

### Phase Breakdown

Each job goes through up to 7 processing phases:

| Phase | Purpose |
|-------|---------|
| **Analyst** | Initial transcript analysis |
| **Formatter** | AP Style transcript formatting |
| **SEO** | Metadata and keyword generation |
| **Manager** | Quality review and coordination |
| **Timestamp** | Timecode extraction |
| **Copy Editor** | Final editorial polish |
| **Recovery** | Error recovery (if needed) |

For each phase, you can see:
- **Status** — pending, processing, completed, or failed
- **Cost** — API cost for that phase
- **Tokens** — Token usage (input + output)
- **Model** — Which LLM model and tier were used
- **Timing** — Start and end timestamps
- **Retries** — How many attempts were needed

### Output Files

Completed jobs produce output files that you can view directly in the dashboard:
- Analysis, Formatted Transcript, SEO Metadata, QA Review, Timestamps, Copy Edited, and Recovery Analysis

Click any output file to view it in a modal with full markdown rendering.

### Job Actions

| Action | Available When | What It Does |
|--------|----------------|--------------|
| **Pause** | Processing | Pauses the job after the current phase completes |
| **Resume** | Paused | Resumes a paused job |
| **Retry** | Failed | Re-runs the job from the failed phase |
| **Cancel** | Pending/Processing | Stops the job |

### Airtable Integration

If the job has an associated Media ID, the detail page shows linked Airtable SST metadata (title, description, URLs) and provides a link to the Media Manager.

### Screengrabs

When screengrabs are available for a Media ID, an **Attach Screengrabs** button appears. This opens a panel where you can review and attach screengrab images to the project.

---

## Projects

The Projects page lets you browse completed work and review the outputs produced.

### Browsing Projects

- **Search**: Filter projects by transcript filename
- **Select**: Click a project to view its artifacts
- **Paginate**: Results are shown 50 per page

### Viewing Artifacts

When you select a project, you'll see:
- All output files produced during processing
- Markdown-rendered previews of each artifact
- SST metadata from Airtable (if linked)
- Cost breakdown by phase
- Timing information

---

## Settings

The Settings page has 6 tabs for configuring different aspects of the system.

### Agents

Configure the base processing tier for each agent type. Three tiers are available:

| Tier | Color | Description |
|------|-------|-------------|
| **Fast** | Green | Quickest processing, lower cost |
| **Balanced** | Cyan | Middle ground for most work |
| **Capable** | Purple | Highest quality, higher cost |

### Routing

Control how jobs are routed to different model tiers:

- **Duration-based routing**: Set transcript duration thresholds (in minutes) that trigger tier escalation
- **Failure escalation**: Automatically escalate to a higher tier when a job fails or times out
  - Toggle auto-escalation on/off
  - Toggle escalate-on-failure
  - Toggle escalate-on-timeout
  - Set timeout threshold (30–300 seconds)

### Worker

- **Concurrent jobs**: How many jobs run simultaneously (1–5, default 3)
- **Poll interval**: How often the worker checks for new jobs (1–30 seconds, default 5s)

### Ingest

Configure the automated transcript scanner:

- **Enable/disable** the scanner
- **Scan interval**: How often to check for new files (1–168 hours, default 24h)
- **Preferred scan time**: Set a specific time of day for scans (24-hour format)
- Shows the last scan status and next scheduled scan

### System

Monitor and control backend components:

- **Component status**: See if the API, Worker, and Transcript Watcher are running
- **Controls**: Start, Stop, or Restart individual components
- **Reference**: Folder paths for transcripts, outputs, and logs

### Accessibility

See the [Accessibility](#accessibility) section below for details on these preferences.

### Saving Changes

When you modify any setting, a **Save Changes** button appears at the bottom. Changes are not applied until you save. Use **Reset** to discard unsaved changes.

---

## Keyboard Shortcuts

Press **?** anywhere in the dashboard to see available shortcuts:

| Shortcut | Action |
|----------|--------|
| `g` then `q` | Go to Queue |
| `g` then `r` | Go to Ready for Work |
| `g` then `p` | Go to Projects |
| `g` then `s` | Go to Settings |
| `/` | Focus search (on Queue page) |
| `?` | Show keyboard shortcuts |
| `Esc` | Close modal or help overlay |

Keyboard shortcuts are automatically disabled when you're typing in a text field, textarea, or other input element.

---

## Accessibility

The dashboard includes built-in accessibility features and user-configurable preferences.

### Built-In Features

- **Skip navigation**: Press Tab when the page loads to reveal a "Skip to main content" link
- **Keyboard navigation**: All interactive elements are reachable via Tab/Shift+Tab
- **Screen reader support**: Semantic HTML, ARIA labels, live regions for status updates
- **Focus management**: Modals trap focus and restore it when closed
- **Color-coded badges**: Status indicators use both color and text labels

### User Preferences

Configure these in **Settings > Accessibility**:

| Preference | What It Does |
|------------|--------------|
| **Reduce Motion** | Disables animations and transitions throughout the dashboard |
| **Text Size** | Choose from Default (16px), Large (18px), or Larger (20px) base font size |
| **High Contrast** | Increases color contrast for better visual clarity |

### System Detection

The dashboard automatically detects your operating system preferences:
- If your OS has "reduce motion" enabled, the dashboard respects it
- If your OS has "increase contrast" enabled, the dashboard detects it

Your manual selections in Settings override the system defaults.

### Preference Persistence

All accessibility preferences are saved in your browser's local storage. They persist across sessions and browser restarts — you only need to set them once.

---

## Troubleshooting

### Dashboard Shows "Offline"

If the status bar shows "Offline" or the dashboard can't reach the API:

1. **Check if Docker containers are running**
   ```bash
   docker compose ps
   ```

2. **Restart the containers**
   ```bash
   docker compose restart
   ```

3. **Check container logs for errors**
   ```bash
   docker compose logs -f
   ```

4. **Rebuild and restart** (if containers won't start)
   ```bash
   docker compose up --build -d
   ```

### Jobs Stuck in Processing

If jobs appear stuck:

1. Check the worker container logs: `docker compose logs -f cardigan-api`
2. Verify OpenRouter API connectivity
3. Try restarting: `docker compose restart`

### Database Issues

The SQLite database is stored at `/data/db/dashboard.db` inside the container. If you suspect corruption:

1. Stop the containers: `docker compose down`
2. Back up the database file from the mounted volume
3. Restart: `docker compose up -d`

---

## API Reference

The Cardigan API is available at port 8100 (Docker) or proxied through the dev server at port 3000.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/system/health` | Health check and system status |
| `GET` | `/api/system/status` | Component status (API, worker, watcher) |
| `POST` | `/api/queue/` | Add a job to the processing queue |
| `GET` | `/api/queue/` | List jobs with filtering and pagination |
| `GET` | `/api/queue/stats` | Queue statistics by status |
| `GET` | `/api/jobs/:id` | Get job details |
| `PATCH` | `/api/jobs/:id` | Update job (cancel, prioritize, etc.) |
| `GET` | `/api/config/routing` | Get routing configuration |
| `PATCH` | `/api/config/routing` | Update routing configuration |
| `GET` | `/api/config/models` | Get model assignments |
| `PATCH` | `/api/config/models` | Update model assignments |
| `GET` | `/api/config/worker` | Get worker configuration |
| `PATCH` | `/api/config/worker` | Update worker configuration |
| `GET` | `/api/ingest/config` | Get ingest scanner configuration |
| `PUT` | `/api/ingest/config` | Update ingest scanner configuration |
| `GET` | `/api/langfuse/model-stats` | Model usage statistics |
| `GET` | `/api/langfuse/phase-stats` | Per-phase processing statistics |
| `GET` | `/api/langfuse/status` | Langfuse connection status |
