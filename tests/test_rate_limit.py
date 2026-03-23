"""Tests for rate limiting middleware."""

import pytest
from unittest.mock import patch

from starlette.testclient import TestClient

from api.main import app
from api.middleware.rate_limit import limiter


@pytest.fixture(autouse=True)
def reset_limiter():
    """Reset rate limiter state between tests."""
    limiter.reset()
    yield


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestRateLimiting:
    def test_returns_429_after_exceeding_limit(self, client, monkeypatch):
        """Verify 429 is returned when rate limit is exceeded."""
        monkeypatch.delenv("CARDIGAN_API_KEY", raising=False)

        # The root endpoint has a default rate limit via the global limiter.
        # Hammer it until we get a 429.
        got_429 = False
        for _ in range(70):
            resp = client.get("/")
            if resp.status_code == 429:
                got_429 = True
                break

        assert got_429, "Expected 429 after exceeding rate limit but never received one"
