"""mmingest indexer — Sprint 2.

Orchestrates the end-to-end pipeline:
    walk -> diff against DB-known state -> enqueue -> fetch sidecars ->
    upsert mmingest_files -> write mmingest_sidecars (FTS via triggers) ->
    verify parity.

Uses S1B components as a library.  Makes NO attempt to reimplement the
crawler, parser, or sidecar fetcher — those are S1B's frozen surface.

Variant lineage persistence order (per spec):
  1. Upsert all FileWorkItem rows into mmingest_files (batch).
  2. Group rows by (media_id, variant_tag) — None treated as its own group.
  3. For each group, call select_primary() to find the REV winner.
  4. UPDATE superseded_by on older rows to point at the primary's id.
  5. Variants (known KNOWN_VARIANT_VOCAB tags) stay with superseded_by=NULL.

Idempotency: re-running on unchanged input produces zero DB writes (the
crawler's change-detection triple suppresses unchanged files upstream).
Re-running after a new _REV arrives flips the previous winner's
superseded_by to the new winner.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from api.services.mmingest._db import fts_parity_delta
from api.services.mmingest.crawler import ChangeTriple, FileWorkItem, MmingestCrawler
from api.services.mmingest.parsers import ParsedFilename, select_primary
from api.services.mmingest.sidecar_fetcher import SidecarFetcher, SidecarResult

logger = logging.getLogger(__name__)

# Batch size mirrors the S-1 _track_files_batch pattern.
# 500 rows x ~20 params = 10 000 — well within SQLite's 32 766 param cap.
_UPSERT_BATCH_SIZE = 500

# Sidecar file types that trigger a content fetch
_SIDECAR_FILE_TYPES = frozenset({"srt", "scc"})


# ---------------------------------------------------------------------------
# Result summary dataclass
# ---------------------------------------------------------------------------


@dataclass
class IndexerRun:
    """Summary returned by MmingestIndexer.run_once()."""

    # Walk / diff counts
    files_seen: int = 0
    files_new: int = 0
    files_changed: int = 0

    # Sidecar fetch counts
    sidecars_fetched: int = 0
    sidecars_persisted: int = 0
    sidecars_failed: int = 0

    # FTS parity check result (0 = in sync; None = pre-migration)
    fts_parity_delta: Optional[int] = None

    # Error list (non-fatal entries; fatal errors raise)
    errors: list[str] = field(default_factory=list)

    # Wall-clock time for the full run
    elapsed_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Main indexer class
# ---------------------------------------------------------------------------


class MmingestIndexer:
    """End-to-end orchestrator for the mmingest file index.

    Instantiate with an async SQLAlchemy engine.  Call ``run_once()`` to
    execute one full pass.  The scheduler calls this; do not call
    ``run_once()`` concurrently.

    Args:
        engine:          Async SQLAlchemy engine pointed at the Cardigan DB.
        base_url:        mmingest root URL.
        directories:     Subdirectory paths to crawl (default: ["/"]).
        max_concurrent:  Max in-flight HTTP requests for the crawler.
        rate_per_second: Token-bucket refill rate for the crawler.
        crawler_auth:    Optional (username, password) for HTTP Basic Auth.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        base_url: str = "https://mmingest.pbswi.wisc.edu/",
        directories: Optional[list[str]] = None,
        max_concurrent: int = 4,
        rate_per_second: float = 2.0,
        crawler_auth: Optional[tuple[str, str]] = None,
    ) -> None:
        self._engine = engine
        self._base_url = base_url
        self._directories = directories or ["/"]
        self._max_concurrent = max_concurrent
        self._rate_per_second = rate_per_second
        self._crawler_auth = crawler_auth

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_once(self) -> IndexerRun:
        """Execute one full indexer pass.

        Returns an IndexerRun summary.  Non-fatal errors are collected in
        IndexerRun.errors; fatal errors propagate as exceptions.
        """
        start = time.monotonic()
        run = IndexerRun()

        # Step 1: load known state from the DB (read-only, no transaction needed)
        async with self._engine.connect() as conn:
            known = await self.load_known_state(conn)
        logger.info("mmingest indexer: %d known files loaded from DB", len(known))

        # Step 2: run the delta walk (S1B does the HTTP + parse work)
        crawler = MmingestCrawler(
            base_url=self._base_url,
            max_concurrent=self._max_concurrent,
            rate_per_second=self._rate_per_second,
            auth=self._crawler_auth,
        )
        work_items = await crawler.delta_walk(
            directories=self._directories,
            known=known,
        )
        run.files_seen = len(work_items)
        logger.info("mmingest indexer: %d new/changed work items from crawler", len(work_items))

        if not work_items:
            # Still run parity check even with no new work
            async with self._engine.connect() as conn:
                run.fts_parity_delta = await self._verify_parity_after_batch(conn)
            run.elapsed_seconds = time.monotonic() - start
            return run

        # Step 3: upsert files into mmingest_files (transactional)
        async with self._engine.begin() as conn:
            url_to_id = await self._upsert_files(conn, work_items)

        run.files_new = len(work_items)  # crawler only returns new/changed

        # Step 4: apply variant lineage (superseded_by) updates (transactional)
        async with self._engine.begin() as conn:
            await self._apply_variant_lineage(conn, work_items, url_to_id)

        # Step 5: fetch and persist sidecars
        sidecar_items = [wi for wi in work_items if wi.file_type in _SIDECAR_FILE_TYPES]
        if sidecar_items:
            fetcher = SidecarFetcher(auth=self._crawler_auth)
            fetch_inputs = [(wi.url, url_to_id.get(wi.url)) for wi in sidecar_items]
            results = await fetcher.fetch_many(
                urls=fetch_inputs,
                max_concurrent=self._max_concurrent,
            )

            run.sidecars_fetched = len(results)
            ok_results = [r for r in results if r.ok]
            run.sidecars_failed = len(results) - len(ok_results)

            for r in results:
                if not r.ok:
                    run.errors.append(f"Sidecar fetch failed for {r.url}: {r.error}")

            if ok_results:
                async with self._engine.begin() as conn:
                    run.sidecars_persisted = await self._persist_sidecars(conn, ok_results, url_to_id)

        # Step 6: parity check after the sidecar batch (read-only)
        async with self._engine.connect() as conn:
            run.fts_parity_delta = await self._verify_parity_after_batch(conn)

        run.elapsed_seconds = time.monotonic() - start
        logger.info(
            "mmingest indexer run complete: files_seen=%d files_new=%d "
            "sidecars_fetched=%d sidecars_persisted=%d fts_delta=%s elapsed=%.1fs",
            run.files_seen,
            run.files_new,
            run.sidecars_fetched,
            run.sidecars_persisted,
            run.fts_parity_delta,
            run.elapsed_seconds,
        )
        return run

    # ------------------------------------------------------------------
    # Load known state
    # ------------------------------------------------------------------

    async def load_known_state(self, conn: AsyncConnection) -> dict[str, ChangeTriple]:
        """Query mmingest_files for all known (url, triple) pairs.

        Returns a dict mapping remote_url -> (etag, last_modified_iso, size_bytes).
        This is passed to MmingestCrawler.delta_walk() as the ``known`` argument so
        that unchanged files are skipped by the crawler.
        """
        rows = await conn.execute(text("""
                SELECT remote_url, etag, remote_modified_at, file_size_bytes
                FROM mmingest_files
            """))
        known: dict[str, ChangeTriple] = {}
        for row in rows.fetchall():
            url = row[0]
            etag = row[1]
            mod_at = row[2]  # stored as ISO string or datetime
            size = row[3]

            # Normalise: the crawler stores mod as ISO string in the triple;
            # convert datetime objects if needed.
            if isinstance(mod_at, datetime):
                mod_str: Optional[str] = mod_at.isoformat()
            elif mod_at is not None:
                mod_str = str(mod_at)
            else:
                mod_str = None

            known[url] = (etag, mod_str, size)
        return known

    # ------------------------------------------------------------------
    # Upsert mmingest_files
    # ------------------------------------------------------------------

    async def _upsert_files(
        self,
        conn: AsyncConnection,
        items: list[FileWorkItem],
    ) -> dict[str, int]:
        """Upsert FileWorkItem rows into mmingest_files.

        Uses INSERT OR REPLACE semantics so re-runs are idempotent.
        Returns a dict mapping url -> mmingest_files.id for use by later steps.

        The upsert uses INSERT OR IGNORE to preserve first_seen_at on existing
        rows, followed by an UPDATE of mutable fields.  This is correct for the
        case where the crawler detected a change but we want to keep provenance.
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        # INSERT OR IGNORE: creates the row if new; skips if already present.
        # The UPDATE below refreshes mutable fields for both new and existing rows.
        insert_sql = text("""
            INSERT OR IGNORE INTO mmingest_files (
                remote_url, directory_path, filename,
                media_id, prefix, prefix_category, show_name,
                season, episode, hd,
                revision_date, variant_tag,
                file_type, file_size_bytes,
                remote_modified_at, etag,
                first_seen_at, last_seen_at,
                status
            ) VALUES (
                :remote_url, :directory_path, :filename,
                :media_id, :prefix, :prefix_category, :show_name,
                :season, :episode, :hd,
                :revision_date, :variant_tag,
                :file_type, :file_size_bytes,
                :remote_modified_at, :etag,
                :now, :now,
                'new'
            )
        """)

        update_sql = text("""
            UPDATE mmingest_files SET
                directory_path      = :directory_path,
                filename            = :filename,
                media_id            = :media_id,
                prefix              = :prefix,
                prefix_category     = :prefix_category,
                show_name           = :show_name,
                season              = :season,
                episode             = :episode,
                hd                  = :hd,
                revision_date       = :revision_date,
                variant_tag         = :variant_tag,
                file_type           = :file_type,
                file_size_bytes     = :file_size_bytes,
                remote_modified_at  = :remote_modified_at,
                etag                = :etag,
                last_seen_at        = :now
            WHERE remote_url = :remote_url
        """)

        def _to_params(item: FileWorkItem) -> dict:
            etag, mod_str, _size = item.change_triple
            mod_iso: Optional[str]
            if item.remote_modified_at is not None:
                mod_iso = item.remote_modified_at.isoformat()
            else:
                mod_iso = mod_str  # fall back to triple's last_modified

            if item.unknown_tag:
                logger.info(
                    "mmingest indexer: persisting file with unknown_tag=%r "
                    "(url=%s); variant_tag stays NULL; "
                    "logged for vocabulary growth (issue #184).",
                    item.unknown_tag,
                    item.url,
                )

            return {
                "remote_url": item.url,
                "directory_path": item.directory_path,
                "filename": item.filename,
                "media_id": item.media_id,
                "prefix": item.prefix,
                "prefix_category": item.prefix_category,
                "show_name": item.show_name,
                "season": item.season,
                "episode": item.episode,
                "hd": (1 if item.hd else 0) if item.hd is not None else None,
                "revision_date": item.revision_date,
                "variant_tag": item.variant_tag,
                "file_type": item.file_type,
                "file_size_bytes": item.file_size_bytes,
                "remote_modified_at": mod_iso,
                "etag": etag,
                "now": now_iso,
            }

        params_list = [_to_params(item) for item in items]

        for batch_start in range(0, len(params_list), _UPSERT_BATCH_SIZE):
            batch = params_list[batch_start : batch_start + _UPSERT_BATCH_SIZE]
            await conn.execute(insert_sql, batch)
            await conn.execute(update_sql, batch)

        # Collect url -> id mapping for all upserted rows
        all_urls = [item.url for item in items]
        url_to_id: dict[str, int] = {}
        for batch_start in range(0, len(all_urls), _UPSERT_BATCH_SIZE):
            batch_urls = all_urls[batch_start : batch_start + _UPSERT_BATCH_SIZE]
            placeholders = ", ".join(f":u{i}" for i in range(len(batch_urls)))
            id_rows = await conn.execute(
                text(f"SELECT id, remote_url FROM mmingest_files WHERE remote_url IN ({placeholders})"),
                {f"u{i}": url for i, url in enumerate(batch_urls)},
            )
            for row in id_rows.fetchall():
                url_to_id[row[1]] = row[0]

        return url_to_id

    # ------------------------------------------------------------------
    # Variant lineage
    # ------------------------------------------------------------------

    async def _apply_variant_lineage(
        self,
        conn: AsyncConnection,
        items: list[FileWorkItem],
        url_to_id: dict[str, int],
    ) -> None:
        """Update superseded_by for older _REV rows within each (media_id, variant_tag) group.

        Algorithm (per spec):
          1. Group FileWorkItems by (media_id, variant_tag).
          2. For each group, call select_primary() to find the winner.
          3. For each superseded item, UPDATE superseded_by = primary_id.
          4. Ensure the primary's superseded_by is NULL (idempotency).

        Items with media_id=None cannot participate in variant lineage and are skipped.
        Items with unknown_tag are treated as primaries within their own group
        (select_primary() handles this via the ParsedFilename surface).
        """
        # Build a ParsedFilename-like view for select_primary.
        # select_primary() takes list[ParsedFilename]; we synthesise minimal objects
        # from the FileWorkItem fields rather than re-parsing from filename.

        # Group by (media_id, variant_tag) — items without media_id are skipped
        groups: dict[tuple, list[FileWorkItem]] = defaultdict(list)
        for item in items:
            if item.media_id is None:
                continue
            key = (item.media_id, item.variant_tag)
            groups[key].append(item)

        if not groups:
            return

        update_sql = text("""
            UPDATE mmingest_files
            SET superseded_by = :superseded_by
            WHERE remote_url = :remote_url
        """)

        # Build up all UPDATE params across all groups; apply in one batch
        superseded_params: list[dict] = []
        clear_primary_params: list[dict] = []

        for key, group in groups.items():
            # Build minimal ParsedFilename objects for select_primary
            pf_list = [_make_parsed_filename(item) for item in group]
            primary_pf, variants_pf, superseded_pf = select_primary(pf_list)

            if primary_pf is None:
                continue

            # Find the primary FileWorkItem by its revision_date / unknown_tag match
            primary_item = _find_item_by_pf(primary_pf, group)
            if primary_item is None:
                logger.warning(
                    "variant_lineage: could not match primary ParsedFilename back to "
                    "FileWorkItem for group %s — skipping lineage update",
                    key,
                )
                continue

            primary_id = url_to_id.get(primary_item.url)
            if primary_id is None:
                logger.warning(
                    "variant_lineage: primary item %s not in url_to_id map — skipping",
                    primary_item.url,
                )
                continue

            # Primary row must have superseded_by = NULL
            clear_primary_params.append({"superseded_by": None, "remote_url": primary_item.url})

            # Superseded rows point at primary
            for sup_pf in superseded_pf:
                sup_item = _find_item_by_pf(sup_pf, group)
                if sup_item is None:
                    continue
                superseded_params.append({"superseded_by": primary_id, "remote_url": sup_item.url})

        # Apply in batches
        all_updates = clear_primary_params + superseded_params
        for batch_start in range(0, len(all_updates), _UPSERT_BATCH_SIZE):
            batch = all_updates[batch_start : batch_start + _UPSERT_BATCH_SIZE]
            await conn.execute(update_sql, batch)

        if superseded_params:
            logger.debug(
                "variant_lineage: set superseded_by on %d rows; cleared primary on %d rows",
                len(superseded_params),
                len(clear_primary_params),
            )

    # ------------------------------------------------------------------
    # Sidecar persistence
    # ------------------------------------------------------------------

    async def _persist_sidecars(
        self,
        conn: AsyncConnection,
        results: list[SidecarResult],
        url_to_id: dict[str, int],
    ) -> int:
        """Insert successful SidecarResult rows into mmingest_sidecars.

        Uses INSERT OR IGNORE so re-runs are idempotent (same file_id + kind
        combination is not duplicated).  Returns count of rows persisted.

        The migration 016 AFTER INSERT trigger propagates each insert to the
        FTS5 index automatically — no explicit FTS write needed here.
        """
        insert_sql = text("""
            INSERT OR IGNORE INTO mmingest_sidecars (file_id, kind, body_text, bytes, fetched_at)
            VALUES (:file_id, :kind, :body_text, :bytes, :fetched_at)
        """)

        now_iso = datetime.now(timezone.utc).isoformat()
        params_list: list[dict] = []

        for result in results:
            # Resolve file_id: prefer file_id_hint (set by fetch_many caller),
            # fall back to url_to_id lookup.
            file_id = result.file_id_hint or url_to_id.get(result.url)
            if file_id is None:
                logger.warning(
                    "persist_sidecars: no file_id for %s — sidecar not persisted",
                    result.url,
                )
                continue

            fetched_at_iso = result.fetched_at.isoformat() if result.fetched_at else now_iso
            params_list.append(
                {
                    "file_id": file_id,
                    "kind": result.kind,
                    "body_text": result.body_text,
                    "bytes": result.bytes,
                    "fetched_at": fetched_at_iso,
                }
            )

        persisted = 0
        for batch_start in range(0, len(params_list), _UPSERT_BATCH_SIZE):
            batch = params_list[batch_start : batch_start + _UPSERT_BATCH_SIZE]
            await conn.execute(insert_sql, batch)
            persisted += len(batch)

        return persisted

    # ------------------------------------------------------------------
    # FTS parity check
    # ------------------------------------------------------------------

    async def _verify_parity_after_batch(self, conn: AsyncConnection) -> Optional[int]:
        """Call fts_parity_delta and log a WARNING on non-zero result.

        Returns the raw delta (0 = in sync; None = pre-migration state).
        Does NOT raise — a non-zero delta is an ops signal, not a crash.
        """
        delta = await fts_parity_delta(conn)
        if delta is None:
            logger.warning(
                "FTS parity: migration 016 tables absent — cannot verify parity. "
                "This should not happen at Sprint 2 runtime; check alembic state."
            )
        elif delta != 0:
            logger.warning(
                "FTS parity delta non-zero after sidecar batch: %d (expected 0). "
                "FTS index may be out of sync with mmingest_sidecars; check trigger health.",
                delta,
            )
        return delta


# ---------------------------------------------------------------------------
# Helpers (module-private)
# ---------------------------------------------------------------------------


def _make_parsed_filename(item: FileWorkItem) -> ParsedFilename:
    """Build a minimal ParsedFilename from a FileWorkItem for use with select_primary().

    select_primary() only examines .revision_date, .variant_tag, .unknown_tag
    (and .media_id for identity).  The other fields are filled with safe defaults.
    """
    return ParsedFilename(
        stem=item.filename.rsplit(".", 1)[0] if "." in item.filename else item.filename,
        file_type=("." + item.file_type) if item.file_type else "",
        media_id=item.media_id or "",
        prefix=item.prefix or "",
        season=item.season or 0,
        episode=item.episode or 0,
        hd=item.hd or False,
        revision_date=item.revision_date,
        variant_tag=item.variant_tag,
        unknown_tag=item.unknown_tag,
        prefix_category=item.prefix_category,
        show_name=item.show_name,
    )


def _find_item_by_pf(
    pf: ParsedFilename,
    group: list[FileWorkItem],
) -> Optional[FileWorkItem]:
    """Find the FileWorkItem in group whose key fields match pf.

    Matches on (media_id, variant_tag, revision_date, unknown_tag).  The
    combination is unique within a (media_id, variant_tag) group.
    """
    for item in group:
        if (
            item.media_id == pf.media_id
            and item.variant_tag == pf.variant_tag
            and item.revision_date == pf.revision_date
            and item.unknown_tag == pf.unknown_tag
        ):
            return item
    return None
