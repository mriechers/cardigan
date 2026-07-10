"""style_engine — pure deterministic house-style rule engine.

Foundation package for the Cardigan hybrid deterministic-LLM pipeline. This
package holds the YAML house-style rule loader (rules.py), the shared
result/violation dataclasses (types.py), the prompt-block renderer
(prompt_blocks.py), the engine primitives (casing.py, entities.py,
scanner.py, limits.py, phase_io.py), and the pure pipeline-stage modules
(pre_stage.py, post_stage.py) that the job worker (a later task) wires
into each phase's generation call. Nothing here touches the DB, async
code, or FastAPI.
"""

from api.services.style_engine.casing import build_canonical, to_down_style
from api.services.style_engine.entities import extract_proper_nouns
from api.services.style_engine.limits import check_field_limits
from api.services.style_engine.phase_io import (
    FieldSpan,
    SeoFields,
    extract_seo_fields,
    splice_seo_fields,
)
from api.services.style_engine.post_stage import run_post_stage
from api.services.style_engine.pre_stage import run_pre_stage
from api.services.style_engine.prompt_blocks import (
    PromptBlockError,
    render_prompt_blocks,
    resolve_prompt_profile,
)
from api.services.style_engine.rules import (
    DEFAULT_RULES_PATH,
    StyleRules,
    StyleRulesError,
    load_rules,
)
from api.services.style_engine.scanner import scan_forbidden, scan_person_voice
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
    "PromptBlockError",
    "render_prompt_blocks",
    "resolve_prompt_profile",
    "AppliedFix",
    "PhaseCheckResult",
    "PostStageResult",
    "PreStageResult",
    "RuleViolation",
    "build_canonical",
    "to_down_style",
    "extract_proper_nouns",
    "scan_forbidden",
    "scan_person_voice",
    "check_field_limits",
    "FieldSpan",
    "SeoFields",
    "extract_seo_fields",
    "splice_seo_fields",
    "run_pre_stage",
    "run_post_stage",
]
