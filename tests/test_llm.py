"""Tests for LLM service layer — credit-exhaustion error detection."""

from unittest.mock import AsyncMock

import pytest

from api.services.llm import CreditExhaustedError, LLMClient


class FakeResp402:
    """Minimal fake httpx response for a 402 Payment Required (credit exhausted)."""

    status_code = 402
    text = '{"error":{"message":"Insufficient credits"}}'

    def json(self):
        return {"error": {"message": "Insufficient credits"}}


async def test_call_openrouter_raises_credit_exhausted_on_402(monkeypatch):
    """_call_openrouter raises CreditExhaustedError on HTTP 402 before raise_for_status."""
    client = LLMClient.__new__(LLMClient)
    client.active_backend = "openrouter"

    # _post_openrouter is the seam isolating the HTTP boundary; patch it to 402.
    monkeypatch.setattr(client, "_post_openrouter", AsyncMock(return_value=FakeResp402()))

    with pytest.raises(CreditExhaustedError):
        await client._call_openrouter(
            config={"endpoint": "https://openrouter.ai/api/v1/chat/completions"},
            model="anthropic/x",
            messages=[],
            api_key="fake-key",
        )
