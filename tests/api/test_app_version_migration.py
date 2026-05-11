"""Migration 011: app_version column exists on jobs, session_stats, chat_sessions."""

import os
import tempfile

import pytest
import pytest_asyncio
from sqlalchemy import text

from api.services import database as db_mod


@pytest_asyncio.fixture
async def fresh_db():
    """Create a fresh DB with all migrations applied via init_db()."""
    orig_engine = db_mod._engine
    orig_factory = db_mod._async_session_factory
    db_mod._engine = None
    db_mod._async_session_factory = None

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    os.environ["DATABASE_PATH"] = db_path
    try:
        await db_mod.init_db()
        yield db_path
    finally:
        await db_mod.close_db()
        db_mod._engine = orig_engine
        db_mod._async_session_factory = orig_factory
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_app_version_column_exists_on_jobs(fresh_db):
    async with db_mod.get_session() as s:
        cols = [r[1] for r in (await s.execute(text("PRAGMA table_info(jobs)"))).fetchall()]
    assert "app_version" in cols


@pytest.mark.asyncio
async def test_app_version_column_exists_on_session_stats(fresh_db):
    async with db_mod.get_session() as s:
        cols = [r[1] for r in (await s.execute(text("PRAGMA table_info(session_stats)"))).fetchall()]
    assert "app_version" in cols


@pytest.mark.asyncio
async def test_app_version_column_exists_on_chat_sessions(fresh_db):
    async with db_mod.get_session() as s:
        cols = [r[1] for r in (await s.execute(text("PRAGMA table_info(chat_sessions)"))).fetchall()]
    assert "app_version" in cols
