from unittest.mock import AsyncMock, patch

import pytest

from api.services import model_roster


@pytest.mark.asyncio
async def test_newest_in_family_picks_newest_excludes_variants():
    raw = [
        {"id": "anthropic/claude-opus-4-6", "created": 100},
        {"id": "anthropic/claude-opus-4-8", "created": 300},  # newest opus
        {"id": "anthropic/claude-opus-4-8-fast", "created": 400},  # excluded (fast)
        {"id": "anthropic/claude-fable-5", "created": 500},  # excluded (fable)
        {"id": "anthropic/claude-sonnet-4-6", "created": 200},
        {"id": "openai/gpt-4o", "created": 999},  # wrong provider
    ]
    with patch.object(model_roster, "fetch_openrouter_models", AsyncMock(return_value=raw)):
        got = await model_roster.newest_in_family("opus", ["fast", "fable"])
    assert got == "anthropic/claude-opus-4-8"


@pytest.mark.asyncio
async def test_newest_in_family_none_on_fetch_failure():
    with patch.object(model_roster, "fetch_openrouter_models", AsyncMock(return_value=None)):
        assert await model_roster.newest_in_family("opus", ["fast"]) is None
