"""API key authentication middleware.

When CARDIGAN_API_KEY is set, all requests (except exempt paths) must include
a matching X-API-Key header. When the env var is empty or absent, auth is
disabled (dev mode).

Auth decision order
-------------------
1. Shared-key path (unchanged from pre-Sprint-3A behaviour)
   If the provided key matches ``CARDIGAN_API_KEY``, the request proceeds.
   For ``/api/mmingest/*`` paths an audit log entry is written with
   ``outcome='shared_key'``.

2. Consumer-key path (Sprint 3A addition)
   If the provided key does NOT match the shared key, attempt a consumer-key
   lookup via ``lookup_consumer_key``.  On match:
     a. For ``/api/mmingest/*`` paths, enforce the required scope
        (``mmingest:read`` for general endpoints; ``mmingest:stream`` for
        ``/stream`` sub-paths).  Return 403 + audit entry on scope failure.
     b. Attach ``request.state.consumer_id`` and ``request.state.consumer_scopes``
        for downstream use by routers.
     c. Write audit log entry with ``outcome='allowed'``.
     d. Schedule ``touch_last_used`` as a background task (fire-and-forget).
     e. Call next middleware.

3. If neither path matched, return 401.

Scope vocabulary (Sprint 3A)
-----------------------------
``mmingest:read``    — required for ``GET /api/mmingest/*`` (non-stream)
``mmingest:stream``  — required for ``GET /api/mmingest/assets/{id}/stream``

Future scopes are added by extending ``_required_scope_for`` — exact string
match, no wildcards, CSV column in ``consumer_keys.scopes``.
"""

import asyncio
import os
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Paths that never require authentication (exact match).
EXEMPT_PATHS = frozenset({"/", "/api/system/health", "/docs", "/openapi.json"})

# Path prefixes exempt from authentication.
# WebSocket paths use the token query param for auth, not X-API-Key header.
EXEMPT_PREFIXES = ("/api/ws/",)

# Prefix that triggers consumer-scope enforcement + audit logging.
_MMINGEST_PREFIX = "/api/mmingest/"


def _required_scope_for(path: str, method: str) -> str:
    """Return the scope string required for a given mmingest path + method.

    ``/api/mmingest/assets/{id}/stream`` requires ``mmingest:stream``.
    All other ``/api/mmingest/`` paths require ``mmingest:read``.

    This is the single authoritative mapping for Sprint 3A scope vocabulary.
    Sprint 3B routers trust the middleware decision and do NOT re-check.
    """
    if "stream" in path.split("/"):
        return "mmingest:stream"
    return "mmingest:read"


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        shared_key = os.environ.get("CARDIGAN_API_KEY", "")
        if not shared_key:
            # Dev mode — no auth required.
            return await call_next(request)

        path = request.url.path
        if path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES):
            return await call_next(request)

        provided = request.headers.get("X-API-Key", "")

        # ------------------------------------------------------------------
        # Path 1 — shared-key (back-compat, unchanged behaviour)
        # ------------------------------------------------------------------
        if provided == shared_key:
            if path.startswith(_MMINGEST_PREFIX):
                await _fire_audit(
                    consumer_id=None,
                    path=path,
                    outcome="shared_key",
                )
            return await call_next(request)

        # ------------------------------------------------------------------
        # Path 2 — consumer key lookup
        # ------------------------------------------------------------------
        # Import here to avoid circular import at module load time.
        from api.services.auth.audit_log import OUTCOME_ALLOWED, OUTCOME_DENIED, extract_media_id_from_path
        from api.services.auth.consumer_keys import lookup_consumer_key, touch_last_used

        try:
            consumer = await lookup_consumer_key(provided)
        except Exception as _lookup_exc:
            # Treat any DB-level failure (not initialized, table missing, etc.)
            # as "no consumer key found" — the request will get a 401.  This
            # keeps auth errors non-fatal and handles test environments where
            # the consumer_keys table may not exist yet.
            import logging as _logging

            _logging.getLogger(__name__).debug(
                "Consumer key lookup failed (%s) — treating as no match",
                type(_lookup_exc).__name__,
            )
            consumer = None
        if consumer is not None:
            if path.startswith(_MMINGEST_PREFIX):
                required = _required_scope_for(path, request.method)
                media_id = extract_media_id_from_path(path)

                if required not in consumer.scopes:
                    await _fire_audit(
                        consumer_id=consumer.id,
                        path=path,
                        media_id=media_id,
                        outcome=OUTCOME_DENIED,
                    )
                    return JSONResponse(
                        status_code=403,
                        content={"detail": f"Insufficient scope — '{required}' required"},
                    )

                await _fire_audit(
                    consumer_id=consumer.id,
                    path=path,
                    media_id=media_id,
                    outcome=OUTCOME_ALLOWED,
                )

            # Attach consumer identity for downstream router use.
            request.state.consumer_id = consumer.id
            request.state.consumer_scopes = consumer.scopes

            # Fire-and-forget last_used_at update — a small lag is acceptable.
            asyncio.create_task(touch_last_used(consumer.id))

            return await call_next(request)

        # ------------------------------------------------------------------
        # Path 3 — neither key matched
        # ------------------------------------------------------------------
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"},
        )


async def _fire_audit(
    consumer_id,
    path: str,
    media_id=None,
    outcome: str = "shared_key",
) -> None:
    """Write an audit log entry, suppressing any DB errors.

    Audit failures MUST NOT block request processing.
    """
    try:
        from api.services.auth.audit_log import write_audit_log

        await write_audit_log(
            consumer_id=consumer_id,
            path=path,
            media_id=media_id,
            timestamp=datetime.now(timezone.utc),
            outcome=outcome,
        )
    except Exception:
        # Audit log write failure is non-fatal — log but continue.
        import logging

        logging.getLogger(__name__).warning(
            "Failed to write mmingest audit log entry (path=%s outcome=%s)",
            path,
            outcome,
            exc_info=True,
        )
