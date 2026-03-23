"""WebSocket router for Cardigan API.

Provides real-time job status updates via WebSocket connections.
Broadcasts job status changes to all connected clients.
"""

import logging
import os
from typing import Any, Dict, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from api.models.job import Job

logger = logging.getLogger(__name__)

router = APIRouter()

# Track all active WebSocket connections
_active_connections: Set[WebSocket] = set()


class ConnectionManager:
    """Manages WebSocket connections and broadcasts."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """Remove a WebSocket connection."""
        self.active_connections.discard(websocket)
        logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")

    async def broadcast_job_update(self, job: Job, event_type: str = "job_updated"):
        """Broadcast job update to all connected clients.

        Args:
            job: The job that was updated
            event_type: Type of update (job_created, job_updated, job_completed, etc.)
        """
        if not self.active_connections:
            return

        # Convert job to dict for JSON serialization
        job_data = job.model_dump(mode='json')

        message = {
            "type": event_type,
            "job": job_data
        }

        # Broadcast to all active connections
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                disconnected.add(connection)

        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection)

    async def broadcast_stats_update(self, stats: Dict[str, Any]):
        """Broadcast queue stats update to all connected clients.

        Args:
            stats: Queue statistics dictionary
        """
        if not self.active_connections:
            return

        message = {
            "type": "stats_updated",
            "stats": stats
        }

        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send stats to WebSocket: {e}")
                disconnected.add(connection)

        for connection in disconnected:
            self.disconnect(connection)


# Global connection manager instance
manager = ConnectionManager()


@router.websocket("/ws/jobs")
async def websocket_jobs_endpoint(websocket: WebSocket, token: str = Query(default=None)):
    """WebSocket endpoint for real-time job updates.

    Clients connect to this endpoint to receive real-time notifications
    when jobs are created, updated, or completed.

    When CARDIGAN_API_KEY is set, the `token` query parameter must match.
    When the env var is empty/absent, all connections are allowed (dev mode).

    Message format:
        {
            "type": "job_created" | "job_updated" | "job_completed" | "job_failed" | "stats_updated",
            "job": { ... }  // Full job object (for job events)
            "stats": { ... } // Queue stats (for stats_updated events)
        }
    """
    api_key = os.environ.get("CARDIGAN_API_KEY", "")
    if api_key and token != api_key:
        await websocket.close(code=1008, reason="Policy Violation")
        return

    await manager.connect(websocket)

    try:
        # Keep connection alive and handle incoming messages
        while True:
            # Wait for any messages from client (can be used for ping/pong)
            data = await websocket.receive_text()

            # Echo back as heartbeat/acknowledgment
            if data == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("Client disconnected normally")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


async def broadcast_job_update(job: Job, event_type: str = "job_updated"):
    """Helper function to broadcast job updates from other modules.

    This can be imported and called from other parts of the application
    (e.g., worker, database service) to notify WebSocket clients of changes.

    Args:
        job: The job that was updated
        event_type: Type of event (job_created, job_updated, job_completed, job_failed)
    """
    await manager.broadcast_job_update(job, event_type)


async def broadcast_stats_update(stats: Dict[str, Any]):
    """Helper function to broadcast stats updates from other modules.

    Args:
        stats: Queue statistics dictionary
    """
    await manager.broadcast_stats_update(stats)


def get_connection_count() -> int:
    """Get the number of active WebSocket connections.

    Returns:
        Number of active connections
    """
    return len(manager.active_connections)
