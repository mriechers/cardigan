"""Unit tests for api.services.auth.consumer_keys.

These tests use an isolated SQLite DB with all migrations applied so the
consumer_keys and mmingest_audit_log tables exist as in production.
"""

import os
import subprocess
import sys
import tempfile

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest_asyncio.fixture
async def migrated_engine():
    """Stand up a fresh DB via ``alembic upgrade head``, yield an async engine."""
    fd, db_path = tempfile.mkstemp(suffix="_ck_test.db")
    os.close(fd)

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    env = {**os.environ, "DATABASE_PATH": db_path}

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    yield engine, db_path

    await engine.dispose()
    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest_asyncio.fixture
async def db_session(migrated_engine):
    """Provide the DATABASE_PATH env var and a working db session for the service layer."""
    engine, db_path = migrated_engine
    os.environ["DATABASE_PATH"] = db_path

    # Reset the database service so it picks up the new path.
    import api.services.database as db_module

    db_module._engine = None
    db_module._async_session_factory = None
    await db_module.init_db()

    yield

    # Teardown — reset so subsequent tests start fresh.
    await db_module.close_db()
    db_module._engine = None
    db_module._async_session_factory = None


class TestCreateConsumerKey:
    @pytest.mark.asyncio
    async def test_returns_plaintext_and_id(self, db_session):
        from api.services.auth.consumer_keys import create_consumer_key

        plaintext, consumer_id = await create_consumer_key(label="test-key", scopes=["mmingest:read"])

        assert isinstance(plaintext, str)
        assert len(plaintext) > 20  # token_urlsafe(32) → ~43 chars
        assert isinstance(consumer_id, int)
        assert consumer_id >= 1

    @pytest.mark.asyncio
    async def test_plaintext_not_stored(self, db_session):
        """The database row must NOT contain the plaintext key."""
        import api.services.database as db_module
        from api.services.auth.consumer_keys import create_consumer_key

        plaintext, consumer_id = await create_consumer_key(label="no-plaintext", scopes=["mmingest:read"])

        async with db_module.get_session() as session:
            result = await session.execute(
                text("SELECT key_hash FROM consumer_keys WHERE id = :id"),
                {"id": consumer_id},
            )
            row = result.fetchone()

        assert row is not None
        assert row.key_hash != plaintext
        # bcrypt hashes always start with $2b$ or $2a$
        assert row.key_hash.startswith("$2")

    @pytest.mark.asyncio
    async def test_scopes_stored_as_csv(self, db_session):
        import api.services.database as db_module
        from api.services.auth.consumer_keys import create_consumer_key

        _, consumer_id = await create_consumer_key(
            label="multi-scope",
            scopes=["mmingest:stream", "mmingest:read"],
        )

        async with db_module.get_session() as session:
            result = await session.execute(
                text("SELECT scopes FROM consumer_keys WHERE id = :id"),
                {"id": consumer_id},
            )
            row = result.fetchone()

        # Stored sorted
        assert "mmingest:read" in row.scopes
        assert "mmingest:stream" in row.scopes


class TestLookupConsumerKey:
    @pytest.mark.asyncio
    async def test_lookup_returns_record_on_valid_key(self, db_session):
        from api.services.auth.consumer_keys import create_consumer_key, lookup_consumer_key

        plaintext, consumer_id = await create_consumer_key(label="lookup-test", scopes=["mmingest:read"])

        record = await lookup_consumer_key(plaintext)

        assert record is not None
        assert record.id == consumer_id
        assert record.label == "lookup-test"
        assert "mmingest:read" in record.scopes

    @pytest.mark.asyncio
    async def test_lookup_returns_none_on_wrong_key(self, db_session):
        from api.services.auth.consumer_keys import create_consumer_key, lookup_consumer_key

        await create_consumer_key(label="another", scopes=["mmingest:read"])

        result = await lookup_consumer_key("definitely-wrong-key")
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_returns_none_on_empty_string(self, db_session):
        from api.services.auth.consumer_keys import lookup_consumer_key

        result = await lookup_consumer_key("")
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_returns_frozenset_scopes(self, db_session):
        from api.services.auth.consumer_keys import create_consumer_key, lookup_consumer_key

        plaintext, _ = await create_consumer_key(
            label="scope-check",
            scopes=["mmingest:read", "mmingest:stream"],
        )

        record = await lookup_consumer_key(plaintext)
        assert isinstance(record.scopes, frozenset)
        assert record.scopes == frozenset({"mmingest:read", "mmingest:stream"})

    @pytest.mark.asyncio
    async def test_revoked_key_not_returned(self, db_session):
        from api.services.auth.consumer_keys import (
            create_consumer_key,
            lookup_consumer_key,
            revoke_consumer_key,
        )

        plaintext, consumer_id = await create_consumer_key(label="to-revoke", scopes=["mmingest:read"])

        await revoke_consumer_key(consumer_id)
        result = await lookup_consumer_key(plaintext)
        assert result is None


class TestTouchLastUsed:
    @pytest.mark.asyncio
    async def test_last_used_at_updated(self, db_session):
        import api.services.database as db_module
        from api.services.auth.consumer_keys import create_consumer_key, touch_last_used

        _, consumer_id = await create_consumer_key(label="touch-test", scopes=["mmingest:read"])

        # Verify initially NULL.
        async with db_module.get_session() as session:
            result = await session.execute(
                text("SELECT last_used_at FROM consumer_keys WHERE id = :id"),
                {"id": consumer_id},
            )
            before = result.fetchone()
        assert before.last_used_at is None

        await touch_last_used(consumer_id)

        # Verify now set.
        async with db_module.get_session() as session:
            result = await session.execute(
                text("SELECT last_used_at FROM consumer_keys WHERE id = :id"),
                {"id": consumer_id},
            )
            after = result.fetchone()
        assert after.last_used_at is not None


class TestRevokeConsumerKey:
    @pytest.mark.asyncio
    async def test_revoke_marks_inactive(self, db_session):
        import api.services.database as db_module
        from api.services.auth.consumer_keys import create_consumer_key, revoke_consumer_key

        _, consumer_id = await create_consumer_key(label="revoke-me", scopes=["mmingest:read"])

        success = await revoke_consumer_key(consumer_id)
        assert success is True

        async with db_module.get_session() as session:
            result = await session.execute(
                text("SELECT active FROM consumer_keys WHERE id = :id"),
                {"id": consumer_id},
            )
            row = result.fetchone()
        assert row.active == 0

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_returns_false(self, db_session):
        from api.services.auth.consumer_keys import revoke_consumer_key

        result = await revoke_consumer_key(999999)
        assert result is False

    @pytest.mark.asyncio
    async def test_revoke_preserves_row(self, db_session):
        """Revoked key row must still exist (soft-delete for audit trail)."""
        import api.services.database as db_module
        from api.services.auth.consumer_keys import create_consumer_key, revoke_consumer_key

        _, consumer_id = await create_consumer_key(label="preserve-me", scopes=["mmingest:read"])
        await revoke_consumer_key(consumer_id)

        async with db_module.get_session() as session:
            result = await session.execute(
                text("SELECT id FROM consumer_keys WHERE id = :id"),
                {"id": consumer_id},
            )
            row = result.fetchone()
        assert row is not None
