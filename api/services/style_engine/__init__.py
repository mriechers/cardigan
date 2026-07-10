"""style_engine — pure deterministic house-style rule engine.

Foundation package for the Cardigan hybrid deterministic-LLM pipeline. This
package holds the YAML house-style rule loader (rules.py) and the shared
result/violation dataclasses (types.py) that later pipeline-stage tasks
(scanner, limits checker, prompt renderer, pre/post pipeline stages) build
on. Nothing here touches the DB, async code, or FastAPI.
"""

from api.services.style_engine.rules import (
    DEFAULT_RULES_PATH,
    StyleRules,
    StyleRulesError,
    load_rules,
)
from api.services.style_engine.types import (
    AppliedFix,
    PhaseCheckResult,
    PostStageResult,
    PreStageResult,
    RuleViolation,
)

__all__ = [
    "DEFAULT_RULES_PATH",
    "StyleRules",
    "StyleRulesError",
    "load_rules",
    "AppliedFix",
    "PhaseCheckResult",
    "PostStageResult",
    "PreStageResult",
    "RuleViolation",
]
