"""Consumer key service — creation, lookup, and maintenance.

Consumer keys are random URL-safe tokens hashed at rest with bcrypt.  The
plaintext is returned to the operator exactly once at creation time; the
database only ever stores the bcrypt hash.

Lookup strategy
---------------
We iterate all active rows in ``consumer_keys`` and bcrypt-compare each hash
against the provided plaintext.  This is correct for small key populations
(tens of keys, not thousands) and keeps the implementation simple.

If the population grows to hundreds of keys and auth latency becomes a concern,
the right optimisation is a two-tier lookup:
  1. Store an HMAC-SHA256 prefix of the key as an indexed column.
  2. Filter rows by prefix first, then bcrypt-verify the small candidate set.
That optimisation is explicitly deferred — it has not been needed in any prior
deployment of this system.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import bcrypt

# ---------------------------------------------------------------------------
# SQLAlchemy table definition (parallel to database.py pattern — table-centric,
# not ORM-mapped, to stay consistent with the existing codebase style).
# ---------------------------------------------------------------------------
from sqlalchemy import Column, DateTime, Integer, MetaData, Table, Text, func, select, update

from api.services.database import get_session

_metadata = MetaData()

consumer_keys_table = Table(
    "consumer_keys",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key_hash", Text, nullable=False),
    Column("label", Text, nullable=False),
    Column("scopes", Text, nullable=False, server_default=""),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("last_used_at", DateTime, nullable=True),
    Column("active", Integer, nullable=False, server_default="1"),
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsumerKeyRecord:
    """Immutable view of a consumer_keys row returned from the database."""

    id: int
    label: str
    scopes: frozenset
    created_at: Optional[datetime]
    last_used_at: Optional[datetime]


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


async def create_consumer_key(label: str, scopes: list[str]) -> tuple[str, int]:
    """Create a new consumer key.

    Generates a cryptographically secure random token, hashes it with bcrypt,
    inserts a row into ``consumer_keys``, and returns the plaintext ONCE.

    Args:
        label:  Human-readable identifier, e.g. ``"search-frontend-prod"``.
        scopes: List of scope strings, e.g. ``["mmingest:read"]``.

    Returns:
        ``(plaintext_key, consumer_id)`` — the plaintext is the only time the
        raw token is visible; store it securely before discarding.
    """
    plaintext = secrets.token_urlsafe(32)
    key_hash = bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt()).decode()
    scopes_csv = ",".join(sorted(set(scopes)))

    async with get_session() as session:
        stmt = consumer_keys_table.insert().values(
            key_hash=key_hash,
            label=label,
            scopes=scopes_csv,
            active=1,
        )
        result = await session.execute(stmt)
        consumer_id: int = result.inserted_primary_key[0]

    return plaintext, consumer_id


async def lookup_consumer_key(plaintext: str) -> Optional[ConsumerKeyRecord]:
    """Look up a consumer key by its plaintext value.

    Fetches all active rows and bcrypt-compares each hash.  Returns the
    matching ``ConsumerKeyRecord``, or ``None`` if no active key matches.

    Args:
        plaintext: The raw key provided by the caller in ``X-API-Key``.
    """
    if not plaintext:
        return None

    async with get_session() as session:
        stmt = select(consumer_keys_table).where(consumer_keys_table.c.active == 1)
        result = await session.execute(stmt)
        rows = result.fetchall()

    # bcrypt comparison is intentionally done OUTSIDE the session to avoid
    # holding the connection open during the (relatively slow) hash check.
    encoded = plaintext.encode()
    for row in rows:
        stored_hash = row.key_hash
        if bcrypt.checkpw(encoded, stored_hash.encode()):
            return ConsumerKeyRecord(
                id=row.id,
                label=row.label,
                scopes=frozenset(s.strip() for s in row.scopes.split(",") if s.strip()),
                created_at=row.created_at,
                last_used_at=row.last_used_at,
            )

    return None


async def touch_last_used(consumer_id: int) -> None:
    """Update ``last_used_at`` to NOW for the given consumer key.

    Designed to be called fire-and-forget via ``asyncio.create_task`` so the
    auth path does not block on the UPDATE.  A small lag in ``last_used_at``
    (up to a few seconds) is acceptable per the Sprint 3A spec.

    Args:
        consumer_id: Primary key of the row to touch.
    """
    async with get_session() as session:
        stmt = (
            update(consumer_keys_table)
            .where(consumer_keys_table.c.id == consumer_id)
            .values(last_used_at=func.current_timestamp())
        )
        await session.execute(stmt)


async def list_consumer_keys() -> list[dict]:
    """Return all rows in ``consumer_keys`` for CLI display.

    Returns a list of dicts with ``id``, ``label``, ``scopes``, ``active``,
    ``created_at``, ``last_used_at``.  The ``key_hash`` field is intentionally
    omitted — callers should never need to see hashes.
    """
    async with get_session() as session:
        stmt = select(consumer_keys_table).order_by(consumer_keys_table.c.id)
        result = await session.execute(stmt)
        rows = result.fetchall()

    return [
        {
            "id": row.id,
            "label": row.label,
            "scopes": row.scopes,
            "active": bool(row.active),
            "created_at": row.created_at,
            "last_used_at": row.last_used_at,
        }
        for row in rows
    ]


async def revoke_consumer_key(consumer_id: int) -> bool:
    """Mark a consumer key as inactive (soft-delete for audit-trail preservation).

    Args:
        consumer_id: Primary key of the row to revoke.

    Returns:
        ``True`` if a row was updated, ``False`` if the ID was not found.
    """
    async with get_session() as session:
        stmt = update(consumer_keys_table).where(consumer_keys_table.c.id == consumer_id).values(active=0)
        result = await session.execute(stmt)
        return result.rowcount > 0
