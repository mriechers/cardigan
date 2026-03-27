"""Pytest configuration for Cardigan v4 tests."""

import asyncio
import os
import tempfile

import pytest

from api.services import database


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    """Initialize a test database for the session.

    Instead of using TestClient lifespan (which hangs on shutdown),
    directly initialize the database engine for tests.
    """
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DATABASE_PATH"] = db_path

    # Initialize DB engine and create tables
    loop = asyncio.new_event_loop()
    loop.run_until_complete(database.init_db())
    loop.close()

    yield

    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest.fixture(scope="session")
def api_client(_init_test_db):
    """Provide a TestClient for API tests."""
    from fastapi.testclient import TestClient

    from api.main import app

    # Don't use context manager — lifespan shutdown hangs due to scheduler.
    # DB is already initialized by _init_test_db fixture above.
    client = TestClient(app, raise_server_exceptions=True)
    yield client
