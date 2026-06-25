"""Service-level tests for ingest scan-result persistence (#75).

Exercises the real config-table round-trip for record_scan_result -> get_ingest_config
so a failed scan's detail is queryable (and cleared on the next success), rather than
being swallowed by a broad except and lost to container logs.
"""

import pytest

from api.services.ingest_config import get_ingest_config, record_scan_result


@pytest.mark.asyncio
async def test_record_scan_result_persists_error_on_failure():
    """A failed scan stores its error detail and flips last_scan_success to False."""
    await record_scan_result(success=False, error="UNIQUE constraint failed: available_files.remote_url")

    cfg = await get_ingest_config()
    assert cfg.last_scan_success is False
    assert cfg.last_scan_error == "UNIQUE constraint failed: available_files.remote_url"
    assert cfg.last_scan_at is not None


@pytest.mark.asyncio
async def test_record_scan_result_clears_error_on_success():
    """A subsequent successful scan clears the stale error (no lingering message)."""
    await record_scan_result(success=False, error="SkyCloud timeout")
    # Sanity: error is set before the success clears it.
    assert (await get_ingest_config()).last_scan_error == "SkyCloud timeout"

    await record_scan_result(success=True)

    cfg = await get_ingest_config()
    assert cfg.last_scan_success is True
    assert cfg.last_scan_error is None


@pytest.mark.asyncio
async def test_record_scan_result_supplies_default_message_when_no_detail():
    """A failure with no detail still records a non-empty, queryable message."""
    await record_scan_result(success=False, error=None)

    assert (await get_ingest_config()).last_scan_error == "Scan failed (no detail captured)"


@pytest.mark.asyncio
async def test_record_scan_result_truncates_oversized_error():
    """A giant traceback is capped so it can't bloat the config row."""
    await record_scan_result(success=False, error="x" * 5000)

    error = (await get_ingest_config()).last_scan_error
    assert error is not None
    assert len(error) <= 2000
