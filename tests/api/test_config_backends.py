"""Tests for the backend-definition CRUD API (/api/config/backends).

Step 3 of the self-updating local-model backends design: a user adds a local
OpenAI-compatible endpoint and it becomes a discoverable, routable backend —
keyed by host so it self-identifies, with no code or config-file hand-edit.
"""
import json

import pytest


@pytest.fixture
def cfg_path(monkeypatch, tmp_path):
    """A throwaway config the CRUD endpoints read/write, so tests never touch
    the committed config/llm-config.json."""
    cfg = {
        "primary_backend": "openrouter",
        "backends": {
            "openrouter": {
                "type": "openrouter",
                "endpoint": "https://openrouter.ai/api/v1/chat/completions",
            }
        },
        "phase_backends": {"analyst": "openrouter"},
        "phase_models": {},
    }
    p = tmp_path / "llm-config.json"
    p.write_text(json.dumps(cfg))
    monkeypatch.setattr("api.routers.config.CONFIG_PATH", p)
    return p


def _backends(cfg_path):
    return json.loads(cfg_path.read_text())["backends"]


def test_create_backend_is_host_keyed_with_defaults(api_client, cfg_path):
    resp = api_client.post(
        "/api/config/backends",
        json={"endpoint": "http://studio.riechers.co:8000/v1", "api_key_env": "LOCAL_LLM_API_KEY"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "studio.riechers.co:8000"  # host-keyed, self-identifying

    entry = _backends(cfg_path)["studio.riechers.co:8000"]
    assert entry["type"] == "openai"
    assert entry["endpoint"] == "http://studio.riechers.co:8000/v1"
    assert entry["enabled"] is True
    assert entry["discover"] is True
    assert entry["cost_per_project"] == 0.0
    assert entry["api_key_env"] == "LOCAL_LLM_API_KEY"


def test_create_backend_rejects_duplicate_host(api_client, cfg_path):
    body = {"endpoint": "http://studio.riechers.co:8000/v1"}
    assert api_client.post("/api/config/backends", json=body).status_code == 201
    assert api_client.post("/api/config/backends", json=body).status_code == 409


def test_create_backend_rejects_invalid_endpoint(api_client, cfg_path):
    resp = api_client.post("/api/config/backends", json={"endpoint": "not-a-url"})
    assert resp.status_code == 400


def test_list_backends_includes_created(api_client, cfg_path):
    api_client.post("/api/config/backends", json={"endpoint": "http://studio.riechers.co:8000/v1"})
    resp = api_client.get("/api/config/backends")
    assert resp.status_code == 200
    names = {b["name"] for b in resp.json()["backends"]}
    assert {"openrouter", "studio.riechers.co:8000"} <= names


def test_patch_backend_toggles_flags(api_client, cfg_path):
    api_client.post("/api/config/backends", json={"endpoint": "http://studio.riechers.co:8000/v1"})
    resp = api_client.patch(
        "/api/config/backends/studio.riechers.co:8000",
        json={"enabled": False, "discover": False},
    )
    assert resp.status_code == 200, resp.text
    entry = _backends(cfg_path)["studio.riechers.co:8000"]
    assert entry["enabled"] is False and entry["discover"] is False


def test_patch_unknown_backend_404(api_client, cfg_path):
    assert api_client.patch("/api/config/backends/nope:1", json={"enabled": False}).status_code == 404


def test_delete_backend(api_client, cfg_path):
    api_client.post("/api/config/backends", json={"endpoint": "http://studio.riechers.co:8000/v1"})
    resp = api_client.delete("/api/config/backends/studio.riechers.co:8000")
    assert resp.status_code == 204
    assert "studio.riechers.co:8000" not in _backends(cfg_path)


def test_delete_referenced_backend_conflicts(api_client, cfg_path):
    # openrouter is primary_backend AND used by phase_backends -> must not orphan routing
    assert api_client.delete("/api/config/backends/openrouter").status_code == 409
