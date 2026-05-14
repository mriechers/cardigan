"""Cost estimation service for Cardigan.

Estimates job processing cost based on transcript size,
configured models, and per-phase output multipliers.
"""

from typing import Any, Dict, List, Optional

from api.services.llm import MODEL_PRICING

# Approximate tokens-per-word ratio (English text averages ~1.33 tokens/word)
TOKENS_PER_WORD = 1.33

# Per-phase multipliers: how many output tokens relative to input tokens.
# Based on observed production data:
# - Analyst: produces ~0.25x input (summary + analysis)
# - Formatter: produces ~0.9x input (reformats full transcript)
# - SEO: produces ~0.15x input (short metadata)
# - Validator: produces ~0.05x input (brief pass/fail report)
# - Timestamp: produces ~0.1x input (chapter markers)
PHASE_OUTPUT_MULTIPLIERS = {
    "analyst": 0.25,
    "formatter": 0.90,
    "seo": 0.15,
    "validator": 0.05,
    "timestamp": 0.10,
    "copy_editor": 0.90,
}

# Conservative fallback pricing (per 1M tokens) for unknown models
FALLBACK_PRICING = {"input": 1.0, "output": 3.0}


def _get_pricing(model_id: str, pricing_overrides: Optional[Dict[str, Dict[str, float]]] = None) -> Dict[str, float]:
    """Get per-1M-token pricing for a model.

    Checks overrides first, then MODEL_PRICING, then falls back to conservative estimate.
    """
    if pricing_overrides and model_id in pricing_overrides:
        return pricing_overrides[model_id]
    return MODEL_PRICING.get(model_id, FALLBACK_PRICING)


def estimate_job_cost(
    word_count: int,
    phase_models: Dict[str, str],
    pricing_overrides: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """Estimate the total cost for processing a transcript.

    Args:
        word_count: Number of words in the transcript
        phase_models: Dict mapping phase name to model ID
        pricing_overrides: Optional dict of {model_id: {"input": float, "output": float}}
            for pricing not in MODEL_PRICING (e.g., from OpenRouter roster)

    Returns:
        Dict with total_estimated_cost and per-phase breakdown
    """
    if word_count == 0:
        return {
            "total_estimated_cost": 0,
            "estimated_input_tokens": 0,
            "phase_estimates": [],
        }

    base_input_tokens = int(word_count * TOKENS_PER_WORD)
    total_cost = 0.0
    phase_estimates: List[Dict[str, Any]] = []

    for phase, model_id in phase_models.items():
        output_multiplier = PHASE_OUTPUT_MULTIPLIERS.get(phase, 0.25)
        est_input = base_input_tokens
        est_output = int(base_input_tokens * output_multiplier)

        pricing = _get_pricing(model_id, pricing_overrides)
        input_cost = (est_input / 1_000_000) * pricing["input"]
        output_cost = (est_output / 1_000_000) * pricing["output"]
        phase_cost = input_cost + output_cost

        phase_estimates.append({
            "phase": phase,
            "model": model_id,
            "estimated_input_tokens": est_input,
            "estimated_output_tokens": est_output,
            "estimated_cost": round(phase_cost, 6),
        })

        total_cost += phase_cost

    return {
        "total_estimated_cost": round(total_cost, 4),
        "estimated_input_tokens": base_input_tokens,
        "phase_estimates": phase_estimates,
    }
