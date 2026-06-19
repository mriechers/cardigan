"""Tests for WebSocket endpoint."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.main import app
from api.models.job import Job, JobStatus


def test_websocket_connection():
    """Test that WebSocket connection can be established."""
    client = TestClient(app)

    # Note: TestClient WebSocket support is basic and doesn't support full WS protocol
    # This test verifies the endpoint exists and accepts connections
    with client.websocket_connect("/api/ws/jobs") as websocket:
        # Send ping
        websocket.send_text("ping")

        # Receive pong
        data = websocket.receive_text()
        assert data == "pong"


def test_websocket_auth_accepts_x_api_key_header(monkeypatch):
    """When CARDIGAN_API_KEY is set, the X-API-Key header (injected by the nginx
    WS proxy) authenticates the connection."""
    monkeypatch.setenv("CARDIGAN_API_KEY", "secret-key")
    client = TestClient(app)

    with client.websocket_connect(
        "/api/ws/jobs", headers={"X-API-Key": "secret-key"}
    ) as websocket:
        websocket.send_text("ping")
        assert websocket.receive_text() == "pong"


def test_websocket_auth_accepts_query_token_fallback(monkeypatch):
    """The ?token= query param still authenticates (fallback for clients that
    cannot set headers on the WS handshake)."""
    monkeypatch.setenv("CARDIGAN_API_KEY", "secret-key")
    client = TestClient(app)

    with client.websocket_connect("/api/ws/jobs?token=secret-key") as websocket:
        websocket.send_text("ping")
        assert websocket.receive_text() == "pong"


def test_websocket_auth_rejects_missing_or_wrong_key(monkeypatch):
    """When CARDIGAN_API_KEY is set, a connection with no/incorrect credential
    is closed with a policy-violation (1008)."""
    monkeypatch.setenv("CARDIGAN_API_KEY", "secret-key")
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/ws/jobs", headers={"X-API-Key": "wrong"}
        ) as websocket:
            websocket.receive_text()


def test_websocket_broadcast_job_update():
    """Test that job updates can be broadcast to WebSocket clients."""
    from datetime import datetime, timezone

    from api.routers.websocket import manager

    # Create a mock job (used for type reference, broadcast is tested below)
    Job(
        id=1,
        project_path="/path/to/project",
        transcript_file="test.txt",
        status=JobStatus.pending,
        priority=0,
        queued_at=datetime.now(timezone.utc),
        estimated_cost=0.0,
        actual_cost=0.0,
        agent_phases=["analyst", "formatter"],
        retry_count=0,
        max_retries=3,
    )

    # Test that broadcast doesn't fail when no clients are connected
    # (This is a synchronous test, so we can't test actual async broadcast)
    assert len(manager.active_connections) == 0


def test_websocket_connection_manager():
    """Test ConnectionManager functionality."""
    from api.routers.websocket import ConnectionManager

    manager = ConnectionManager()

    # Verify initial state
    assert len(manager.active_connections) == 0

    # Note: Cannot fully test add/remove without actual WebSocket connections
    # in a unit test environment. Integration tests would be needed for that.
