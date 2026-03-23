"""API key authentication middleware.

When CARDIGAN_API_KEY is set, all requests (except exempt paths) must include
a matching X-API-Key header. When the env var is empty or absent, auth is
disabled (dev mode).
"""

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Paths that never require authentication (exact match).
EXEMPT_PATHS = frozenset({"/", "/api/system/health", "/docs", "/openapi.json"})

# Path prefixes exempt from authentication.
# WebSocket paths use the token query param for auth, not X-API-Key header.
EXEMPT_PREFIXES = ("/api/ws/",)


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        api_key = os.environ.get("CARDIGAN_API_KEY", "")
        if not api_key:
            # Dev mode — no auth required.
            return await call_next(request)

        path = request.url.path
        if path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES):
            return await call_next(request)

        provided = request.headers.get("X-API-Key", "")
        if provided != api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)
