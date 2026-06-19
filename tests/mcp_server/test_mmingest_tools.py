"""Tests for the three mmingest MCP tools (HTTP-backed since 2026-06).

The tools used to query an in-process SQLite engine (Sprint 4A, Option B —
duplicated SQL).  They now proxy the Cardigan HTTP API at EDITORIAL_API_URL so
they always read the live deployment's index rather than whatever local
``dashboard.db`` happens to sit next to the MCP process.

These tests therefore mock ``mcp_server.server._mmingest_api_get`` (the single
HTTP seam) and verify the parts the MCP layer still owns:

  * client-side input guards (empty query, bad ISO datetime) short-circuit
    BEFORE any HTTP call,
  * tool args map onto the API's query contract (``query`` -> ``q``, limit
    caps, default ``since`` window),
  * the JSON envelope each endpoint returns is formatted into the expected
    markdown,
  * transport / status errors (404, 500, unreachable) become friendly
    TextContent instead of tracebacks.

The SQL-level filtering behaviour (prefix/since/FTS ranking) is covered against
the real DB in tests/api/test_mmingest_router.py — it is the router's job now,
not the MCP tool's.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from mcp_server.server import (
    handle_get_mmingest_asset,
    handle_list_recent_mmingest_assets,
    handle_search_mmingest,
)

API_GET = "mcp_server.server._mmingest_api_get"


# ---------------------------------------------------------------------------
# search_mmingest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_happy_path_formatting():
    """A search hit is rendered with media_id, show prefix, kind, and snippet."""
    payload = {
        "results": [
            {
                "media_id": "6POL0101",
                "prefix": "6POL",
                "season": "01",
                "episode": "01",
                "revision_date": "2026-03-19",
                "modified_at": "2026-03-19T10:00:00",
                "snippet": "inside wisconsin <b>politics</b> legislature",
                "sidecar_kind": "srt",
            }
        ],
        "total": 1,
    }
    with patch(API_GET, new=AsyncMock(return_value=(payload, 200, None))):
        results = await handle_search_mmingest({"query": "politics"})

    text_out = results[0].text
    assert "6POL0101" in text_out
    assert "6POL" in text_out
    assert "srt" in text_out
    assert "politics" in text_out.lower()
    assert "showing 1" in text_out


@pytest.mark.asyncio
async def test_search_param_mapping():
    """Tool args map onto the API contract: query->q, since parsed, limit capped."""
    mock_get = AsyncMock(return_value=({"results": [], "total": 0}, 200, None))
    with patch(API_GET, new=mock_get):
        await handle_search_mmingest(
            {
                "query": "politics",
                "prefix": "6POL",
                "since": "2026-03-01T00:00:00",
                "limit": 500,  # over the 100 cap
            }
        )

    mock_get.assert_awaited_once()
    path, params = mock_get.await_args.args
    assert path == "/api/mmingest/search"
    assert params["q"] == "politics"
    assert params["prefix"] == "6POL"
    assert params["since"] == "2026-03-01T00:00:00"
    assert params["limit"] == 100  # clamped


@pytest.mark.asyncio
async def test_search_empty_query_guard_no_http():
    """Empty/whitespace query returns an error WITHOUT calling the API."""
    mock_get = AsyncMock()
    with patch(API_GET, new=mock_get):
        for bad_query in ["", "   "]:
            results = await handle_search_mmingest({"query": bad_query})
            assert "Error" in results[0].text
    mock_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_invalid_since_guard_no_http():
    """A malformed ISO 'since' is rejected client-side before any API call."""
    mock_get = AsyncMock()
    with patch(API_GET, new=mock_get):
        results = await handle_search_mmingest({"query": "politics", "since": "not-a-date"})
    assert "invalid ISO 8601" in results[0].text
    mock_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_fts5_syntax_error_maps_500():
    """The API returns 500 on malformed FTS5; the tool surfaces a friendly hint."""
    with patch(API_GET, new=AsyncMock(return_value=(None, 500, None))):
        results = await handle_search_mmingest({"query": '"unclosed phrase'})
    text_out = results[0].text
    assert "FTS5" in text_out
    assert "Traceback" not in text_out


@pytest.mark.asyncio
async def test_search_empty_results_message():
    """Zero results yields a friendly 'No results found' message echoing filters."""
    with patch(API_GET, new=AsyncMock(return_value=({"results": [], "total": 0}, 200, None))):
        results = await handle_search_mmingest({"query": "zzzznope", "prefix": "6POL"})
    text_out = results[0].text
    assert "No results found" in text_out
    assert "6POL" in text_out


@pytest.mark.asyncio
async def test_search_transport_error_surfaced():
    """A transport error string from the helper is returned verbatim."""
    msg = "Error: could not reach mmingest API at https://x/api/mmingest/search (ConnectError)."
    with patch(API_GET, new=AsyncMock(return_value=(None, None, msg))):
        results = await handle_search_mmingest({"query": "politics"})
    assert results[0].text == msg


# ---------------------------------------------------------------------------
# get_mmingest_asset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_asset_primary_only():
    """Primary-only asset renders the Primary section with the Airtable link."""
    payload = {
        "primary": {
            "file_id": 1,
            "media_id": "6POL0101",
            "variant_tag": None,
            "revision_date": "2026-03-19",
            "url": "http://mmingest.example/6POL0101.mp4",
            "file_type": "mp4",
            "remote_modified_at": "2026-03-19T10:00:00",
            "file_size_bytes": 1000,
            "airtable_record_id": "recTEST123456",
        },
        "variants": [],
        "superseded": [],
    }
    with patch(API_GET, new=AsyncMock(return_value=(payload, 200, None))):
        results = await handle_get_mmingest_asset({"media_id": "6POL0101"})

    text_out = results[0].text
    assert "6POL0101" in text_out
    assert "Primary" in text_out
    assert "recTEST123456" in text_out
    assert "Variants" in text_out
    assert "Superseded" in text_out


@pytest.mark.asyncio
async def test_get_asset_with_variant():
    """A PLEDGE variant appears in the Variants section."""
    payload = {
        "primary": {
            "media_id": "6POL0101",
            "url": "http://mmingest.example/6POL0101.mp4",
            "file_type": "mp4",
            "revision_date": None,
            "remote_modified_at": None,
            "airtable_record_id": None,
        },
        "variants": [
            {
                "variant_tag": "PLEDGE",
                "url": "http://mmingest.example/6POL0101_PLEDGE.mp4",
                "file_type": "mp4",
            }
        ],
        "superseded": [],
    }
    with patch(API_GET, new=AsyncMock(return_value=(payload, 200, None))):
        results = await handle_get_mmingest_asset({"media_id": "6POL0101"})

    text_out = results[0].text
    assert "Primary" in text_out
    assert "PLEDGE" in text_out
    assert "6POL0101_PLEDGE.mp4" in text_out
    assert "(not linked)" in text_out  # no airtable_record_id


@pytest.mark.asyncio
async def test_get_asset_not_found_maps_404():
    """A 404 from the API becomes a friendly 'No asset found' message."""
    with patch(API_GET, new=AsyncMock(return_value=(None, 404, None))):
        results = await handle_get_mmingest_asset({"media_id": "NONEXISTENT999"})
    text_out = results[0].text
    assert "No asset found" in text_out
    assert "NONEXISTENT999" in text_out


@pytest.mark.asyncio
async def test_get_asset_empty_media_id_guard_no_http():
    """An empty media_id is rejected before any API call."""
    mock_get = AsyncMock()
    with patch(API_GET, new=mock_get):
        results = await handle_get_mmingest_asset({"media_id": "  "})
    assert "Error" in results[0].text
    mock_get.assert_not_awaited()


# ---------------------------------------------------------------------------
# list_recent_mmingest_assets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_happy_path_formatting():
    """A recent arrival is rendered with media_id, show name, and url."""
    payload = {
        "results": [
            {
                "media_id": "6POL0102",
                "prefix": "6POL",
                "show_name": "Wisconsin Politics",
                "file_type": "mp4",
                "url": "http://mmingest.example/6POL0102.mp4",
                "first_seen_at": "2026-06-01T00:00:00",
                "remote_modified_at": "2026-06-01T00:00:00",
            }
        ],
        "total": 1,
    }
    with patch(API_GET, new=AsyncMock(return_value=(payload, 200, None))):
        results = await handle_list_recent_mmingest_assets({})

    text_out = results[0].text
    assert "6POL0102" in text_out
    assert "Wisconsin Politics" in text_out
    assert "http://mmingest.example/6POL0102.mp4" in text_out
    assert "showing 1" in text_out


@pytest.mark.asyncio
async def test_recent_param_mapping_default_since_and_cap():
    """No 'since' defaults to ~24h ago; limit is clamped to 200."""
    mock_get = AsyncMock(return_value=({"results": [], "total": 0}, 200, None))
    with patch(API_GET, new=mock_get):
        await handle_list_recent_mmingest_assets({"limit": 250})

    path, params = mock_get.await_args.args
    assert path == "/api/mmingest/recent"
    assert params["limit"] == 200  # clamped
    # since defaults to a timestamp roughly 24h in the past
    since_dt = datetime.fromisoformat(params["since"])
    age_hours = (datetime.now(timezone.utc) - since_dt).total_seconds() / 3600
    assert 23.0 < age_hours < 25.0


@pytest.mark.asyncio
async def test_recent_invalid_since_guard_no_http():
    """A malformed ISO 'since' is rejected client-side before any API call."""
    mock_get = AsyncMock()
    with patch(API_GET, new=mock_get):
        results = await handle_list_recent_mmingest_assets({"since": "garbage"})
    assert "invalid ISO 8601" in results[0].text
    mock_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_recent_empty_result_message():
    """An empty window yields a friendly 'No new arrivals' message."""
    with patch(API_GET, new=AsyncMock(return_value=({"results": [], "total": 0}, 200, None))):
        results = await handle_list_recent_mmingest_assets({})
    assert "no new arrivals" in results[0].text.lower()
