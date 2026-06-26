"""Dynamic model roster via OpenRouter API.

Fetches available models from OpenRouter, filters by configured family
patterns, and classifies each into a cost tier. Falls back to the static
`available_models` list in llm-config.json when OpenRouter is unreachable.
"""

import asyncio
import json
import logging
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from api.services.secrets import get_secret

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("config/llm-config.json")

# Cache: list of model dicts + expiry timestamp
_cache: Dict[str, Any] = {"models": None, "expires": 0.0}
_cache_lock = asyncio.Lock()
CACHE_TTL_SECONDS = 3600  # 1 hour


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _get_family_patterns(config: dict) -> List[dict]:
    """Return model_families from config, or empty list if not configured."""
    return config.get("model_families", [])


def _match_model(model_id: str, families: List[dict]) -> Optional[dict]:
    """Match a model ID against family patterns, return first match or None."""
    for family in families:
        for pattern in family["patterns"]:
            if fnmatch(model_id, pattern):
                return family
    return None


async def fetch_openrouter_models() -> Optional[List[dict]]:
    """Fetch model list from OpenRouter API.

    Returns the raw model list on success, None on failure.
    """
    api_key = get_secret("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY not available, cannot fetch models")
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
    except (httpx.HTTPError, KeyError, json.JSONDecodeError) as e:
        logger.warning("Failed to fetch OpenRouter models: %s", e)
        return None


def _classify_models(raw_models: List[dict], families: List[dict]) -> List[dict]:
    """Filter and classify raw OpenRouter models using family patterns.

    Returns a list of dicts matching the AvailableModel schema:
    {id, name, provider, tier}
    """
    results = []
    seen_ids = set()

    for model in raw_models:
        model_id = model.get("id", "")
        if model_id in seen_ids:
            continue

        family = _match_model(model_id, families)
        if family is None:
            continue

        # Extract pricing (OpenRouter reports cost per token as strings)
        pricing_input = None
        pricing_output = None
        raw_pricing = model.get("pricing", {})
        if raw_pricing:
            try:
                prompt_per_token = float(raw_pricing.get("prompt", 0))
                completion_per_token = float(raw_pricing.get("completion", 0))
                # Convert per-token to per-1M-tokens
                pricing_input = round(prompt_per_token * 1_000_000, 4)
                pricing_output = round(completion_per_token * 1_000_000, 4)
            except (ValueError, TypeError):
                pass

        seen_ids.add(model_id)
        results.append(
            {
                "id": model_id,
                "name": model.get("name", model_id),
                "provider": family["provider"],
                "tier": family["tier"],
                "pricing_input": pricing_input,
                "pricing_output": pricing_output,
            }
        )

    # Sort by tier, then name
    results.sort(key=lambda m: (m["tier"], m["name"]))
    return results


def _static_fallback(config: dict) -> List[dict]:
    """Return the static available_models from config as fallback."""
    return config.get("available_models", [])


async def get_available_models() -> List[dict]:
    """Get the current model roster, using cache when fresh.

    Priority:
    1. Cached dynamic roster (if within TTL)
    2. Fresh fetch from OpenRouter → classify → cache
    3. Static fallback from config
    """
    now = time.time()

    # Fast path: return cache if fresh
    if _cache["models"] is not None and now < _cache["expires"]:
        return _cache["models"]

    async with _cache_lock:
        # Re-check after acquiring lock (another coroutine may have refreshed)
        now = time.time()
        if _cache["models"] is not None and now < _cache["expires"]:
            return _cache["models"]

        config = _load_config()
        families = _get_family_patterns(config)

        # If no families configured, use static list
        if not families:
            logger.info("No model_families configured, using static available_models")
            return _static_fallback(config)

        # Try dynamic fetch
        raw_models = await fetch_openrouter_models()
        if raw_models is None:
            logger.info("OpenRouter fetch failed, using static fallback")
            return _static_fallback(config)

        classified = _classify_models(raw_models, families)
        if not classified:
            logger.warning("No models matched family patterns, using static fallback")
            return _static_fallback(config)

        # Update cache
        _cache["models"] = classified
        _cache["expires"] = now + CACHE_TTL_SECONDS
        logger.info("Refreshed model roster: %d models from OpenRouter", len(classified))
        return classified


def invalidate_cache() -> None:
    """Clear the model roster cache, forcing a refresh on next call."""
    _cache["models"] = None
    _cache["expires"] = 0.0


async def newest_in_family(family: str, exclude_variants: list) -> Optional[str]:
    """Newest anthropic/* model id in `family` by OpenRouter `created`, excluding
    any id containing an excluded variant token (e.g. 'fast', 'fable').

    Returns None on fetch failure or no match — callers fall through to
    pause-and-suggest rather than guessing.
    """
    raw = await fetch_openrouter_models()
    if not raw:
        return None
    family = family.lower()
    candidates = []
    for m in raw:
        mid = (m.get("id") or "").lower()
        if not mid.startswith("anthropic/") or family not in mid:
            continue
        if any(v.lower() in mid for v in exclude_variants):
            continue
        candidates.append((m.get("created") or 0, m["id"]))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1]
