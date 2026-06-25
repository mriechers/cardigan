"""Pytest configuration for Cardigan v4 tests."""

import os

# Must be set before any app modules are imported (disables rate limiter)
os.environ["TESTING"] = "1"

import asyncio
import tempfile

import pytest

from api.services import database

# Path of the shared, session-scoped test DB. Stashed at session init so the
# per-test healing fixture below can restore it after a test swaps it out.
_SHARED_DB_PATH: str | None = None


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    """Initialize a test database for the session.

    Instead of using TestClient lifespan (which hangs on shutdown),
    directly initialize the database engine for tests.
    """
    global _SHARED_DB_PATH
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DATABASE_PATH"] = db_path
    _SHARED_DB_PATH = db_path

    # Initialize DB engine and create tables
    loop = asyncio.new_event_loop()
    loop.run_until_complete(database.init_db())
    loop.close()

    yield

    _SHARED_DB_PATH = None
    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _restore_shared_db():
    """Heal the shared session DB after any test that points DATABASE_PATH at its
    own migrated DB and nulls the database-module globals (e.g. the consumer-key
    fixtures in test_consumer_key_auth.py / test_consumer_keys_service.py).

    Those fixtures call ``close_db()`` and set ``_engine``/``_async_session_factory``
    to ``None`` on teardown but never re-init the session DB. Because ``_init_test_db``
    is session-scoped (runs once), the first such test leaves the factory ``None`` for
    the rest of the run, so every later test that reaches ``get_session()`` fails with
    "Database not initialized. Call init_db() first." — the dominant cause of the
    full-sweep failures tracked in #200 (surfaced once #102's hang was fixed).

    This is an autouse function-scoped fixture, so it sets up before any test-requested
    fixture and therefore tears down *after* them — i.e. after the polluting fixture has
    nulled the globals. It only re-inits when drift is detected, keeping the common path
    cheap.
    """
    yield

    if _SHARED_DB_PATH is None:
        return

    drifted = database._async_session_factory is None or os.environ.get("DATABASE_PATH") != _SHARED_DB_PATH
    if not drifted:
        return

    os.environ["DATABASE_PATH"] = _SHARED_DB_PATH
    database._engine = None
    database._async_session_factory = None
    loop = asyncio.new_event_loop()
    loop.run_until_complete(database.init_db())
    loop.close()


@pytest.fixture(scope="session")
def api_client(_init_test_db):
    """Provide a TestClient for API tests."""
    from fastapi.testclient import TestClient

    from api.main import app

    # Don't use context manager — lifespan shutdown hangs due to scheduler.
    # DB is already initialized by _init_test_db fixture above.
    client = TestClient(app, raise_server_exceptions=True)
    yield client
