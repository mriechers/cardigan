"""mmingest incremental delta crawler — Sprint 1B.

Polite async crawler for mmingest.pbswi.wisc.edu (Apache 2.4.46 mod_autoindex
on Windows; a production asset server — treat it gently).

Design:
  * Bounded async queue: max ``max_concurrent`` requests in flight (default 4).
  * Token-bucket rate limiter: configurable requests/second.
  * Exponential backoff with jitter on 5xx / timeout / connect error.
  * Configurable pause window: suppress crawling during broadcast traffic hours.
  * Change detection via ``(etag, last_modified, content_length)`` triple.
  * Two priority lanes: sidecar work (.srt/.scc) drains before MP4/other work.
  * NO DB writes — output is in-memory ``FileWorkItem`` dataclasses for S2.

Usage::

    crawler = MmingestCrawler(base_url="https://mmingest.pbswi.wisc.edu/")
    work_items = await crawler.delta_walk(
        directories=["/IWP/"],
        known={"https://mmingest.pbswi.wisc.edu/IWP/6POL0101.srt": ("etag1", "2026-03-19", 34000)},
    )
    # work_items contains only new/changed files
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from datetime import time as dtime
from typing import Optional

import httpx

from api.services.mmingest.parsers import (
    AutoindexParser,
    DirEntry,
    ParsedFilename,
    ParseError,
    parse_filename,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Work-item dataclasses (NO DB ties — S2 wires persistence)
# ---------------------------------------------------------------------------

# HTTP change-detection triple: (etag, last_modified, content_length)
# Any element may be None if the server did not supply the header.
ChangeTriple = tuple[Optional[str], Optional[str], Optional[int]]


@dataclass
class FileWorkItem:
    """One discovered/changed file to be indexed by S2.

    Fields mirror ``mmingest_files`` columns (migration 015) so the indexer
    can do a straightforward upsert without re-parsing.
    """

    # Location
    url: str
    directory_path: str
    filename: str

    # Parsed metadata (None if filename did not match grammar)
    media_id: Optional[str]
    prefix: Optional[str]
    prefix_category: str  # "broadcast" | "non-broadcast" | "unknown"
    show_name: Optional[str]
    season: Optional[int]
    episode: Optional[int]
    hd: Optional[bool]
    revision_date: Optional[str]
    variant_tag: Optional[str]
    unknown_tag: Optional[str]

    # File type derived from extension
    file_type: str  # "mp4" | "srt" | "scc" | "image" | "other"

    # HTTP metadata from directory listing
    remote_modified_at: Optional[datetime]
    file_size_bytes: Optional[int]

    # Change-detection triple as seen this crawl (for S2 to persist)
    change_triple: ChangeTriple = field(default_factory=lambda: (None, None, None))

    # Priority lane: "sidecar" (.srt/.scc) drains first; "primary" (.mp4/other) second
    lane: str = "primary"


@dataclass
class DirWorkItem:
    """Represents a subdirectory that needs to be crawled."""

    url: str
    path: str
    depth: int


# ---------------------------------------------------------------------------
# Token bucket rate limiter
# ---------------------------------------------------------------------------


class TokenBucket:
    """Simple thread-safe token bucket for rate limiting."""

    def __init__(self, rate: float, burst: int = 1) -> None:
        """
        Args:
            rate:  Tokens (requests) refilled per second.
            burst: Maximum tokens that can accumulate.
        """
        self._rate = rate
        self._burst = burst
        self._tokens: float = float(burst)
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return

            # Need to wait
            wait_time = (1.0 - self._tokens) / self._rate
            self._tokens = 0.0

        await asyncio.sleep(wait_time)


# ---------------------------------------------------------------------------
# Main crawler
# ---------------------------------------------------------------------------

# File extensions routed to the sidecar lane (high priority for search)
_SIDECAR_EXTS: frozenset[str] = frozenset({".srt", ".scc"})

# Maximum recursion depth when walking directories
_DEFAULT_MAX_DEPTH = 4

# Backoff config
_BACKOFF_BASE = 1.0  # seconds
_BACKOFF_MAX = 60.0  # seconds
_BACKOFF_JITTER = 0.5  # random multiplier range


class MmingestCrawler:
    """Incremental delta walker for mmingest.pbswi.wisc.edu.

    Emits ``FileWorkItem`` dataclasses for each new or changed file.  Makes
    no database writes.
    """

    def __init__(
        self,
        base_url: str = "https://mmingest.pbswi.wisc.edu/",
        max_concurrent: int = 4,
        rate_per_second: float = 2.0,
        timeout_seconds: int = 30,
        max_depth: int = _DEFAULT_MAX_DEPTH,
        ignore_directories: Optional[list[str]] = None,
        pause_window: Optional[tuple[dtime, dtime]] = None,
        auth: Optional[tuple[str, str]] = None,
    ) -> None:
        """
        Args:
            base_url:         Root URL of the ingest server.
            max_concurrent:   Maximum simultaneous in-flight HTTP requests (default 4).
            rate_per_second:  Token-bucket refill rate (requests/sec, default 2.0).
            timeout_seconds:  Per-request HTTP timeout.
            max_depth:        Maximum directory recursion depth.
            ignore_directories: Directory names to skip (matched case-insensitively).
            pause_window:     (start_time, end_time) window during which crawling
                              is suspended (e.g. broadcast traffic hours).
                              Times are compared against UTC wall clock.
                              If start > end, the window wraps midnight.
            auth:             Optional (username, password) tuple for HTTP Basic Auth.
        """
        self.base_url = base_url.rstrip("/")
        self.max_concurrent = max_concurrent
        self.timeout = timeout_seconds
        self.max_depth = max_depth
        self.ignore_directories: frozenset[str] = frozenset(d.strip("/").lower() for d in (ignore_directories or []))
        self.pause_window = pause_window
        self.auth = auth
        self._rate_limiter = TokenBucket(rate=rate_per_second, burst=max_concurrent)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def delta_walk(
        self,
        directories: Optional[list[str]] = None,
        known: Optional[dict[str, ChangeTriple]] = None,
    ) -> list[FileWorkItem]:
        """Walk configured directories and return work items for new/changed files.

        Args:
            directories: List of root paths to crawl (default: ["/"]).
                         Paths should start with "/" and may include trailing slash.
            known:       Dict mapping URL -> ChangeTriple for already-indexed files.
                         Files whose triple matches are skipped (no change).

        Returns:
            List of FileWorkItem for files that are new or have changed since
            the last crawl.  Order is not guaranteed.  Sidecar items are
            marked with ``lane="sidecar"`` for priority processing by S2.
        """
        if directories is None:
            directories = ["/"]
        if known is None:
            known = {}

        # Validate pause window before starting
        self._check_pause_window()

        # Semaphore enforces the concurrency cap
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            auth=httpx.BasicAuth(*self.auth) if self.auth else None,
        ) as client:
            tasks = []
            for dir_path in directories:
                url = f"{self.base_url}{dir_path.rstrip('/')}/"
                task = asyncio.create_task(
                    self._walk_directory(
                        client=client,
                        semaphore=semaphore,
                        url=url,
                        path=dir_path,
                        depth=0,
                        known=known,
                        visited=set(),
                        ancestor_segments=set(),
                    )
                )
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)

        work_items: list[FileWorkItem] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("Directory walk error: %s", result)
            else:
                work_items.extend(result)

        return work_items

    # ------------------------------------------------------------------
    # Internal walking logic
    # ------------------------------------------------------------------

    async def _walk_directory(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        url: str,
        path: str,
        depth: int,
        known: dict[str, ChangeTriple],
        visited: set[str],
        ancestor_segments: set[str],
    ) -> list[FileWorkItem]:
        """Recursively walk a single directory URL."""
        canonical = url.rstrip("/")
        if canonical in visited:
            logger.debug("Skipping already-visited: %s", url)
            return []

        if depth > self.max_depth:
            logger.debug("Max depth %d reached at %s", self.max_depth, url)
            return []

        # Fetch directory listing with rate limiting + backoff
        html = await self._fetch_with_backoff(client, semaphore, url)
        if html is None:
            return []

        visited.add(canonical)

        # Parse entries
        parser = AutoindexParser(base_url=url)
        entries = parser.parse(html)

        work_items: list[FileWorkItem] = []
        subdir_tasks: list[asyncio.Task] = []

        # Current directory's name for loop detection
        current_segment = path.rstrip("/").split("/")[-1].lower()
        child_ancestor_segments = ancestor_segments | ({current_segment} if current_segment else set())

        for entry in entries:
            if entry.is_dir:
                subdir_name_lower = entry.name.lower()

                # Skip configured ignore directories
                if subdir_name_lower in self.ignore_directories:
                    logger.debug("Skipping ignored directory: %s", entry.url)
                    continue

                # Skip ancestor-segment loops (mirrors ingest_scanner defence)
                if subdir_name_lower in child_ancestor_segments:
                    logger.warning(
                        "Skipping recursive loop: '%s' already in ancestor path %s",
                        entry.name,
                        path,
                    )
                    continue

                sub_path = f"{path.rstrip('/')}/{entry.name}/"
                task = asyncio.create_task(
                    self._walk_directory(
                        client=client,
                        semaphore=semaphore,
                        url=entry.url,
                        path=sub_path,
                        depth=depth + 1,
                        known=known,
                        visited=visited,
                        ancestor_segments=child_ancestor_segments,
                    )
                )
                subdir_tasks.append(task)
            else:
                item = self._make_work_item(entry, path, known)
                if item is not None:
                    work_items.append(item)

        if subdir_tasks:
            sub_results = await asyncio.gather(*subdir_tasks, return_exceptions=True)
            for sub_result in sub_results:
                if isinstance(sub_result, Exception):
                    logger.warning("Subdirectory walk error: %s", sub_result)
                else:
                    work_items.extend(sub_result)

        return work_items

    def _make_work_item(
        self,
        entry: DirEntry,
        directory_path: str,
        known: dict[str, ChangeTriple],
    ) -> Optional[FileWorkItem]:
        """Convert a DirEntry to a FileWorkItem, or None if unchanged.

        Change detection: if the URL is in ``known`` and the server's
        (etag, last_modified, content_length) triple matches, the file
        is unchanged and we return None.

        The directory listing does not supply ETag or content_length;
        we use ``(None, last_modified_iso, size_bytes)`` as the triple.
        This is intentionally conservative: if the server later sends
        an ETag on HEAD/GET requests, S2 can upgrade the stored triple.
        """
        url = entry.url

        # Build the triple from listing metadata
        mod_str = entry.modified.isoformat() if entry.modified else None
        current_triple: ChangeTriple = (None, mod_str, entry.size_bytes)

        # Skip if unchanged
        if url in known and known[url] == current_triple:
            logger.debug("Unchanged (triple match): %s", url)
            return None

        # Parse filename
        filename = entry.name
        parsed = parse_filename(filename)

        # Determine file type from extension
        if "." in filename:
            ext = "." + filename.rsplit(".", 1)[1].lower()
        else:
            ext = ""

        file_type = _ext_to_file_type(ext)
        lane = "sidecar" if ext in _SIDECAR_EXTS else "primary"

        if isinstance(parsed, ParsedFilename):
            return FileWorkItem(
                url=url,
                directory_path=directory_path,
                filename=filename,
                media_id=parsed.media_id,
                prefix=parsed.prefix,
                prefix_category=parsed.prefix_category,
                show_name=parsed.show_name,
                season=parsed.season,
                episode=parsed.episode,
                hd=parsed.hd,
                revision_date=parsed.revision_date,
                variant_tag=parsed.variant_tag,
                unknown_tag=parsed.unknown_tag,
                file_type=file_type,
                remote_modified_at=entry.modified,
                file_size_bytes=entry.size_bytes,
                change_triple=current_triple,
                lane=lane,
            )
        else:
            # ParseError — still emit a work item; S2 can index it without parsed fields
            assert isinstance(parsed, ParseError)
            logger.debug("Filename parse error for %s: %s", filename, parsed.reason)
            return FileWorkItem(
                url=url,
                directory_path=directory_path,
                filename=filename,
                media_id=None,
                prefix=None,
                prefix_category="unknown",
                show_name=None,
                season=None,
                episode=None,
                hd=None,
                revision_date=None,
                variant_tag=None,
                unknown_tag=None,
                file_type=file_type,
                remote_modified_at=entry.modified,
                file_size_bytes=entry.size_bytes,
                change_triple=current_triple,
                lane=lane,
            )

    # ------------------------------------------------------------------
    # HTTP fetch with rate limiting and backoff
    # ------------------------------------------------------------------

    async def _fetch_with_backoff(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        url: str,
        max_retries: int = 4,
    ) -> Optional[str]:
        """GET a URL with rate limiting, concurrency cap, and exponential backoff.

        Retries on 5xx, ConnectError, TimeoutException.
        Returns None if all retries exhausted or a 4xx is received.
        """
        import random

        backoff = _BACKOFF_BASE
        for attempt in range(max_retries + 1):
            # Check pause window before each attempt
            self._check_pause_window()

            await self._rate_limiter.acquire()

            try:
                async with semaphore:
                    resp = await client.get(url)

                if resp.status_code == 200:
                    return resp.text

                if resp.status_code == 404:
                    logger.warning("404 Not Found: %s", url)
                    return None

                if 400 <= resp.status_code < 500:
                    logger.warning("Client error %d for %s — not retrying", resp.status_code, url)
                    return None

                # 5xx — retry
                logger.warning(
                    "Server error %d for %s (attempt %d/%d)",
                    resp.status_code,
                    url,
                    attempt + 1,
                    max_retries + 1,
                )

            except httpx.TimeoutException as exc:
                logger.warning("Timeout for %s (attempt %d/%d): %s", url, attempt + 1, max_retries + 1, exc)
            except httpx.ConnectError as exc:
                logger.warning("Connect error for %s (attempt %d/%d): %s", url, attempt + 1, max_retries + 1, exc)
            except httpx.HTTPError as exc:
                logger.warning("HTTP error for %s (attempt %d/%d): %s", url, attempt + 1, max_retries + 1, exc)

            if attempt < max_retries:
                jitter = 1.0 + random.uniform(-_BACKOFF_JITTER, _BACKOFF_JITTER)
                sleep_time = min(backoff * jitter, _BACKOFF_MAX)
                logger.debug("Backoff %.1fs before retry %d for %s", sleep_time, attempt + 2, url)
                await asyncio.sleep(sleep_time)
                backoff = min(backoff * 2, _BACKOFF_MAX)

        logger.error("All %d retries exhausted for %s", max_retries + 1, url)
        return None

    def _check_pause_window(self) -> None:
        """Raise RuntimeError if currently inside the configured pause window.

        Called before each fetch attempt.  The pause window is intended to
        suppress crawling during broadcast traffic hours.  Callers higher up
        the stack (scheduler) should back off gracefully on this error.
        """
        if self.pause_window is None:
            return

        start_t, end_t = self.pause_window
        now_t = datetime.now(timezone.utc).time()

        in_window: bool
        if start_t <= end_t:
            in_window = start_t <= now_t <= end_t
        else:
            # Window wraps midnight
            in_window = now_t >= start_t or now_t <= end_t

        if in_window:
            raise RuntimeError(
                f"Crawl paused: currently in pause window " f"{start_t.strftime('%H:%M')}-{end_t.strftime('%H:%M')} UTC"
            )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _ext_to_file_type(ext: str) -> str:
    """Map a lowercase file extension (with dot) to a mmingest_files file_type value."""
    mapping = {
        ".mp4": "mp4",
        ".srt": "srt",
        ".scc": "scc",
        ".jpg": "image",
        ".jpeg": "image",
        ".png": "image",
        ".gif": "image",
        ".mov": "mp4",  # grouped with video
        ".mxf": "mp4",
    }
    return mapping.get(ext, "other")
