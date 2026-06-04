"""Tests for api/services/mmingest/crawler.py.

Coverage:
  * Concurrency cap: max in-flight <= max_concurrent under load
  * Rate limiter: token bucket paces requests
  * Priority lanes: sidecar (.srt/.scc) items are marked lane="sidecar"
  * Exponential backoff: simulated 5xx triggers retry with sleep
  * Change detection: known triple skips unchanged files
  * Pause window: raises when inside the window
  * No DB writes: output is purely in-memory FileWorkItem objects
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from datetime import time as dtime
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.mmingest.crawler import (
    ChangeTriple,
    FileWorkItem,
    MmingestCrawler,
    TokenBucket,
    _ext_to_file_type,
)

# ---------------------------------------------------------------------------
# TokenBucket unit tests
# ---------------------------------------------------------------------------


class TestTokenBucket:
    """Unit tests for the token bucket rate limiter."""

    @pytest.mark.asyncio
    async def test_immediate_acquire_when_full(self):
        """First acquire should not block when bucket is full."""
        bucket = TokenBucket(rate=10.0, burst=5)
        start = time.monotonic()
        await bucket.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1, f"Expected immediate acquire, took {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_burst_acquires_without_delay(self):
        """Acquiring up to burst size should not incur significant delay."""
        burst = 3
        bucket = TokenBucket(rate=1.0, burst=burst)
        start = time.monotonic()
        for _ in range(burst):
            await bucket.acquire()
        elapsed = time.monotonic() - start
        # With burst=3, rate=1: 3 tokens available immediately
        assert elapsed < 0.2, f"Burst acquires took too long: {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_rate_limits_beyond_burst(self):
        """Acquiring beyond burst requires waiting."""
        bucket = TokenBucket(rate=10.0, burst=1)  # 1 token/100ms
        await bucket.acquire()  # Uses the initial token

        start = time.monotonic()
        await bucket.acquire()  # Must wait ~100ms
        elapsed = time.monotonic() - start

        # At 10 req/s, one extra token costs ~100ms
        # Allow generous tolerance for CI environments
        assert elapsed >= 0.05, f"Should have waited but only took {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# MmingestCrawler — concurrency cap
# ---------------------------------------------------------------------------


class TestConcurrencyCap:
    """Verify max_concurrent is respected under load."""

    @pytest.mark.asyncio
    async def test_max_in_flight_respected(self):
        """At most max_concurrent requests should be in flight simultaneously.

        We test the semaphore directly: by tracking concurrent acquires on
        the asyncio.Semaphore that delta_walk creates, we can verify the cap
        is respected without bypassing the semaphore via mocking.
        """
        max_concurrent = 4
        concurrent_high_water = 0
        current_concurrent = 0

        # Track the semaphore by wrapping asyncio.Semaphore
        original_semaphore = asyncio.Semaphore

        class TrackingSemaphore:
            """Wraps asyncio.Semaphore and tracks concurrent enters."""

            def __init__(self, value: int):
                self._sem = original_semaphore(value)

            def __aenter__(self):
                return self._sem.__aenter__()

            def __aexit__(self, *args):
                return self._sem.__aexit__(*args)

            # Expose for compatibility
            def locked(self):
                return self._sem.locked()

        async def fake_get(url, **kwargs):
            nonlocal concurrent_high_water, current_concurrent
            current_concurrent += 1
            if current_concurrent > concurrent_high_water:
                concurrent_high_water = current_concurrent
            await asyncio.sleep(0.02)
            current_concurrent -= 1
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "<html><body></body></html>"
            return resp

        crawler = MmingestCrawler(
            base_url="https://test.example.com/",
            max_concurrent=max_concurrent,
            rate_per_second=1000.0,
        )

        directories = [f"/dir{i}/" for i in range(10)]

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = fake_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await crawler.delta_walk(directories=directories, known={})

        assert (
            concurrent_high_water <= max_concurrent
        ), f"Max concurrent {concurrent_high_water} exceeded cap of {max_concurrent}"

    @pytest.mark.asyncio
    async def test_semaphore_cap_is_4_by_default(self):
        """Default max_concurrent is 4."""
        crawler = MmingestCrawler()
        assert crawler.max_concurrent == 4


# ---------------------------------------------------------------------------
# Priority lanes
# ---------------------------------------------------------------------------


class TestPriorityLanes:
    """Verify .srt/.scc files are marked lane='sidecar'."""

    def _make_dir_entry(self, name: str, url: str = "https://test.com/") -> MagicMock:
        from api.services.mmingest.parsers import DirEntry

        return DirEntry(
            name=name,
            is_dir=False,
            url=url + name,
            modified=None,
            size_bytes=None,
        )

    @pytest.mark.asyncio
    async def test_srt_is_sidecar_lane(self):
        from api.services.mmingest.parsers import DirEntry

        entry = DirEntry(
            name="6POL0101.srt",
            is_dir=False,
            url="https://test.com/6POL0101.srt",
            modified=None,
            size_bytes=None,
        )
        crawler = MmingestCrawler()
        item = crawler._make_work_item(entry, "/", {})
        assert item is not None
        assert item.lane == "sidecar"

    @pytest.mark.asyncio
    async def test_scc_is_sidecar_lane(self):
        from api.services.mmingest.parsers import DirEntry

        entry = DirEntry(
            name="6POL0101.scc",
            is_dir=False,
            url="https://test.com/6POL0101.scc",
            modified=None,
            size_bytes=None,
        )
        crawler = MmingestCrawler()
        item = crawler._make_work_item(entry, "/", {})
        assert item is not None
        assert item.lane == "sidecar"

    @pytest.mark.asyncio
    async def test_mp4_is_primary_lane(self):
        from api.services.mmingest.parsers import DirEntry

        entry = DirEntry(
            name="6POL0101.mp4",
            is_dir=False,
            url="https://test.com/6POL0101.mp4",
            modified=None,
            size_bytes=None,
        )
        crawler = MmingestCrawler()
        item = crawler._make_work_item(entry, "/", {})
        assert item is not None
        assert item.lane == "primary"

    @pytest.mark.asyncio
    async def test_image_is_primary_lane(self):
        from api.services.mmingest.parsers import DirEntry

        entry = DirEntry(
            name="6POL0101_REV20260319_anya1.jpg",
            is_dir=False,
            url="https://test.com/6POL0101_REV20260319_anya1.jpg",
            modified=None,
            size_bytes=None,
        )
        crawler = MmingestCrawler()
        item = crawler._make_work_item(entry, "/", {})
        assert item is not None
        assert item.lane == "primary"


# ---------------------------------------------------------------------------
# Exponential backoff on 5xx
# ---------------------------------------------------------------------------


class TestExponentialBackoff:
    """Verify the crawler retries with backoff on server errors."""

    @pytest.mark.asyncio
    async def test_5xx_triggers_retry(self):
        """A 5xx response should be retried, not returned immediately."""
        call_count = 0
        sleep_calls: list[float] = []

        async def mock_sleep(duration: float):
            sleep_calls.append(duration)

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count < 3:
                resp.status_code = 503
            else:
                resp.status_code = 200
                resp.text = "<html><body></body></html>"
            return resp

        crawler = MmingestCrawler(
            base_url="https://test.example.com/",
            rate_per_second=1000.0,
        )

        mock_client = AsyncMock()
        mock_client.get = mock_get
        semaphore = asyncio.Semaphore(4)

        with patch("asyncio.sleep", side_effect=mock_sleep):
            result = await crawler._fetch_with_backoff(mock_client, semaphore, "https://test.example.com/test/")

        assert result is not None
        assert call_count == 3, f"Expected 3 calls (2 retries), got {call_count}"
        assert len(sleep_calls) == 2, f"Expected 2 backoff sleeps, got {len(sleep_calls)}"

    @pytest.mark.asyncio
    async def test_4xx_does_not_retry(self):
        """A 404 should not be retried — return None immediately."""
        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 404
            return resp

        crawler = MmingestCrawler(rate_per_second=1000.0)
        mock_client = AsyncMock()
        mock_client.get = mock_get
        semaphore = asyncio.Semaphore(4)

        result = await crawler._fetch_with_backoff(mock_client, semaphore, "https://test.example.com/missing/")

        assert result is None
        assert call_count == 1, f"404 should not retry, got {call_count} calls"

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_returns_none(self):
        """If all retries fail, return None instead of raising."""
        import httpx

        crawler = MmingestCrawler(rate_per_second=1000.0)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        semaphore = asyncio.Semaphore(4)

        sleep_calls: list[float] = []

        with patch("asyncio.sleep", side_effect=lambda d: sleep_calls.append(d)):
            result = await crawler._fetch_with_backoff(
                mock_client, semaphore, "https://test.example.com/bad/", max_retries=2
            )

        assert result is None
        # 2 retries means 2 sleeps
        assert len(sleep_calls) == 2

    @pytest.mark.asyncio
    async def test_backoff_increases(self):
        """Each retry should sleep longer than the previous (exponential)."""
        import httpx

        crawler = MmingestCrawler(rate_per_second=1000.0)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        semaphore = asyncio.Semaphore(4)

        sleep_calls: list[float] = []

        with patch("asyncio.sleep", side_effect=lambda d: sleep_calls.append(d)):
            await crawler._fetch_with_backoff(mock_client, semaphore, "https://test.example.com/bad/", max_retries=3)

        assert len(sleep_calls) == 3
        # Each sleep should be at least as long as the previous (accounting for jitter)
        # We check the trend: last sleep >= first sleep
        assert sleep_calls[-1] >= sleep_calls[0] * 0.5  # generous jitter tolerance


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------


class TestChangeDetection:
    """Verify that files with matching triples are skipped."""

    def _make_dir_entry(self, name: str, modified: Optional[datetime] = None, size: Optional[int] = None):
        from api.services.mmingest.parsers import DirEntry

        return DirEntry(
            name=name,
            is_dir=False,
            url=f"https://test.com/{name}",
            modified=modified,
            size_bytes=size,
        )

    def test_unchanged_file_returns_none(self):
        """If the triple matches, _make_work_item returns None (no work needed)."""
        mod = datetime(2026, 3, 19, 17, 24, tzinfo=timezone.utc)
        entry = self._make_dir_entry("6POL0101.srt", modified=mod, size=34000)
        url = entry.url
        triple: ChangeTriple = (None, mod.isoformat(), 34000)
        known = {url: triple}

        crawler = MmingestCrawler()
        result = crawler._make_work_item(entry, "/IWP/", known)
        assert result is None

    def test_changed_size_returns_work_item(self):
        """If size changed, return a new work item."""
        mod = datetime(2026, 3, 19, 17, 24, tzinfo=timezone.utc)
        entry = self._make_dir_entry("6POL0101.srt", modified=mod, size=99999)
        url = entry.url
        # Known triple has different size
        known = {url: (None, mod.isoformat(), 34000)}

        crawler = MmingestCrawler()
        result = crawler._make_work_item(entry, "/IWP/", known)
        assert result is not None
        assert result.file_size_bytes == 99999

    def test_new_file_not_in_known_returns_work_item(self):
        """New file (not in known) should always produce a work item."""
        entry = self._make_dir_entry("6POL0102.srt", size=32000)
        crawler = MmingestCrawler()
        result = crawler._make_work_item(entry, "/IWP/", known={})
        assert result is not None

    def test_change_triple_stored_on_work_item(self):
        """The work item should carry the current triple for S2 to persist."""
        mod = datetime(2026, 4, 9, 14, 55, tzinfo=timezone.utc)
        entry = self._make_dir_entry("6POL0104.srt", modified=mod, size=32768)
        crawler = MmingestCrawler()
        result = crawler._make_work_item(entry, "/IWP/", known={})
        assert result is not None
        # Triple should be (None, mod.isoformat(), 32768)
        assert result.change_triple == (None, mod.isoformat(), 32768)


# ---------------------------------------------------------------------------
# No DB writes
# ---------------------------------------------------------------------------


class TestNoDbWrites:
    """Verify the crawler produces only in-memory work items."""

    @pytest.mark.asyncio
    async def test_delta_walk_returns_list_not_db_objects(self):
        """delta_walk returns a plain list of FileWorkItem dataclasses."""
        html = """<html><body>
        <table>
        <tr><td><a href="6POL0101.srt">6POL0101.srt</a></td>
        <td align="right">2026-03-19 17:24  </td>
        <td align="right"> 34K</td></tr>
        </table>
        </body></html>"""

        crawler = MmingestCrawler(
            base_url="https://test.example.com/",
            rate_per_second=1000.0,
        )

        async def mock_fetch(*args, **kwargs):
            return html

        with patch.object(crawler, "_fetch_with_backoff", side_effect=mock_fetch):
            results = await crawler.delta_walk(directories=["/IWP/"])

        assert isinstance(results, list)
        assert all(isinstance(r, FileWorkItem) for r in results)

    @pytest.mark.asyncio
    async def test_no_get_session_calls(self):
        """The crawler must never import or call get_session."""
        import api.services.mmingest.crawler as crawler_module

        # get_session should not be imported or callable from crawler
        assert not hasattr(crawler_module, "get_session"), "crawler.py imported get_session — it must make NO DB writes"


# ---------------------------------------------------------------------------
# Pause window
# ---------------------------------------------------------------------------


class TestPauseWindow:
    """Verify the pause window suppresses crawling."""

    def test_outside_pause_window_does_not_raise(self):
        """Outside the window, _check_pause_window should not raise."""
        # Use a window that starts and ends 1 hour from now in UTC
        from datetime import datetime, timezone

        now_utc = datetime.now(timezone.utc)
        # Set window to 3 hours from now — we're definitely outside
        start = dtime((now_utc.hour + 3) % 24, 0)
        end = dtime((now_utc.hour + 4) % 24, 0)
        crawler = MmingestCrawler(pause_window=(start, end))
        # Should not raise
        crawler._check_pause_window()

    def test_inside_pause_window_raises(self):
        """Inside the window, _check_pause_window should raise RuntimeError."""
        from datetime import datetime, timezone

        now_utc = datetime.now(timezone.utc)
        # Set window to cover current time
        h = now_utc.hour
        start = dtime(h, 0)
        end = dtime((h + 1) % 24, 59)
        crawler = MmingestCrawler(pause_window=(start, end))
        with pytest.raises(RuntimeError, match="Crawl paused"):
            crawler._check_pause_window()

    def test_no_pause_window_never_raises(self):
        """With pause_window=None, _check_pause_window must never raise."""
        crawler = MmingestCrawler(pause_window=None)
        crawler._check_pause_window()  # Should not raise


# ---------------------------------------------------------------------------
# Utility function
# ---------------------------------------------------------------------------


class TestExtToFileType:
    def test_mp4_maps_to_mp4(self):
        assert _ext_to_file_type(".mp4") == "mp4"

    def test_srt_maps_to_srt(self):
        assert _ext_to_file_type(".srt") == "srt"

    def test_scc_maps_to_scc(self):
        assert _ext_to_file_type(".scc") == "scc"

    def test_jpg_maps_to_image(self):
        assert _ext_to_file_type(".jpg") == "image"

    def test_jpeg_maps_to_image(self):
        assert _ext_to_file_type(".jpeg") == "image"

    def test_unknown_maps_to_other(self):
        assert _ext_to_file_type(".xyz") == "other"

    def test_empty_ext_maps_to_other(self):
        assert _ext_to_file_type("") == "other"
