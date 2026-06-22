"""FastAPI router for mmingest search/asset/captions/recent endpoints.

Five endpoints under /api/mmingest (registered in api/main.py with
prefix="/api/mmingest"):

  GET /search                       — FTS5 BM25-ranked full-text search
  GET /assets/{media_id}            — {primary, variants, superseded} shape
  GET /assets/{media_id}/url        — resolved URL string (convenience)
  GET /assets/{media_id}/captions   — cached sidecar body from DB (no round-trip)
  GET /recent                       — chronological listing for arrival watchers

Auth seam
---------
Sprint 3A's middleware enforces mmingest:read scope before these endpoints
fire.  While S3A is in flight, _require_scope is a no-op marker so endpoint
signatures stay compatible once S3A merges.

Audit log
---------
Audit log writes are middleware territory (S3A).  This router does NOT write
to mmingest_audit_log.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Per-request timeout (seconds) for the live Airtable lookup on /assets/{id}.
# Kept short so a slow/unreachable Airtable can't hang a worker for the
# AirtableClient default (60s) — the cached mmingest_files.airtable_record_id
# is the fallback when the lookup times out.  See issue #192.
_ASSET_AIRTABLE_TIMEOUT_S = 5.0

from api.models.mmingest import (
    AssetEntry,
    AssetResponse,
    CaptionResponse,
    RecentEntry,
    RecentResponse,
    SearchResponse,
    SearchResult,
    UrlResponse,
)
from api.services.airtable import AirtableClient, get_airtable_client
from api.services.database import get_db_url

router = APIRouter()


# ---------------------------------------------------------------------------
# Scope-enforcement stub (no-op until S3A merges)
# ---------------------------------------------------------------------------


def _require_scope(scope: str) -> Depends:
    """Stub: real scope enforcement lands in Sprint 3A middleware.

    Kept as a no-op dependency so endpoint signatures don't change when S3A
    merges.  Once S3A's middleware is live, request.state.consumer_scopes is
    set and the middleware has already rejected the call with 403 if the
    required scope is missing.  This dep stays as a marker so endpoint
    signatures document required scope.

    TODO(S3A): _require_scope makes this real once the middleware lands.
    """

    async def _dep(request: Request) -> None:  # noqa: ARG001
        return None

    return Depends(_dep)


# ---------------------------------------------------------------------------
# DB engine helper
# ---------------------------------------------------------------------------


def _get_engine() -> AsyncEngine:
    """Return (or create) the async engine for the mmingest tables.

    Uses the same DATABASE_PATH as the rest of the app so all queries hit the
    same SQLite file.  A module-level singleton is fine for the dev server;
    production should switch to the shared engine from api.services.database
    once that module exposes a getter.

    TODO: wire to the shared engine factory from api.services.database after
    that module grows an engine-getter (currently it only exposes get_session).
    """
    return create_async_engine(get_db_url(), echo=False)


# ---------------------------------------------------------------------------
# 1. GET /search
# ---------------------------------------------------------------------------


@router.get("/search", response_model=SearchResponse)
async def search(
    q: Annotated[str, Query(description="FTS5 MATCH query string")],
    prefix: Annotated[Optional[str], Query(description="Filter by 4-char prefix (exact, case-insensitive)")] = None,
    since: Annotated[Optional[datetime], Query(description="Filter where remote_modified_at >= since")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Max results (1-100)")] = 25,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
    _scope=_require_scope("mmingest:read"),
) -> SearchResponse:
    """Full-text search over indexed sidecar bodies (BM25 ranked).

    Surfaces only current rows (superseded_by IS NULL) by default.
    Airtable is NOT queried here — keep search fast.
    """
    engine = _get_engine()
    try:
        async with engine.connect() as conn:
            # BM25-ranked search via FTS5 JOIN shape (per migration 016 design note).
            # DO NOT select mmingest_sidecars_fts.media_id — that column does not
            # exist on the FTS table.  Always JOIN to mmingest_files for display fields.
            search_sql = text("""
                SELECT
                    mf.media_id,
                    mf.prefix,
                    mf.season,
                    mf.episode,
                    mf.revision_date,
                    mf.remote_modified_at,
                    snippet(mmingest_sidecars_fts, 0, '<b>', '</b>', '...', 32) AS snippet,
                    s.kind AS sidecar_kind,
                    fts.rank
                FROM   mmingest_sidecars_fts AS fts
                JOIN   mmingest_sidecars AS s ON s.id = fts.rowid
                JOIN   mmingest_files AS mf ON mf.id = s.file_id
                WHERE  mmingest_sidecars_fts MATCH :q
                  AND  (:prefix IS NULL OR LOWER(mf.prefix) = LOWER(:prefix))
                  AND  (:since IS NULL OR mf.remote_modified_at >= :since)
                  AND  mf.superseded_by IS NULL
                ORDER  BY fts.rank
                LIMIT  :limit OFFSET :offset
            """)

            count_sql = text("""
                SELECT COUNT(*)
                FROM   mmingest_sidecars_fts AS fts
                JOIN   mmingest_sidecars AS s ON s.id = fts.rowid
                JOIN   mmingest_files AS mf ON mf.id = s.file_id
                WHERE  mmingest_sidecars_fts MATCH :q
                  AND  (:prefix IS NULL OR LOWER(mf.prefix) = LOWER(:prefix))
                  AND  (:since IS NULL OR mf.remote_modified_at >= :since)
                  AND  mf.superseded_by IS NULL
            """)

            params = {
                "q": q,
                "prefix": prefix,
                "since": since.isoformat() if since else None,
                "limit": limit,
                "offset": offset,
            }
            count_params = {
                "q": q,
                "prefix": prefix,
                "since": since.isoformat() if since else None,
            }

            try:
                rows = (await conn.execute(search_sql, params)).fetchall()
                total = (await conn.execute(count_sql, count_params)).scalar() or 0
            except OperationalError as e:
                # Malformed FTS5 MATCH syntax (e.g. unbalanced quotes, bad
                # operators) raises OperationalError. That's a client error, not
                # a server fault — return 400 instead of a 500 that crashes the
                # worker. The query is parameter-bound, so this is not injection.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Malformed FTS5 search query: {e.orig}. Check for "
                        "unbalanced quotes or invalid FTS5 operators (phrase "
                        '"..." , prefix term* , AND/OR/NOT, NEAR).'
                    ),
                ) from e
    finally:
        await engine.dispose()

    results = [
        SearchResult(
            media_id=row._mapping["media_id"],
            prefix=row._mapping["prefix"],
            season=row._mapping["season"],
            episode=row._mapping["episode"],
            revision_date=row._mapping["revision_date"],
            modified_at=row._mapping["remote_modified_at"],
            snippet=row._mapping["snippet"] or "",
            sidecar_kind=row._mapping["sidecar_kind"],
        )
        for row in rows
    ]

    return SearchResponse(results=results, total=total)


# ---------------------------------------------------------------------------
# Shared asset-resolution helper
# ---------------------------------------------------------------------------


def _row_to_asset_entry(row, airtable_record_id: Optional[str] = None) -> AssetEntry:
    """Convert a DB row (mapping) to an AssetEntry model."""
    return AssetEntry(
        file_id=row._mapping["id"],
        media_id=row._mapping["media_id"],
        variant_tag=row._mapping["variant_tag"],
        revision_date=row._mapping["revision_date"],
        url=row._mapping["remote_url"],
        file_type=row._mapping["file_type"],
        remote_modified_at=row._mapping["remote_modified_at"],
        file_size_bytes=row._mapping["file_size_bytes"],
        airtable_record_id=airtable_record_id,
    )


async def _resolve_asset(
    media_id: str,
    conn,
) -> tuple[list, list, list]:
    """Fetch and partition all rows for media_id into (primary_rows, variant_rows, superseded_rows).

    Returns three lists of row mappings:
      primary_rows   — variant_tag IS NULL AND superseded_by IS NULL  (should be 0 or 1)
      variant_rows   — variant_tag IS NOT NULL AND superseded_by IS NULL
      superseded_rows — superseded_by IS NOT NULL (any variant_tag)
    """
    sql = text("""
        SELECT id, media_id, variant_tag, revision_date, remote_url,
               file_type, remote_modified_at, file_size_bytes,
               superseded_by, airtable_record_id
        FROM   mmingest_files
        WHERE  media_id = :media_id
    """)
    rows = (await conn.execute(sql, {"media_id": media_id})).fetchall()
    if not rows:
        return [], [], []

    primary_rows = []
    variant_rows = []
    superseded_rows = []

    for row in rows:
        m = row._mapping
        if m["superseded_by"] is not None:
            superseded_rows.append(row)
        elif m["variant_tag"] is None:
            primary_rows.append(row)
        else:
            variant_rows.append(row)

    return primary_rows, variant_rows, superseded_rows


# ---------------------------------------------------------------------------
# 2. GET /assets/{media_id}
# ---------------------------------------------------------------------------


@router.get("/assets/{media_id}", response_model=AssetResponse)
async def get_asset(
    media_id: str,
    airtable_client: Annotated[AirtableClient, Depends(get_airtable_client)],
    _scope=_require_scope("mmingest:read"),
) -> AssetResponse:
    """Return the full {primary, variants, superseded} shape for a media_id.

    Airtable record_id is looked up live for the primary only (one call,
    one media_id).  Variants and superseded rows do not get Airtable calls.

    TODO: add response caching (TTL=300s) once usage patterns are known.
    """
    engine = _get_engine()
    try:
        async with engine.connect() as conn:
            primary_rows, variant_rows, superseded_rows = await _resolve_asset(media_id, conn)
    finally:
        await engine.dispose()

    # 404 if nothing at all matches this media_id
    if not primary_rows and not variant_rows and not superseded_rows:
        raise HTTPException(status_code=404, detail=f"No asset found for media_id={media_id!r}")

    # Live Airtable lookup for primary (single-element list, single batch call)
    at_record_id: Optional[str] = None
    if primary_rows:
        try:
            at_results = await asyncio.wait_for(
                airtable_client.batch_search_sst_by_media_ids([media_id]),
                timeout=_ASSET_AIRTABLE_TIMEOUT_S,
            )
            at_record = at_results.get(media_id)
            if at_record:
                at_record_id = at_record.get("id")
        except Exception:
            # Airtable unavailable or too slow (TimeoutError from wait_for) —
            # surface the pre-cached value from DB instead of hanging/500ing.
            at_record_id = primary_rows[0]._mapping.get("airtable_record_id")

    primary: Optional[AssetEntry] = None
    if primary_rows:
        # Per variant-selection rule: exactly one primary (the winner).
        # If somehow there are multiples (shouldn't happen with S2 algorithm),
        # take the first row — the indexer guarantees uniqueness.
        primary = _row_to_asset_entry(primary_rows[0], airtable_record_id=at_record_id)

    variants = [_row_to_asset_entry(r) for r in variant_rows]
    superseded = [_row_to_asset_entry(r) for r in superseded_rows]

    return AssetResponse(primary=primary, variants=variants, superseded=superseded)


# ---------------------------------------------------------------------------
# 3. GET /assets/{media_id}/url
# ---------------------------------------------------------------------------


@router.get("/assets/{media_id}/url", response_model=UrlResponse)
async def get_asset_url(
    media_id: str,
    variant: Annotated[
        Optional[str], Query(description="Variant tag override (e.g. PLEDGE).  Omit for primary URL.")
    ] = None,
    _scope=_require_scope("mmingest:read"),
) -> UrlResponse:
    """Return just the resolved URL for a media_id.

    Convenience for editors who need to paste a URL into PMM.
    Default returns primary.url.  With ?variant=PLEDGE returns that variant's URL.
    404 if the media_id has no primary (or no such variant).
    """
    engine = _get_engine()
    try:
        async with engine.connect() as conn:
            primary_rows, variant_rows, _ = await _resolve_asset(media_id, conn)
    finally:
        await engine.dispose()

    if not primary_rows and not variant_rows:
        raise HTTPException(status_code=404, detail=f"No asset found for media_id={media_id!r}")

    if variant is not None:
        # Return the matching variant's URL
        matched = [r for r in variant_rows if (r._mapping["variant_tag"] or "").upper() == variant.upper()]
        if not matched:
            raise HTTPException(
                status_code=404,
                detail=f"No variant {variant!r} found for media_id={media_id!r}",
            )
        return UrlResponse(url=matched[0]._mapping["remote_url"])

    # Default: primary URL
    if not primary_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No primary (non-variant) asset found for media_id={media_id!r}",
        )
    return UrlResponse(url=primary_rows[0]._mapping["remote_url"])


# ---------------------------------------------------------------------------
# 4. GET /assets/{media_id}/captions
# ---------------------------------------------------------------------------


@router.get("/assets/{media_id}/captions", response_model=CaptionResponse)
async def get_captions(
    media_id: str,
    format: Annotated[Literal["srt", "scc"], Query(description="Caption format")] = "srt",
    _scope=_require_scope("mmingest:read"),
) -> CaptionResponse:
    """Return cached sidecar body from DB.  NEVER round-trips to mmingest.

    404 — no sidecar row of that kind exists for the primary asset.
    503 — sidecar row exists but body_text is NULL or empty (indexer pending).
    """
    engine = _get_engine()
    try:
        async with engine.connect() as conn:
            primary_rows, _, _ = await _resolve_asset(media_id, conn)

            if not primary_rows:
                raise HTTPException(status_code=404, detail=f"No asset found for media_id={media_id!r}")

            file_id = primary_rows[0]._mapping["id"]

            sidecar_sql = text("""
                SELECT id, kind, body_text, bytes, fetched_at
                FROM   mmingest_sidecars
                WHERE  file_id = :file_id
                  AND  kind = :kind
                LIMIT 1
            """)
            sidecar_row = (await conn.execute(sidecar_sql, {"file_id": file_id, "kind": format})).fetchone()
    finally:
        await engine.dispose()

    if sidecar_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No {format!r} sidecar found for media_id={media_id!r}",
        )

    body_text = sidecar_row._mapping["body_text"]
    if not body_text:
        # Row exists but indexer hasn't filled body_text yet.
        # Return 503 — do NOT proxy to mmingest.
        raise HTTPException(
            status_code=503,
            headers={"Retry-After": "60"},
            detail=f"Sidecar body for media_id={media_id!r} format={format!r} is still being indexed.  Retry shortly.",
        )

    return CaptionResponse(
        media_id=media_id,
        kind=sidecar_row._mapping["kind"],
        body_text=body_text,
        bytes=sidecar_row._mapping["bytes"],
        fetched_at=sidecar_row._mapping["fetched_at"],
    )


# ---------------------------------------------------------------------------
# 5. GET /recent
# ---------------------------------------------------------------------------


@router.get("/recent", response_model=RecentResponse)
async def get_recent(
    since: Annotated[
        Optional[datetime],
        Query(description="Return rows where first_seen_at >= since.  Default: last 24h."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Max results (1-200)")] = 50,
    prefix: Annotated[Optional[str], Query(description="Filter by 4-char prefix (exact, case-insensitive)")] = None,
    _scope=_require_scope("mmingest:read"),
) -> RecentResponse:
    """Chronological listing of recently-indexed files.

    Surfaces only current rows (superseded_by IS NULL).
    Ordered by first_seen_at DESC so the newest arrivals appear first.
    """
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(hours=24)

    engine = _get_engine()
    try:
        async with engine.connect() as conn:
            recent_sql = text("""
                SELECT media_id, prefix, show_name, file_type, remote_url,
                       first_seen_at, remote_modified_at
                FROM   mmingest_files
                WHERE  first_seen_at >= :since
                  AND  (:prefix IS NULL OR LOWER(prefix) = LOWER(:prefix))
                  AND  superseded_by IS NULL
                ORDER  BY first_seen_at DESC
                LIMIT  :limit
            """)

            count_sql = text("""
                SELECT COUNT(*)
                FROM   mmingest_files
                WHERE  first_seen_at >= :since
                  AND  (:prefix IS NULL OR LOWER(prefix) = LOWER(:prefix))
                  AND  superseded_by IS NULL
            """)

            params = {
                "since": since.isoformat(),
                "prefix": prefix,
                "limit": limit,
            }
            count_params = {
                "since": since.isoformat(),
                "prefix": prefix,
            }

            rows = (await conn.execute(recent_sql, params)).fetchall()
            total = (await conn.execute(count_sql, count_params)).scalar() or 0
    finally:
        await engine.dispose()

    results = [
        RecentEntry(
            media_id=row._mapping["media_id"],
            prefix=row._mapping["prefix"],
            show_name=row._mapping["show_name"],
            file_type=row._mapping["file_type"],
            url=row._mapping["remote_url"],
            first_seen_at=row._mapping["first_seen_at"],
            remote_modified_at=row._mapping["remote_modified_at"],
        )
        for row in rows
    ]

    return RecentResponse(results=results, total=total)
