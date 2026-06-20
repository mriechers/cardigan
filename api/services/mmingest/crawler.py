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
import random
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
        """Wait until a token is available, then consume it.

        Uses a re-acquire loop to avoid over-issue during the sleep period:
        after sleeping, we re-enter the lock and re-check — another coroutine
        may have consumed the token that was refilled during our sleep, so we
        loop until we can actually decrement.
        """
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
                self._last_refill = now

                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return

                # Compute sleep and reserve the token speculatively
                # (set tokens to 0 so concurrent acquires see an empty bucket)
                wait_time = (1.0 - self._tokens) / self._rate
                self._tokens = 0.0

            # Sleep outside the lock so other coroutines can run
            await asyncio.sleep(wait_time)
            # Re-enter the loop: re-acquire lock and check again


# ---------------------------------------------------------------------------
# Two-lane work queue
# ---------------------------------------------------------------------------


class TwoLaneWorkQueue:
    """Bounded async queue with two priority lanes.

    Sidecar items (.srt/.scc — cheap to index, search depends on them) always
    drain before primary items (.mp4, images, other) when both lanes have work.
    S2's indexer calls :meth:`get` in a loop; the crawler calls :meth:`put`
    with items whose ``lane`` field routes them automatically.

    Capacity is shared across both lanes.  When the queue is full, :meth:`put`
    blocks until a slot opens (asyncio cooperative back-pressure).

    Usage::

        queue = TwoLaneWorkQueue(maxsize=200)
        await queue.put(work_item)          # routes by item.lane
        item = await queue.get()            # sidecar-first drain
        queue.task_done()                   # mirrors asyncio.Queue.task_done()
    """

    def __init__(self, maxsize: int = 0) -> None:
        """
        Args:
            maxsize: Maximum total items across both lanes.  0 means unbounded.
        """
        self._maxsize = maxsize
        self._sidecar: asyncio.Queue[FileWorkItem] = asyncio.Queue()
        self._primary: asyncio.Queue[FileWorkItem] = asyncio.Queue()
        self._total: int = 0
        self._lock = asyncio.Lock()
        # Notified whenever an item is placed so waiting get() can wake up
        self._not_empty = asyncio.Event()

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def qsize(self) -> int:
        """Total items waiting across both lanes."""
        return self._total

    def empty(self) -> bool:
        return self._total == 0

    def full(self) -> bool:
        return self._maxsize > 0 and self._total >= self._maxsize

    async def put(self, item: FileWorkItem) -> None:
        """Route *item* to its lane and block if the queue is full."""
        while True:
            async with self._lock:
                if not self.full():
                    self._route(item)
                    self._total += 1
                    self._not_empty.set()
                    return
            # Queue is full — yield and retry
            await asyncio.sleep(0)

    def put_nowait(self, item: FileWorkItem) -> None:
        """Non-blocking put.  Raises ``asyncio.QueueFull`` if at capacity."""
        if self.full():
            raise asyncio.QueueFull()
        self._route(item)
        self._total += 1
        self._not_empty.set()

    def _route(self, item: FileWorkItem) -> None:
        if item.lane == "sidecar":
            self._sidecar.put_nowait(item)
        else:
            self._primary.put_nowait(item)

    async def get(self) -> FileWorkItem:
        """Return the next item — sidecar lane is always drained first.

        Lost-wakeup safety: the ``_not_empty`` event is cleared INSIDE the lock
        before we release it and call ``wait()``.  Any ``put()`` that arrives
        after our clear (but before our ``wait()``) will re-set the event while
        still holding its own lock turn, so ``wait()`` returns immediately.
        """
        while True:
            async with self._lock:
                # Try sidecar first
                if not self._sidecar.empty():
                    item = self._sidecar.get_nowait()
                    self._total -= 1
                    if self._total == 0:
                        self._not_empty.clear()
                    return item
                # Fall back to primary
                if not self._primary.empty():
                    item = self._primary.get_nowait()
                    self._total -= 1
                    if self._total == 0:
                        self._not_empty.clear()
                    return item
                # Nothing available — arm the wait INSIDE the lock so we cannot
                # miss a put() that arrives between lock-release and wait().
                self._not_empty.clear()
            # Lock released; a concurrent put() may have already re-set the event.
            await self._not_empty.wait()

    def get_nowait(self) -> FileWorkItem:
        """Non-blocking get.  Raises ``asyncio.QueueEmpty`` if empty."""
        if not self._sidecar.empty():
            item = self._sidecar.get_nowait()
            self._total -= 1
            if self._total == 0:
                self._not_empty.clear()
            return item
        if not self._primary.empty():
            item = self._primary.get_nowait()
            self._total -= 1
            if self._total == 0:
                self._not_empty.clear()
            return item
        raise asyncio.QueueEmpty()

    def task_done(self) -> None:
        """Not tracked internally; provided for API compatibility with asyncio.Queue."""

    async def join(self) -> None:
        """Wait until the queue is empty (join semantics without task tracking)."""
        while not self.empty():
            await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Change-detection helpers
# ---------------------------------------------------------------------------


def _triples_match(known: ChangeTriple, current: ChangeTriple) -> bool:
    """None-tolerant comparison of two HTTP change-detection triples.

    ``current`` is always ``(None, mod_iso, size)`` because directory listings
    never supply ETags.  ``known`` may have a real ETag if S2 persisted one
    after a HEAD/GET.  The rule:

    * If *either* side's ETag is None, skip the ETag comparison entirely.
    * ``last_modified`` must match when both sides have a value; if one side
      is None treat as "unknown → no mismatch signal from this field alone".
    * ``content_length`` follows the same rule as ``last_modified``.
    * A triple is "unchanged" only if no differing field is detected.
    """
    k_etag, k_mod, k_size = known
    c_etag, c_mod, c_size = current

    # ETag: only compare when both sides have one
    if k_etag is not None and c_etag is not None and k_etag != c_etag:
        return False

    # last_modified: if both present and different → changed
    if k_mod is not None and c_mod is not None and k_mod != c_mod:
        return False

    # content_length: if both present and different → changed
    if k_size is not None and c_size is not None and k_size != c_size:
        return False

    return True


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
        # burst=1 (not max_concurrent) to match the validated smoke-test
        # politeness envelope: no first-wave request cluster against the Apache
        # ingest server after start or after the quiet window (#183).
        self._rate_limiter = TokenBucket(rate=rate_per_second, burst=1)

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

        # Single shared visited set prevents duplicate crawling when overlapping
        # roots are supplied (e.g. ["/", "/IWP/"]).  Note: there is a narrow
        # TOCTOU window — a URL could be checked-not-visited before another
        # coroutine adds it.  In cooperative asyncio this can only happen at
        # an ``await`` point inside _walk_directory, which is acceptable for
        # a polite production server where the worst case is one duplicate fetch.
        visited: set[str] = set()

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
                        visited=visited,
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

        # Build the triple from listing metadata.
        # Directory listings never supply an ETag — we always store None here.
        # S2 may later upgrade the persisted triple with a real ETag once it
        # fetches the file; this side must not penalise that upgrade.
        mod_str = entry.modified.isoformat() if entry.modified else None
        current_triple: ChangeTriple = (None, mod_str, entry.size_bytes)

        # Skip if unchanged — ETag-tolerant comparison:
        # * If either side lacks an ETag (None), skip the ETag field entirely.
        # * last_modified and content_length must match when both sides have them.
        if url in known:
            if _triples_match(known[url], current_triple):
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
        backoff = _BACKOFF_BASE
        for attempt in range(max_retries + 1):
            # Check pause window BEFORE consuming a rate-limiter token.
            # Raising here means we spent no tokens on a suppressed crawl.
            # The caller (run_delta_walk) catches RuntimeError and logs it.
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

        Called at the top of each fetch-attempt loop, BEFORE acquiring a
        rate-limiter token.  This prevents token consumption when crawling is
        suppressed.

        Behaviour when raised:
          * ``_fetch_with_backoff`` propagates it immediately (no retry).
          * ``_walk_directory`` surfaces it as an exception result in
            ``asyncio.gather``, which ``delta_walk`` logs as a walk error.
          * ``run_delta_walk`` (scheduler) catches it and logs a clean message
            rather than treating it as an infrastructure failure.

        The pause window is intended to suppress crawling during broadcast
        traffic hours (production server — treat it gently).
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
