"""Auth + scope enforcement tests for Sprint 3A — consumer keys.

Covers:
- Back-compat: shared-key path still works for existing endpoints
- Consumer-key happy path: valid key + correct scope → 200
- Scope enforcement: wrong scope → 403
- Audit log: entries written on every /api/mmingest/* hit
- last_used_at: updated after successful consumer-key auth
- 401 on invalid key (neither shared nor consumer)

The /api/mmingest/* endpoints are provided by a minimal test router registered
on the test app, since Sprint 3B's router may not be merged at test time.
"""

import os
import subprocess
import sys
import tempfile

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def migrated_engine():
    """Fresh DB with alembic upgrade head applied."""
    fd, db_path = tempfile.mkstemp(suffix="_auth_test.db")
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
async def auth_client(migrated_engine, monkeypatch):
    """TestClient with a minimal app that includes the auth middleware + a stub
    /api/mmingest/ router so middleware decisions can be tested without S3B."""
    engine, db_path = migrated_engine
    monkeypatch.setenv("DATABASE_PATH", db_path)

    # Reset db service to the new path.
    import api.services.database as db_module

    db_module._engine = None
    db_module._async_session_factory = None
    await db_module.init_db()

    from api.middleware.auth import APIKeyMiddleware

    test_app = FastAPI()
    test_app.add_middleware(APIKeyMiddleware)

    @test_app.get("/api/jobs")
    async def stub_jobs():
        return {"jobs": []}

    @test_app.get("/api/mmingest/search")
    async def stub_mmingest_search():
        return {"results": []}

    @test_app.get("/api/mmingest/recent")
    async def stub_mmingest_recent():
        return {"recent": []}

    @test_app.get("/api/mmingest/assets/{media_id}")
    async def stub_mmingest_asset(media_id: str):
        return {"media_id": media_id}

    @test_app.get("/api/mmingest/assets/{media_id}/stream")
    async def stub_mmingest_stream(media_id: str):
        return {"stream_url": f"https://example.com/{media_id}"}

    client = TestClient(test_app, raise_server_exceptions=False)
    yield client, db_path, db_module

    await db_module.close_db()
    db_module._engine = None
    db_module._async_session_factory = None


async def _create_key(label, scopes):
    from api.services.auth.consumer_keys import create_consumer_key

    return await create_consumer_key(label=label, scopes=scopes)


async def _audit_rows(db_path: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT * FROM mmingest_audit_log ORDER BY id"))
            rows = result.fetchall()
        return rows
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Back-compat tests (Gate 2)
# ---------------------------------------------------------------------------


class TestBackCompat:
    """Existing shared-key auth must continue to work after Sprint 3A."""

    def test_shared_key_allows_jobs_endpoint(self, auth_client, monkeypatch):
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret-key")
        resp = client.get("/api/jobs", headers={"X-API-Key": "shared-secret-key"})
        assert resp.status_code == 200

    def test_wrong_key_rejects_jobs_endpoint(self, auth_client, monkeypatch):
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret-key")
        resp = client.get("/api/jobs", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401

    def test_missing_key_rejects_jobs_endpoint(self, auth_client, monkeypatch):
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret-key")
        resp = client.get("/api/jobs")
        assert resp.status_code == 401

    def test_no_shared_key_env_allows_all(self, auth_client, monkeypatch):
        """Dev mode — auth disabled when CARDIGAN_API_KEY is unset."""
        client, _, _ = auth_client
        monkeypatch.delenv("CARDIGAN_API_KEY", raising=False)
        resp = client.get("/api/jobs")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Consumer-key happy path (Gate 3)
# ---------------------------------------------------------------------------


class TestConsumerKeyHappyPath:
    @pytest.mark.asyncio
    async def test_read_scope_allows_mmingest_search(self, auth_client, monkeypatch):
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")

        plaintext, _ = await _create_key("read-key", ["mmingest:read"])
        resp = client.get("/api/mmingest/search", headers={"X-API-Key": plaintext})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_read_scope_allows_mmingest_recent(self, auth_client, monkeypatch):
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")

        plaintext, _ = await _create_key("read-key-recent", ["mmingest:read"])
        resp = client.get("/api/mmingest/recent", headers={"X-API-Key": plaintext})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_stream_scope_allows_stream_endpoint(self, auth_client, monkeypatch):
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")

        plaintext, _ = await _create_key("stream-key", ["mmingest:stream"])
        resp = client.get(
            "/api/mmingest/assets/TESTMID001/stream",
            headers={"X-API-Key": plaintext},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scope enforcement (Gate 4)
# ---------------------------------------------------------------------------


class TestScopeEnforcement:
    @pytest.mark.asyncio
    async def test_stream_only_key_denied_on_search(self, auth_client, monkeypatch):
        """Key with only mmingest:stream must be denied on /search (needs mmingest:read)."""
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")

        plaintext, _ = await _create_key("stream-only", ["mmingest:stream"])
        resp = client.get("/api/mmingest/search", headers={"X-API-Key": plaintext})
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_read_only_key_denied_on_stream(self, auth_client, monkeypatch):
        """Key with only mmingest:read must be denied on /stream (needs mmingest:stream)."""
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")

        plaintext, _ = await _create_key("read-only-stream", ["mmingest:read"])
        resp = client.get(
            "/api/mmingest/assets/TESTMID002/stream",
            headers={"X-API-Key": plaintext},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_both_scopes_allow_stream(self, auth_client, monkeypatch):
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")

        plaintext, _ = await _create_key("both-scopes", ["mmingest:read", "mmingest:stream"])
        resp = client.get(
            "/api/mmingest/assets/TESTMID003/stream",
            headers={"X-API-Key": plaintext},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_no_scope_key_denied_on_mmingest(self, auth_client, monkeypatch):
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")

        plaintext, _ = await _create_key("no-scope", [])
        resp = client.get("/api/mmingest/search", headers={"X-API-Key": plaintext})
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_consumer_key_with_read_scope_can_access_non_mmingest(self, auth_client, monkeypatch):
        """Consumer keys are not restricted on non-mmingest endpoints."""
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")

        plaintext, _ = await _create_key("read-non-mmingest", ["mmingest:read"])
        resp = client.get("/api/jobs", headers={"X-API-Key": plaintext})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Audit log (Gate 5)
# ---------------------------------------------------------------------------


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_audit_entry_written_on_allowed_consumer_key(self, auth_client, monkeypatch):
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")

        plaintext, consumer_id = await _create_key("audit-allowed", ["mmingest:read"])
        client.get("/api/mmingest/search", headers={"X-API-Key": plaintext})

        rows = await _audit_rows(db_path)
        matching = [r for r in rows if r.consumer_id == consumer_id and r.outcome == "allowed"]
        assert len(matching) >= 1

    @pytest.mark.asyncio
    async def test_audit_entry_written_on_denied_consumer_key(self, auth_client, monkeypatch):
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")

        plaintext, consumer_id = await _create_key("audit-denied", ["mmingest:stream"])
        client.get("/api/mmingest/search", headers={"X-API-Key": plaintext})

        rows = await _audit_rows(db_path)
        matching = [r for r in rows if r.consumer_id == consumer_id and r.outcome == "denied"]
        assert len(matching) >= 1

    @pytest.mark.asyncio
    async def test_audit_entry_written_on_shared_key_mmingest(self, auth_client, monkeypatch):
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")

        client.get("/api/mmingest/search", headers={"X-API-Key": "shared-secret"})

        rows = await _audit_rows(db_path)
        matching = [r for r in rows if r.consumer_id is None and r.outcome == "shared_key"]
        assert len(matching) >= 1

    @pytest.mark.asyncio
    async def test_audit_entry_has_correct_path(self, auth_client, monkeypatch):
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")

        plaintext, consumer_id = await _create_key("audit-path", ["mmingest:read"])
        client.get("/api/mmingest/recent", headers={"X-API-Key": plaintext})

        rows = await _audit_rows(db_path)
        matching = [r for r in rows if r.consumer_id == consumer_id]
        assert any(r.path == "/api/mmingest/recent" for r in matching)

    @pytest.mark.asyncio
    async def test_audit_entry_extracts_media_id(self, auth_client, monkeypatch):
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")

        plaintext, consumer_id = await _create_key("audit-media-id", ["mmingest:read"])
        client.get("/api/mmingest/assets/TESTMID999", headers={"X-API-Key": plaintext})

        rows = await _audit_rows(db_path)
        matching = [r for r in rows if r.consumer_id == consumer_id]
        assert any(r.media_id == "TESTMID999" for r in matching)

    @pytest.mark.asyncio
    async def test_no_audit_entry_on_non_mmingest_path(self, auth_client, monkeypatch):
        """Audit log must NOT fire for non-mmingest endpoints."""
        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")

        before_rows = await _audit_rows(db_path)
        before_count = len(before_rows)

        client.get("/api/jobs", headers={"X-API-Key": "shared-secret"})

        after_rows = await _audit_rows(db_path)
        assert len(after_rows) == before_count


# ---------------------------------------------------------------------------
# last_used_at update (Gate 6)
# ---------------------------------------------------------------------------


class TestLastUsedAt:
    @pytest.mark.asyncio
    async def test_last_used_at_updated_after_successful_auth(self, auth_client, monkeypatch):
        """Verify touch_last_used sets last_used_at on a successful consumer-key auth.

        We call touch_last_used directly (the service-layer function) rather than
        relying on the fire-and-forget asyncio.create_task inside the middleware —
        sync TestClient doesn't pump the event loop after the response returns, so
        background tasks don't complete before we check.  The middleware contract
        (create_task → touch_last_used) is verified at the unit level by
        TestTouchLastUsed in test_consumer_keys_service.py.
        """
        import api.services.database as db_module
        from api.services.auth.consumer_keys import touch_last_used

        client, db_path, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")

        plaintext, consumer_id = await _create_key("last-used-test", ["mmingest:read"])

        # Confirm initially NULL.
        async with db_module.get_session() as session:
            result = await session.execute(
                text("SELECT last_used_at FROM consumer_keys WHERE id = :id"),
                {"id": consumer_id},
            )
            before = result.fetchone()
        assert before.last_used_at is None

        # Now simulate what the middleware does on successful auth.
        await touch_last_used(consumer_id)

        async with db_module.get_session() as session:
            result = await session.execute(
                text("SELECT last_used_at FROM consumer_keys WHERE id = :id"),
                {"id": consumer_id},
            )
            row = result.fetchone()

        assert row.last_used_at is not None


# ---------------------------------------------------------------------------
# Invalid key returns 401 (safety check)
# ---------------------------------------------------------------------------


class TestInvalidKey:
    def test_invalid_key_returns_401(self, auth_client, monkeypatch):
        client, _, _ = auth_client
        monkeypatch.setenv("CARDIGAN_API_KEY", "shared-secret")
        resp = client.get("/api/jobs", headers={"X-API-Key": "garbage-key-xyz"})
        assert resp.status_code == 401
