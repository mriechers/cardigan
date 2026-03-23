"""
Langfuse Analytics Service for Cardigan

Provides integration with Langfuse observability platform for:
- Real-time model usage statistics
- Per-job cost tracking
- Analytics dashboard data

Uses httpx to call Langfuse's REST API directly (no SDK dependency).

Langfuse credentials are loaded from:
1. Environment variables / .env file (preferred — contains current keys)
2. macOS Keychain via keychain_secrets (fallback)
"""

import importlib.util
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

from api.services.logging import get_logger

logger = get_logger(__name__)

# Optionally load keychain_secrets without sys.path manipulation.
# The module lives in ~/Developer/the-lodge/scripts/ and may not be
# on sys.path, so we probe the known location with importlib.util.
_keychain_get_secret = None
_keychain_path = Path.home() / "Developer/the-lodge/scripts/keychain_secrets.py"
if _keychain_path.exists():
    try:
        spec = importlib.util.spec_from_file_location("keychain_secrets", _keychain_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _keychain_get_secret = getattr(mod, "get_secret", None)
    except Exception:
        pass


def _get_langfuse_credential(key: str) -> Optional[str]:
    """Get Langfuse credential from environment first, Keychain as fallback."""
    # Ensure .env is loaded (idempotent — safe to call multiple times)
    load_dotenv()

    # Prefer environment / .env (contains current, correct keys)
    value = os.environ.get(key)
    if value:
        return value

    # Fall back to Keychain
    if _keychain_get_secret:
        value = _keychain_get_secret(key)
        if value:
            return value

    return None


@dataclass
class ModelStats:
    """Statistics for a single model."""

    model_name: str
    request_count: int
    total_cost: float
    total_tokens: int
    avg_latency_ms: Optional[float] = None


@dataclass
class ModelStatsResponse:
    """Response containing model usage statistics."""

    models: List[ModelStats]
    period_start: datetime
    period_end: datetime
    total_cost: float
    total_requests: int


class LangfuseClient:
    """
    Client for Langfuse analytics REST API.

    Uses httpx.AsyncClient to call Langfuse endpoints directly,
    bypassing the Langfuse Python SDK (which has pydantic.v1 issues on Python 3.14).
    """

    def __init__(self):
        self._http_client: Optional[httpx.AsyncClient] = None
        self._initialized = False
        self._init_error: Optional[str] = None
        self._host: Optional[str] = None

    def _ensure_initialized(self) -> bool:
        """Lazily initialize credentials and HTTP client."""
        if self._initialized:
            return self._http_client is not None

        self._initialized = True

        public_key = _get_langfuse_credential("LANGFUSE_PUBLIC_KEY")
        secret_key = _get_langfuse_credential("LANGFUSE_SECRET_KEY")
        self._host = _get_langfuse_credential("LANGFUSE_BASE_URL") or "https://cloud.langfuse.com"

        if not public_key or not secret_key:
            self._init_error = "Langfuse credentials not found in environment or Keychain"
            logger.warning(self._init_error)
            return False

        try:
            self._http_client = httpx.AsyncClient(
                base_url=self._host.rstrip("/"),
                auth=(public_key, secret_key),
                timeout=httpx.Timeout(15.0),
                headers={"Content-Type": "application/json"},
            )
            logger.info(f"Langfuse REST client initialized (host: {self._host})")
            return True
        except Exception as e:
            self._init_error = f"Failed to initialize Langfuse HTTP client: {e}"
            logger.error(self._init_error)
            return False

    def is_available(self) -> bool:
        """Check if Langfuse is configured and available."""
        return self._ensure_initialized()

    def get_status(self) -> Dict[str, Any]:
        """Get Langfuse connection status."""
        available = self._ensure_initialized()
        return {
            "available": available,
            "error": self._init_error if not available else None,
            "host": self._host or _get_langfuse_credential("LANGFUSE_BASE_URL") or "https://cloud.langfuse.com",
        }

    async def trace_generation(
        self,
        name: str,
        model: str,
        input_messages: List[Dict[str, str]],
        output: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        cost: float,
        duration_ms: int,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        job_id: Optional[int] = None,
        phase: Optional[str] = None,
        tier: Optional[int] = None,
        tier_label: Optional[str] = None,
        backend: Optional[str] = None,
    ) -> Optional[str]:
        """
        Create a Langfuse generation trace for an LLM call.

        Sends a batch ingestion request containing both a trace-create
        and a generation-create event in a single HTTP POST.

        Returns:
            Trace ID if successful, None otherwise
        """
        if not self._ensure_initialized():
            return None

        try:
            now = datetime.now(timezone.utc).isoformat()
            trace_id = str(uuid.uuid4())
            generation_id = str(uuid.uuid4())

            # Build trace metadata
            trace_metadata = metadata or {}
            trace_metadata.update(
                {
                    "backend": backend,
                    "tier": tier,
                    "tier_label": tier_label,
                    "duration_ms": duration_ms,
                }
            )

            # Build tags list
            trace_tags = tags or []
            if phase:
                trace_tags.append(f"phase:{phase}")
            if tier_label:
                trace_tags.append(f"tier:{tier_label}")
            trace_tags.append("editorial-assistant")

            # Build batch ingestion payload
            batch = [
                {
                    "id": str(uuid.uuid4()),
                    "type": "trace-create",
                    "timestamp": now,
                    "body": {
                        "id": trace_id,
                        "name": f"job-{job_id}" if job_id else name,
                        "userId": f"job-{job_id}" if job_id else None,
                        "sessionId": f"session-{job_id}" if job_id else None,
                        "tags": trace_tags,
                        "metadata": {"job_id": job_id, "phase": phase},
                    },
                },
                {
                    "id": str(uuid.uuid4()),
                    "type": "generation-create",
                    "timestamp": now,
                    "body": {
                        "id": generation_id,
                        "traceId": trace_id,
                        "name": name,
                        "model": model,
                        "input": input_messages,
                        "output": output,
                        "usage": {
                            "input": input_tokens,
                            "output": output_tokens,
                            "total": total_tokens,
                        },
                        "metadata": trace_metadata,
                        "level": "DEFAULT",
                    },
                },
            ]

            resp = await self._http_client.post(
                "/api/public/ingestion",
                json={"batch": batch},
            )
            resp.raise_for_status()

            logger.debug(f"Langfuse trace created: {trace_id} for phase={phase}, model={model}")
            return trace_id

        except Exception as e:
            logger.warning(f"Failed to create Langfuse trace: {e}")
            return None

    async def get_model_stats(self, days: int = 7, limit: int = 20) -> Optional[ModelStatsResponse]:
        """
        Get model usage statistics from Langfuse.

        Queries the Langfuse Metrics v2 REST API to get aggregated stats
        grouped by providedModelName.

        Args:
            days: Number of days to look back (default: 7)
            limit: Maximum number of models to return (default: 20)

        Returns:
            ModelStatsResponse with usage stats, or None if unavailable
        """
        if not self._ensure_initialized():
            return None

        try:
            now = datetime.now(timezone.utc)
            period_end = now
            period_start = now - timedelta(days=days)

            query = {
                "view": "observations",
                "dimensions": [{"field": "providedModelName"}],
                "metrics": [
                    {"measure": "count", "aggregation": "count"},
                    {"measure": "totalCost", "aggregation": "sum"},
                    {"measure": "totalTokens", "aggregation": "sum"},
                ],
                "filters": [],
                "fromTimestamp": period_start.isoformat(),
                "toTimestamp": period_end.isoformat(),
            }

            resp = await self._http_client.get(
                "/api/public/v2/metrics",
                params={"query": json.dumps(query)},
            )
            resp.raise_for_status()
            result = resp.json()

            # Parse results
            models = []
            total_cost = 0.0
            total_requests = 0

            for row in (result.get("data") or [])[:limit]:
                model_name = row.get("providedModelName") or "unknown"
                count = int(row.get("count_count", 0))
                cost = float(row.get("sum_totalCost", 0) or 0)
                tokens = int(row.get("sum_totalTokens", 0) or 0)

                models.append(
                    ModelStats(
                        model_name=model_name,
                        request_count=count,
                        total_cost=cost,
                        total_tokens=tokens,
                        avg_latency_ms=None,
                    )
                )

                total_cost += cost
                total_requests += count

            # Sort by cost descending
            models.sort(key=lambda m: m.total_cost, reverse=True)

            return ModelStatsResponse(
                models=models,
                period_start=period_start,
                period_end=period_end,
                total_cost=total_cost,
                total_requests=total_requests,
            )

        except Exception as e:
            logger.error(f"Failed to fetch model stats from Langfuse: {e}")
            return None

    async def get_trace_cost(self, trace_id: str) -> Optional[float]:
        """
        Get the actual cost for a specific trace.

        Used for per-job cost tracking after job completion.

        Args:
            trace_id: The Langfuse trace ID

        Returns:
            Total cost in USD, or None if not found
        """
        if not self._ensure_initialized():
            return None

        try:
            resp = await self._http_client.get(f"/api/public/traces/{trace_id}")
            resp.raise_for_status()
            trace = resp.json()

            total_cost = 0.0
            for obs in trace.get("observations") or []:
                calc_cost = obs.get("calculatedTotalCost")
                if calc_cost is not None:
                    total_cost += float(calc_cost)
            return total_cost

        except Exception as e:
            logger.error(f"Failed to fetch trace cost from Langfuse: {e}")
            return None


# Module-level singleton
_langfuse_client: Optional[LangfuseClient] = None


def get_langfuse_client() -> LangfuseClient:
    """Get the singleton Langfuse client instance."""
    global _langfuse_client
    if _langfuse_client is None:
        _langfuse_client = LangfuseClient()
    return _langfuse_client
