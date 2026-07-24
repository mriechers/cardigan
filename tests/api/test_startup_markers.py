"""Tests for the app-lifecycle markers (restart time + deploy time).

`database.record_startup_markers` writes ``api_restarted_at`` on every boot and
``deployed_version`` / ``version_deployed_at`` only when the version changes, and
``GET /api/system/health`` surfaces them under an ``instance`` block. These exercise
the producer (the three branches) and the API shape — the monitor-side consumer is
covered separately in ``tests/test_monitor.py``.
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from api.main import app
from api.services.database import close_db, get_config, init_db, record_startup_markers, set_config

_PAST = "2020-01-01T00:00:00+00:00"


@pytest_asyncio.fixture
async def temp_db():
    """A hermetic temp DB so marker writes don't leak into the shared session DB."""
    import api.services.database as db_mod

    orig_engine = db_mod._engine
    orig_factory = db_mod._async_session_factory
    orig_db_path = os.environ.get("DATABASE_PATH")

    db_mod._engine = None
    db_mod._async_session_factory = None
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DATABASE_PATH"] = db_path

    await init_db()
    from api.services.database import _engine, metadata

    async with _engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    yield db_path

    await close_db()
    db_mod._engine = orig_engine
    db_mod._async_session_factory = orig_factory
    if orig_db_path is not None:
        os.environ["DATABASE_PATH"] = orig_db_path
    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_first_boot_stamps_both_markers(temp_db):
    await record_startup_markers("4.3.0")

    restarted = await get_config("api_restarted_at")
    deployed_ver = await get_config("deployed_version")
    deployed_at = await get_config("version_deployed_at")

    assert restarted and restarted.value
    assert deployed_ver.value == "4.3.0"
    # On a virgin DB both markers are stamped with the same boot timestamp.
    assert deployed_at.value == restarted.value


@pytest.mark.asyncio
async def test_same_version_restart_preserves_deploy_marker(temp_db):
    # Simulate a prior deploy of the same version at a fixed past time.
    await set_config("deployed_version", "4.3.0", value_type="string")
    await set_config("version_deployed_at", _PAST, value_type="string")

    await record_startup_markers("4.3.0")

    # Restart marker is (re)written; deploy marker stays put — a restart is not a deploy.
    assert (await get_config("api_restarted_at")).value
    assert (await get_config("version_deployed_at")).value == _PAST
    assert (await get_config("deployed_version")).value == "4.3.0"


@pytest.mark.asyncio
async def test_version_change_moves_deploy_marker(temp_db):
    await set_config("deployed_version", "4.2.0", value_type="string")
    await set_config("version_deployed_at", _PAST, value_type="string")

    await record_startup_markers("4.3.0")

    assert (await get_config("deployed_version")).value == "4.3.0"
    assert (await get_config("version_deployed_at")).value != _PAST  # moved to now


@pytest.mark.asyncio
async def test_blank_prior_version_is_treated_as_a_deploy(temp_db):
    # Guards the `not prev.value` branch: an empty stored version must re-stamp.
    await set_config("deployed_version", "", value_type="string")
    await set_config("version_deployed_at", _PAST, value_type="string")

    await record_startup_markers("4.3.0")

    assert (await get_config("deployed_version")).value == "4.3.0"
    assert (await get_config("version_deployed_at")).value != _PAST


def test_health_endpoint_exposes_instance_block():
    """GET /api/system/health returns the version/restart/deploy markers."""
    client = TestClient(app)

    def _cfg(value):
        item = MagicMock()
        item.value = value
        return item

    async def fake_get_config(key):
        return {
            "api_restarted_at": _cfg("2026-07-23T00:00:00+00:00"),
            "version_deployed_at": _cfg("2026-07-20T00:00:00+00:00"),
        }.get(
            key
        )  # llm_runtime_status → None (falls back to in-process status)

    mock_client = MagicMock()
    mock_client.get_status.return_value = {
        "active_backend": None,
        "active_model": None,
        "primary_backend": "openrouter",
        "fallback_model": "anthropic/claude-sonnet-4",
        "phase_backends": {},
        "last_run_totals": None,
    }

    with (
        patch("api.main.get_llm_client", return_value=mock_client),
        patch("api.main.database.get_config", side_effect=fake_get_config),
        patch("api.main.database.list_jobs", new_callable=AsyncMock, return_value=[]),
    ):
        response = client.get("/api/system/health")

    assert response.status_code == 200
    inst = response.json()["instance"]
    assert inst["restarted_at"] == "2026-07-23T00:00:00+00:00"
    assert inst["version_deployed_at"] == "2026-07-20T00:00:00+00:00"
    assert inst["version"]  # __version__ is always present


def test_health_instance_block_null_when_markers_absent():
    """Older DBs without the markers report null, not an error."""
    client = TestClient(app)

    async def fake_get_config(key):
        return None  # no markers recorded yet

    mock_client = MagicMock()
    mock_client.get_status.return_value = {
        "active_backend": None,
        "active_model": None,
        "primary_backend": "openrouter",
        "fallback_model": None,
        "phase_backends": {},
        "last_run_totals": None,
    }

    with (
        patch("api.main.get_llm_client", return_value=mock_client),
        patch("api.main.database.get_config", side_effect=fake_get_config),
        patch("api.main.database.list_jobs", new_callable=AsyncMock, return_value=[]),
    ):
        response = client.get("/api/system/health")

    assert response.status_code == 200
    inst = response.json()["instance"]
    assert inst["restarted_at"] is None
    assert inst["version_deployed_at"] is None
