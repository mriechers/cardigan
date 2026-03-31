"""
Cardigan v4.0 - FastAPI Application

Main entry point for the API server.
"""

import importlib.util
import os
from pathlib import Path

# Load .env file FIRST — it contains the current, correct credentials
from dotenv import load_dotenv

load_dotenv()

# Then backfill from Keychain for any keys still missing.
# keychain_secrets isn't on sys.path, so use spec_from_file_location.
_keychain_path = Path.home() / "Developer/the-lodge/scripts/keychain_secrets.py"
if _keychain_path.exists():
    try:
        spec = importlib.util.spec_from_file_location("keychain_secrets", _keychain_path)
        if spec and spec.loader:
            _keychain_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(_keychain_mod)
            _get_secret = getattr(_keychain_mod, "get_secret", None)
            if _get_secret:
                for key in ["OPENROUTER_API_KEY", "AIRTABLE_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]:
                    if key not in os.environ:
                        value = _get_secret(key)
                        if value:
                            os.environ[key] = value
    except Exception:
        pass  # Keychain module not available (e.g., CI/Docker)

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from api.middleware.auth import APIKeyMiddleware
from api.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from api.services import database
from api.services.ingest_config import ensure_defaults as ensure_ingest_defaults
from api.services.ingest_scheduler import start_scheduler, stop_scheduler
from api.services.llm import close_llm_client, get_llm_client
from api.services.logging import get_logger, setup_logging

# Initialize logging for API
setup_logging(log_file="api.log")
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan events.

    Initializes database connection pool and LLM client on startup,
    closes connections on shutdown.
    """
    # Startup: Initialize database and LLM client
    logger.info("Starting Cardigan API v4.0")
    await database.init_db()
    logger.info("Database initialized")
    get_llm_client()  # Initialize LLM client
    logger.info("LLM client initialized")
    # Initialize ingest config defaults (Sprint 11.1)
    await ensure_ingest_defaults()
    logger.info("Ingest configuration initialized")
    # Start ingest scheduler (Sprint 11.1)
    await start_scheduler()
    logger.info("Ingest scheduler started")
    yield
    # Shutdown: Close connections
    logger.info("Shutting down API server")
    await stop_scheduler()
    await close_llm_client()
    await database.close_db()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Cardigan API",
    description="API for Cardigan - PBS Wisconsin transcript processing and metadata generation",
    version="4.0.0",
    lifespan=lifespan,
)

# CORS middleware for web dashboard
_default_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
]
_cors_env = os.environ.get("CORS_ORIGINS", "")
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()] if _cors_env else _default_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API key authentication middleware
app.add_middleware(APIKeyMiddleware)

# Rate limiter
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)


# Global exception handler: ensure ALL errors return JSON, never HTML
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    from fastapi.responses import JSONResponse

    logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "version": "4.0.0"}


@app.get("/api/system/health")
async def health():
    """Enhanced system health check endpoint.

    Returns system status including:
    - Basic health status
    - Queue statistics (pending, in_progress counts)
    - Active LLM model/preset info
    - Last run cost totals
    """
    from api.models.job import JobStatus

    # Get queue stats
    pending_jobs = await database.list_jobs(status=JobStatus.pending, limit=1000)
    in_progress_jobs = await database.list_jobs(status=JobStatus.in_progress, limit=1000)

    queue_stats = {
        "pending": len(pending_jobs),
        "in_progress": len(in_progress_jobs),
    }

    # Get LLM status
    llm_client = get_llm_client()
    llm_status = llm_client.get_status()

    return {
        "status": "ok",
        "queue": queue_stats,
        "llm": {
            "active_backend": llm_status.get("active_backend"),
            "active_model": llm_status.get("active_model"),
            "active_preset": llm_status.get("active_preset"),
            "primary_backend": llm_status.get("primary_backend"),
            "configured_preset": llm_status.get("configured_preset"),
            "fallback_model": llm_status.get("fallback_model"),
            "phase_backends": llm_status.get("phase_backends"),
            "openrouter_presets": llm_status.get("openrouter_presets"),
        },
        "last_run": llm_status.get("last_run_totals"),
    }


# Register routers
from api.routers import chat_prototype, config, ingest, jobs, langfuse, queue, system, upload, websocket

app.include_router(queue.router, prefix="/api/queue", tags=["queue"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(config.router, prefix="/api", tags=["config"])
app.include_router(websocket.router, prefix="/api", tags=["websocket"])
app.include_router(upload.router, prefix="/api/upload", tags=["upload"])
app.include_router(system.router, prefix="/api/system", tags=["system"])
app.include_router(ingest.router, prefix="/api/ingest", tags=["ingest"])
app.include_router(langfuse.router, prefix="/api/langfuse", tags=["langfuse"])
app.include_router(chat_prototype.router, prefix="/api/chat", tags=["chat-prototype"])
