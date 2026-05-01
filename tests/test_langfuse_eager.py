"""Tests for Langfuse eager initialization."""

from unittest.mock import patch


def test_langfuse_client_has_initialize_method():
    """LangfuseClient should expose an async initialize() method."""
    from api.services.langfuse_client import LangfuseClient
    import inspect

    assert hasattr(LangfuseClient, "initialize")
    assert inspect.iscoroutinefunction(LangfuseClient.initialize)


@patch("api.services.langfuse_client._get_langfuse_credential")
def test_langfuse_initialize_sets_initialized_flag(mock_cred):
    """After initialize(), _ensure_initialized should be a no-op."""
    import asyncio
    from api.services.langfuse_client import LangfuseClient

    mock_cred.return_value = None  # No credentials — init will "fail" but flag set

    client = LangfuseClient()
    assert not client._initialized

    asyncio.run(client.initialize())
    assert client._initialized
