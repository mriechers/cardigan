"""
Cardigan v4.0 - FastAPI Application

Main entry point for the API server.
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()

from api.services.secrets import bootstrap_secrets

bootstrap_secrets()

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from api import __version__
from api.middleware.auth import APIKeyMiddleware
from api.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from api.services import database
from api.services.ingest_config import ensure_defaults as ensure_ingest_defaults
from api.services.ingest_scheduler import start_scheduler, stop_scheduler
from api.services.langfuse_client import get_langfuse_client
from api.services.llm import close_llm_client, get_llm_client
from api.services.logging import get_logger, setup_logging
from api.services.mmingest.scheduler import start_mmingest_scheduler, stop_mmingest_scheduler

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
    llm_client = get_llm_client()  # Initialize LLM client
    logger.info("LLM client initialized")
    # Fail-fast at boot if the house-style YAML can't render the phase prompts
    # (PR #295 review #1). Every phase prompt carries {{style:*}} tokens now, so
    # a missing/corrupt config/house_style.yaml would otherwise fail every job
    # at runtime. Runtime still degrades gracefully (worker._load_agent_prompt
    # strips tokens); this surfaces the problem loudly at deploy instead.
    from api.services.style_engine.prompt_blocks import PromptBlockError, validate_prompt_blocks
    from api.services.worker import AGENTS_DIR

    _style_cfg = llm_client.config.get("routing", {}).get("style_engine", {})
    _rules_file = _style_cfg.get("rules_file", "config/house_style.yaml")
    try:
        _validated = validate_prompt_blocks(AGENTS_DIR, rules_path=_rules_file)
        logger.info("Prompt-block validation passed (%d prompt(s) carry style tokens)", len(_validated))
    except PromptBlockError:
        logger.critical("House-style prompt-block validation FAILED; refusing to start", exc_info=True)
        raise
    langfuse = get_langfuse_client()
    await langfuse.initialize()
    logger.info("Langfuse client initialized")
    # Initialize ingest config defaults (Sprint 11.1)
    await ensure_ingest_defaults()
    logger.info("Ingest configuration initialized")
    # Start ingest scheduler (Sprint 11.1)
    await start_scheduler()
    logger.info("Ingest scheduler started")
    # Start mmingest delta-walk scheduler (was the documented Sprint 2 TODO:
    # the crawler/indexer existed but was never wired into the app lifespan,
    # so mmingest_files/sidecars never populated in a running deployment).
    await start_mmingest_scheduler()
    logger.info("mmingest scheduler started")
    yield
    # Shutdown: Close connections
    logger.info("Shutting down API server")
    await stop_mmingest_scheduler()
    await stop_scheduler()
    await close_llm_client()
    await database.close_db()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Cardigan API",
    description="API for Cardigan - PBS Wisconsin transcript processing and metadata generation",
    version=__version__,
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

    logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "version": __version__}


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

    # Get LLM status. Config-derived fields (primary_backend, fallback_model,
    # phase_backends) are valid from the in-process client. But active_backend,
    # active_model and last_run_totals are runtime state set by whichever process
    # actually ran the job — in the multi-container deployment that's the worker,
    # not this API process, so they're null here. Prefer the worker-published
    # snapshot from the shared DB, falling back to in-process state for the
    # single-process dev case (#158).
    llm_client = get_llm_client()
    llm_status = llm_client.get_status()

    active_backend = llm_status.get("active_backend")
    active_model = llm_status.get("active_model")
    last_run = llm_status.get("last_run_totals")

    runtime_item = await database.get_config("llm_runtime_status")
    if runtime_item and runtime_item.value:
        try:
            runtime = json.loads(runtime_item.value)
            active_backend = runtime.get("active_backend") or active_backend
            active_model = runtime.get("active_model") or active_model
            last_run = runtime.get("last_run_totals") or last_run
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "status": "ok",
        "queue": queue_stats,
        "llm": {
            "active_backend": active_backend,
            "active_model": active_model,
            "primary_backend": llm_status.get("primary_backend"),
            "fallback_model": llm_status.get("fallback_model"),
            "phase_backends": llm_status.get("phase_backends"),
        },
        "last_run": last_run,
    }


# Register routers
from api.routers import config, export, ingest, jobs, langfuse, mmingest, queue, system, upload, websocket

app.include_router(queue.router, prefix="/api/queue", tags=["queue"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(config.router, prefix="/api", tags=["config"])
app.include_router(websocket.router, prefix="/api", tags=["websocket"])
app.include_router(upload.router, prefix="/api/upload", tags=["upload"])
app.include_router(system.router, prefix="/api/system", tags=["system"])
app.include_router(ingest.router, prefix="/api/ingest", tags=["ingest"])
app.include_router(langfuse.router, prefix="/api/langfuse", tags=["langfuse"])
app.include_router(export.router, prefix="/api", tags=["export"])
app.include_router(mmingest.router, prefix="/api/mmingest", tags=["mmingest"])
