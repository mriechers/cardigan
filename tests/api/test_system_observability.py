"""Tests for cross-container observability (#158, #179).

The API container cannot see the worker/watcher containers via pgrep/lsof, so
/system/status falls back to shared-DB heartbeats and /system/health reads a
worker-published LLM snapshot. These tests exercise that fallback logic with the
local process probes forced to "not found" (the prod/LXC shape).
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


class TestSystemStatusHeartbeat:
    """GET /api/system/status worker/watcher detection via DB heartbeat (#179)."""

    def test_worker_up_via_fresh_heartbeat_without_local_process(self):
        """A fresh DB heartbeat reports the worker running even when pgrep finds nothing."""
        with (
            patch("api.routers.system._check_port_in_use", return_value=1234),
            patch("api.routers.system._find_process", return_value=None),
            patch(
                "api.routers.system.database.get_heartbeat_age_seconds",
                new_callable=AsyncMock,
            ) as mock_age,
        ):
            # worker fresh (5s), watcher stale-missing (None)
            mock_age.side_effect = [5.0, None]
            response = client.get("/api/system/status")

        assert response.status_code == 200
        data = response.json()
        assert data["worker"]["running"] is True
        assert data["worker"]["pid"] is None  # cross-container: no local pid
        assert data["worker"]["heartbeat_age_seconds"] == 5.0
        assert data["watcher"]["running"] is False

    def test_worker_down_when_no_process_and_no_heartbeat(self):
        """No local process and no heartbeat -> reported down (no false green)."""
        with (
            patch("api.routers.system._check_port_in_use", return_value=1234),
            patch("api.routers.system._find_process", return_value=None),
            patch(
                "api.routers.system.database.get_heartbeat_age_seconds",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            response = client.get("/api/system/status")

        assert response.status_code == 200
        data = response.json()
        assert data["worker"]["running"] is False
        assert data["watcher"]["running"] is False

    def test_stale_heartbeat_reports_down(self):
        """A heartbeat older than the stale window is not considered alive."""
        with (
            patch("api.routers.system._check_port_in_use", return_value=1234),
            patch("api.routers.system._find_process", return_value=None),
            patch(
                "api.routers.system.database.get_heartbeat_age_seconds",
                new_callable=AsyncMock,
                return_value=9999.0,
            ),
        ):
            response = client.get("/api/system/status")

        assert response.json()["worker"]["running"] is False

    def test_local_process_still_reports_up_in_dev(self):
        """Single-host dev: pgrep finds the process even with no heartbeat."""
        with (
            patch("api.routers.system._check_port_in_use", return_value=1234),
            patch("api.routers.system._find_process", return_value=4321),
            patch(
                "api.routers.system.database.get_heartbeat_age_seconds",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            response = client.get("/api/system/status")

        data = response.json()
        assert data["worker"]["running"] is True
        assert data["worker"]["pid"] == 4321


class TestWatcherHeartbeatEndpoint:
    """POST /api/system/watcher/heartbeat records watcher liveness (#179)."""

    def test_records_heartbeat(self):
        with patch("api.routers.system.database.record_heartbeat", new_callable=AsyncMock) as mock_record:
            response = client.post("/api/system/watcher/heartbeat")

        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_record.assert_awaited_once_with("watcher")


class TestHealthLLMSnapshot:
    """GET /api/system/health prefers the worker-published DB snapshot (#158)."""

    def test_health_uses_db_runtime_when_inprocess_null(self):
        """active_backend/model/last_run come from the DB snapshot when the API
        process has none (the multi-container case)."""
        in_process_status = {
            "active_backend": None,
            "active_model": None,
            "primary_backend": "openrouter",
            "fallback_model": "anthropic/claude-sonnet-4",
            "phase_backends": {"analyst": "openrouter"},
            "last_run_totals": None,
        }
        runtime_snapshot = MagicMock()
        runtime_snapshot.value = json.dumps(
            {
                "active_backend": "openrouter",
                "active_model": "anthropic/claude-opus-4",
                "last_run_totals": {"cost": 0.1359},
            }
        )

        mock_client = MagicMock()
        mock_client.get_status.return_value = in_process_status

        async def fake_get_config(key):
            return runtime_snapshot if key == "llm_runtime_status" else None

        with (
            patch("api.main.get_llm_client", return_value=mock_client),
            patch("api.main.database.get_config", side_effect=fake_get_config),
            patch("api.main.database.list_jobs", new_callable=AsyncMock, return_value=[]),
        ):
            response = client.get("/api/system/health")

        assert response.status_code == 200
        data = response.json()
        assert data["llm"]["active_backend"] == "openrouter"
        assert data["llm"]["active_model"] == "anthropic/claude-opus-4"
        assert data["last_run"] == {"cost": 0.1359}
        # config-derived fields still come from the in-process client
        assert data["llm"]["primary_backend"] == "openrouter"
