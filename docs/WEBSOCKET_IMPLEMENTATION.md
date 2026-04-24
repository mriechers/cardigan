# WebSocket Live Updates Implementation

## Overview

Implemented real-time job status updates for The Metadata Neighborhood dashboard using WebSocket connections. This eliminates the need for constant polling and provides instant feedback when jobs are created, updated, or completed.

## Architecture

### Backend (FastAPI)

**File:** `api/routers/websocket.py`

- WebSocket endpoint at `/api/ws/jobs`
- Connection manager handles multiple simultaneous client connections
- Broadcasts job updates to all connected clients
- Supports heartbeat/ping-pong to keep connections alive
- Graceful connection/disconnection handling

**Integration:** `api/services/database.py`

- `create_job()` broadcasts "job_created" events
- `update_job()` broadcasts appropriate events based on status changes:
  - `job_started` - when status changes to `in_progress`
  - `job_completed` - when status changes to `completed`
  - `job_failed` - when status changes to `failed`
  - `job_updated` - for all other updates

### Frontend (React)

**Hook:** `web/src/hooks/useWebSocket.ts`

- Custom React hook `useJobsWebSocket()`
- Manages WebSocket connection lifecycle
- Auto-reconnects on disconnect (configurable interval)
- Heartbeat mechanism (30s ping interval)
- Callbacks for job updates and stats updates
- Graceful degradation - fallback to polling if WebSocket fails

**Integration:**

- **Queue Page** (`web/src/pages/Queue.tsx`)
  - Real-time job list updates
  - Toast notifications for completed/failed jobs
  - Fallback polling: 30s with WebSocket, 5s without

- **Home Page** (`web/src/pages/Home.tsx`)
  - Real-time stats updates
  - Recent jobs list updates
  - Fallback polling: 30s with WebSocket, 10s without

## Message Format

WebSocket messages are JSON objects:

```json
{
  "type": "job_created" | "job_updated" | "job_started" | "job_completed" | "job_failed" | "stats_updated",
  "job": { /* full Job object */ },
  "stats": { /* queue statistics */ }
}
```

## Connection Flow

1. Client connects to `ws://localhost:8100/api/ws/jobs`
2. Server accepts connection and adds to active connections
3. Server broadcasts updates when jobs change
4. Client sends "ping" every 30s, server responds with "pong"
5. On disconnect, server removes from active connections
6. Client auto-reconnects after 3s (configurable)

## Benefits

1. **Instant Updates** - No waiting for polling intervals
2. **Reduced Load** - Far fewer HTTP requests compared to polling
3. **Better UX** - Users see changes immediately
4. **Scalable** - Efficient broadcast to multiple clients
5. **Resilient** - Automatic reconnection and fallback to polling

## Testing

**Unit Tests:** `tests/test_websocket.py`

- WebSocket connection establishment
- Ping/pong heartbeat
- Connection manager functionality

**Manual Testing:**

1. Start API server: `uvicorn api.main:app --reload`
2. Start web dev server: `cd web && npm run dev`
3. Open multiple browser windows to the Queue page
4. Create/update a job via API or worker
5. Observe real-time updates in all windows

## Future Enhancements

1. Add WebSocket support to JobDetail page for live phase progress
2. Broadcast worker health status
3. Add rate limiting to prevent WebSocket spam
4. Implement authentication for WebSocket connections
5. Add compression for large job payloads

## Configuration

WebSocket connection settings can be adjusted in the hook:

```typescript
useJobsWebSocket({
  autoReconnect: true,           // Enable auto-reconnect
  reconnectInterval: 3000,       // Reconnect delay in ms
  onJobUpdate: (job, type) => { /* handler */ },
  onStatsUpdate: (stats) => { /* handler */ }
})
```

## Monitoring

Connection count can be checked via:

```python
from api.routers.websocket import get_connection_count
count = get_connection_count()
```

This could be exposed via a monitoring endpoint in the future.
