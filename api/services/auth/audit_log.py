"""Audit log service for ``/api/mmingest/*`` access.

Every request that passes through the auth middleware on an ``/api/mmingest/``
path writes one row here — successes, scope-denials, and shared-key callers
alike.  This gives operators a complete access history regardless of outcome.

Table: ``mmingest_audit_log``
-----------------------------
Created by migration 018.  Schema mirrors what the middleware needs:

    id           INTEGER PK AUTOINCREMENT
    consumer_id  INTEGER  NULL  — NULL for shared-key callers
    path         TEXT     NOT NULL
    media_id     TEXT     NULL  — extracted from path when applicable
    ts           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    outcome      TEXT     NOT NULL  ('allowed' | 'denied' | 'shared_key')
    FOREIGN KEY (consumer_id) REFERENCES consumer_keys(id)
"""

import re
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, ForeignKey, Integer, MetaData, Table, Text, func

from api.services.database import get_session

# ---------------------------------------------------------------------------
# Table definition (table-centric, consistent with database.py pattern)
# ---------------------------------------------------------------------------

_metadata = MetaData()

mmingest_audit_log_table = Table(
    "mmingest_audit_log",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("consumer_id", Integer, ForeignKey("consumer_keys.id"), nullable=True),
    Column("path", Text, nullable=False),
    Column("media_id", Text, nullable=True),
    Column("ts", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("outcome", Text, nullable=False),
)

# ---------------------------------------------------------------------------
# Path → media_id extraction
# ---------------------------------------------------------------------------

# Matches /api/mmingest/assets/{media_id} and /api/mmingest/assets/{media_id}/...
_ASSET_MEDIA_ID_RE = re.compile(r"^/api/mmingest/assets/([^/]+)(?:/.*)?$")


def _extract_media_id(path: str) -> Optional[str]:
    """Extract the ``media_id`` path segment from an asset URL, if present.

    Examples::

        /api/mmingest/assets/2WLI1209HD          → "2WLI1209HD"
        /api/mmingest/assets/2WLI1209HD/stream   → "2WLI1209HD"
        /api/mmingest/search?q=test              → None
        /api/mmingest/recent                     → None
    """
    m = _ASSET_MEDIA_ID_RE.match(path)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

# Valid outcome values (for documentation; not enforced here to avoid coupling)
OUTCOME_ALLOWED = "allowed"
OUTCOME_DENIED = "denied"
OUTCOME_SHARED_KEY = "shared_key"


async def write_audit_log(
    consumer_id: Optional[int],
    path: str,
    media_id: Optional[str],
    timestamp: datetime,
    outcome: str,
) -> None:
    """Write one row to ``mmingest_audit_log``.

    Designed to be called from middleware — fast, fire-and-forget friendly.
    ``media_id`` can be supplied explicitly (if the caller already parsed it)
    or passed as ``None`` (this function won't extract it from the path; let
    the middleware pass it for a cleaner separation of concerns).

    Args:
        consumer_id: FK into ``consumer_keys.id``; ``None`` for shared-key callers.
        path:        Full request path, e.g. ``/api/mmingest/assets/2WLI1209HD``.
        media_id:    Extracted media ID, or ``None``.
        timestamp:   The datetime of the request (caller provides, avoids DB skew).
        outcome:     One of ``"allowed"``, ``"denied"``, ``"shared_key"``.
    """
    async with get_session() as session:
        stmt = mmingest_audit_log_table.insert().values(
            consumer_id=consumer_id,
            path=path,
            media_id=media_id,
            ts=timestamp,
            outcome=outcome,
        )
        await session.execute(stmt)


def extract_media_id_from_path(path: str) -> Optional[str]:
    """Public helper so the middleware can extract ``media_id`` without
    importing the internal regex directly.

    See :func:`_extract_media_id` for matching rules.
    """
    return _extract_media_id(path)
