"""Rate limiting middleware using slowapi.

Expensive endpoints (POST queue, ingest scan, upload): 10/min
Read endpoints (GET): 60/min
"""

import os

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["60/minute"],
    enabled=os.getenv("TESTING") != "1",
)

# Rate limit strings used by endpoint decorators.
RATE_EXPENSIVE = "10/minute"
RATE_READ = "60/minute"


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Return 429 with Retry-After header when rate limit is exceeded."""
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Try again later."},
        headers={"Retry-After": str(exc.detail.split()[-1]) if exc.detail else "60"},
    )
