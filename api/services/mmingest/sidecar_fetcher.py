"""mmingest sidecar fetcher — Sprint 1B.

GETs .srt/.scc files from the mmingest server and returns their content
as ``SidecarResult`` work items.  Makes NO database writes — S2 wires
persistence.

Usage::

    fetcher = SidecarFetcher()
    result = await fetcher.fetch(
        url="https://mmingest.pbswi.wisc.edu/IWP/6POL0101_REV20260319.srt",
        file_id_hint=None,   # S2 passes the mmingest_files.id once known
    )
    if result.ok:
        print(result.body_text[:200])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Supported sidecar file extensions
SIDECAR_EXTENSIONS: frozenset[str] = frozenset({".srt", ".scc"})


@dataclass
class SidecarResult:
    """Result of fetching a single sidecar file.

    No DB references — S2 maps ``url`` to ``mmingest_files.id`` and writes
    to ``mmingest_sidecars``.
    """

    url: str
    filename: str
    kind: str  # "srt" or "scc"
    ok: bool  # True if fetch succeeded

    # Populated on success
    body_text: Optional[str] = None
    bytes: Optional[int] = None  # content length
    fetched_at: Optional[datetime] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None

    # Populated on failure
    error: Optional[str] = None
    status_code: Optional[int] = None

    # Opaque hint passed through for S2 convenience (not used internally)
    file_id_hint: Optional[int] = None


class SidecarFetcher:
    """Fetches .srt and .scc sidecar files from the mmingest server.

    Designed for single-file fetches or small batches.  For bulk sidecar
    queueing, use the crawler's priority lane + the scheduler.
    """

    def __init__(
        self,
        timeout_seconds: int = 30,
        auth: Optional[tuple[str, str]] = None,
    ) -> None:
        """
        Args:
            timeout_seconds: Per-request timeout.
            auth: Optional (username, password) for HTTP Basic Auth.
        """
        self.timeout = timeout_seconds
        self.auth = auth

    async def fetch(
        self,
        url: str,
        file_id_hint: Optional[int] = None,
    ) -> SidecarResult:
        """Fetch a single sidecar file.

        Args:
            url:          Full URL of the .srt or .scc file.
            file_id_hint: Optional mmingest_files.id that S2 can use to
                          correlate this result without a second lookup.

        Returns:
            SidecarResult — always succeeds structurally; check ``.ok``.
        """
        filename = url.rstrip("/").split("/")[-1]
        ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""

        if ext not in SIDECAR_EXTENSIONS:
            return SidecarResult(
                url=url,
                filename=filename,
                kind=ext.lstrip(".") or "unknown",
                ok=False,
                error=f"Not a sidecar extension: {ext!r} (expected .srt or .scc)",
                file_id_hint=file_id_hint,
            )

        kind = ext.lstrip(".")

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                auth=httpx.BasicAuth(*self.auth) if self.auth else None,
            ) as client:
                resp = await client.get(url)

            if resp.status_code != 200:
                return SidecarResult(
                    url=url,
                    filename=filename,
                    kind=kind,
                    ok=False,
                    error=f"HTTP {resp.status_code}",
                    status_code=resp.status_code,
                    file_id_hint=file_id_hint,
                )

            body_bytes = resp.content
            # Decode as UTF-8; fall back to latin-1 for legacy SCC files
            try:
                body_text = body_bytes.decode("utf-8")
            except UnicodeDecodeError:
                body_text = body_bytes.decode("latin-1")

            return SidecarResult(
                url=url,
                filename=filename,
                kind=kind,
                ok=True,
                body_text=body_text,
                bytes=len(body_bytes),
                fetched_at=datetime.now(timezone.utc),
                etag=resp.headers.get("ETag"),
                last_modified=resp.headers.get("Last-Modified"),
                file_id_hint=file_id_hint,
            )

        except httpx.TimeoutException as exc:
            return SidecarResult(
                url=url,
                filename=filename,
                kind=kind,
                ok=False,
                error=f"Timeout: {exc}",
                file_id_hint=file_id_hint,
            )
        except httpx.HTTPError as exc:
            return SidecarResult(
                url=url,
                filename=filename,
                kind=kind,
                ok=False,
                error=f"HTTP error: {exc}",
                file_id_hint=file_id_hint,
            )
        except Exception as exc:
            logger.exception("Unexpected error fetching sidecar %s", url)
            return SidecarResult(
                url=url,
                filename=filename,
                kind=kind,
                ok=False,
                error=f"Unexpected error: {exc}",
                file_id_hint=file_id_hint,
            )

    async def fetch_many(
        self,
        urls: list[tuple[str, Optional[int]]],
        max_concurrent: int = 4,
    ) -> list[SidecarResult]:
        """Fetch multiple sidecar files concurrently.

        Args:
            urls:           List of (url, file_id_hint) tuples.
            max_concurrent: Max simultaneous fetches.

        Returns:
            List of SidecarResult in the same order as ``urls``.
        """
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)

        async def _bounded_fetch(url: str, hint: Optional[int]) -> SidecarResult:
            async with semaphore:
                return await self.fetch(url, file_id_hint=hint)

        tasks = [asyncio.create_task(_bounded_fetch(url, hint)) for url, hint in urls]
        return list(await asyncio.gather(*tasks))
