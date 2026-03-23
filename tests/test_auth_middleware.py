"""Tests for API key authentication middleware."""

import pytest
from unittest.mock import patch, AsyncMock

from starlette.testclient import TestClient

from api.main import app


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestAuthDisabled:
    """When CARDIGAN_API_KEY is not set, all requests should pass through."""

    def test_request_allowed_without_key_env(self, client, monkeypatch):
        monkeypatch.delenv("CARDIGAN_API_KEY", raising=False)
        resp = client.get("/")
        assert resp.status_code == 200

    def test_protected_endpoint_allowed_without_key_env(self, client, monkeypatch):
        monkeypatch.delenv("CARDIGAN_API_KEY", raising=False)
        resp = client.get("/api/system/health")
        # Health endpoint may 500 if DB isn't initialized, but should not 401.
        assert resp.status_code != 401


class TestAuthEnabled:
    """When CARDIGAN_API_KEY is set, non-exempt requests need X-API-Key header."""

    def test_valid_key_allowed(self, client, monkeypatch):
        monkeypatch.setenv("CARDIGAN_API_KEY", "test-secret-key")
        resp = client.get("/api/queue/", headers={"X-API-Key": "test-secret-key"})
        # May be 200 or other non-401 status depending on DB state
        assert resp.status_code != 401

    def test_missing_key_rejected(self, client, monkeypatch):
        monkeypatch.setenv("CARDIGAN_API_KEY", "test-secret-key")
        resp = client.get("/api/queue/")
        assert resp.status_code == 401
        assert "API key" in resp.json()["detail"]

    def test_wrong_key_rejected(self, client, monkeypatch):
        monkeypatch.setenv("CARDIGAN_API_KEY", "test-secret-key")
        resp = client.get("/api/queue/", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401

    def test_exempt_root(self, client, monkeypatch):
        monkeypatch.setenv("CARDIGAN_API_KEY", "test-secret-key")
        resp = client.get("/")
        assert resp.status_code == 200

    def test_exempt_health(self, client, monkeypatch):
        monkeypatch.setenv("CARDIGAN_API_KEY", "test-secret-key")
        resp = client.get("/api/system/health")
        # Health endpoint may 500 if DB isn't initialized, but should not 401.
        assert resp.status_code != 401

    def test_exempt_docs(self, client, monkeypatch):
        monkeypatch.setenv("CARDIGAN_API_KEY", "test-secret-key")
        resp = client.get("/docs")
        # FastAPI docs redirect or return HTML, but not 401
        assert resp.status_code != 401

    def test_exempt_openapi(self, client, monkeypatch):
        monkeypatch.setenv("CARDIGAN_API_KEY", "test-secret-key")
        resp = client.get("/openapi.json")
        assert resp.status_code != 401

    def test_exempt_websocket_path(self, client, monkeypatch):
        """WS upgrade path is exempt from HTTP auth (uses token query param instead)."""
        monkeypatch.setenv("CARDIGAN_API_KEY", "test-secret-key")
        # A plain GET to the WS path won't upgrade, but should not get 401.
        resp = client.get("/api/ws/jobs")
        assert resp.status_code != 401
