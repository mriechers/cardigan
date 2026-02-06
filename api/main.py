"""
Editorial Assistant v3.0 - FastAPI Application

Main entry point for the API server.
"""

import os
import sys
from pathlib import Path

# Load secrets from Keychain into environment (falls back to .env)
# This must happen before any imports that use the secrets
sys.path.insert(0, str(Path.home() / "Developer/the-lodge/scripts"))
try:
    from keychain_secrets import get_secret

    # Load known secrets into environment if not already set
    for key in ["OPENROUTER_API_KEY", "AIRTABLE_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]:
        if key not in os.environ:
            value = get_secret(key)
            if value:
                os.environ[key] = value
except ImportError:
    pass  # Keychain module not available (e.g., CI/Docker)

# Load remaining environment variables from .env file
from dotenv import load_dotenv

load_dotenv()

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

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
    logger.info("Starting Editorial Assistant API v3.0")
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
    title="Editorial Assistant API",
    description="API for PBS Wisconsin Editorial Assistant v3.0",
    version="3.0.0-dev",
    lifespan=lifespan,
)

# CORS middleware for web dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # React dev server
        "http://localhost:5173",  # Vite dev server
        "http://metadata.neighborhood:3000",  # Local domain alias
        "https://cardigan.bymarkriechers.com",  # Cloudflare Tunnel
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses."""
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "version": "3.0.0-dev"}


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
