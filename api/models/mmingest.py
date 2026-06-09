"""Pydantic models for the mmingest search/asset/captions/recent API endpoints.

All request and response shapes for the five endpoints in api/routers/mmingest.py
live here to keep the router file readable.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Search endpoint
# ---------------------------------------------------------------------------


class SearchResult(BaseModel):
    """One hit returned by GET /api/mmingest/search."""

    media_id: Optional[str] = None
    prefix: Optional[str] = None
    season: Optional[str] = None
    episode: Optional[str] = None
    revision_date: Optional[str] = None
    modified_at: Optional[datetime] = None  # alias for remote_modified_at
    snippet: str
    sidecar_kind: str  # 'srt' | 'scc'


class SearchResponse(BaseModel):
    """Response envelope for GET /api/mmingest/search."""

    results: list[SearchResult]
    total: int


# ---------------------------------------------------------------------------
# Asset endpoint
# ---------------------------------------------------------------------------


class AssetEntry(BaseModel):
    """One file row returned in an asset response."""

    file_id: int
    media_id: str
    variant_tag: Optional[str] = None
    revision_date: Optional[str] = None
    url: str
    file_type: str
    remote_modified_at: Optional[datetime] = None
    file_size_bytes: Optional[int] = None
    # Populated on primary only via Airtable batch lookup.
    # The indexer also pre-caches this value in mmingest_files.airtable_record_id;
    # for /assets/{id} we do a live lookup to get the freshest value.
    airtable_record_id: Optional[str] = None


class AssetResponse(BaseModel):
    """Response for GET /api/mmingest/assets/{media_id}.

    Shape per the variant-selection rule:
      primary    — the no-variant-tag, latest-REV row (superseded_by IS NULL)
      variants   — rows with a non-NULL variant_tag and superseded_by IS NULL
                   (PLEDGE, DS, etc. — coexisting cuts, not superseded)
      superseded — older _REV rows (superseded_by IS NOT NULL) for all groups
    """

    primary: Optional[AssetEntry] = None
    variants: list[AssetEntry] = Field(default_factory=list)
    superseded: list[AssetEntry] = Field(default_factory=list)


class UrlResponse(BaseModel):
    """Response for GET /api/mmingest/assets/{media_id}/url."""

    url: str


# ---------------------------------------------------------------------------
# Captions endpoint
# ---------------------------------------------------------------------------


class CaptionResponse(BaseModel):
    """Response for GET /api/mmingest/assets/{media_id}/captions."""

    media_id: str
    kind: str  # 'srt' | 'scc'
    body_text: str
    bytes: Optional[int] = None
    fetched_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Recent endpoint
# ---------------------------------------------------------------------------


class RecentEntry(BaseModel):
    """One row returned by GET /api/mmingest/recent."""

    media_id: Optional[str] = None
    prefix: Optional[str] = None
    show_name: Optional[str] = None
    file_type: str
    url: str
    first_seen_at: datetime
    remote_modified_at: Optional[datetime] = None


class RecentResponse(BaseModel):
    """Response envelope for GET /api/mmingest/recent."""

    results: list[RecentEntry]
    total: int
