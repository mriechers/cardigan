"""style_engine — pure deterministic house-style rule engine.

Foundation package for the Cardigan hybrid deterministic-LLM pipeline. This
package holds the YAML house-style rule loader (rules.py), the shared
result/violation dataclasses (types.py), the prompt-block renderer
(prompt_blocks.py), the engine primitives (casing.py, entities.py,
scanner.py, limits.py, phase_io.py, substitutions.py), the timestamp
structured-contract timecode math (timecodes.py), the pure pipeline-stage
modules (pre_stage.py, post_stage.py), the deterministic validator checklist
(lint.py), the shared review-notes-placement check (review_notes.py), and
the QA-verdict merge (qa_merge.py) that the job worker wires into each
phase's generation call and the validator's QA gate. Nothing here touches
the DB, async code, or FastAPI.
"""

from api.services.style_engine.casing import build_canonical, to_down_style
from api.services.style_engine.entities import extract_proper_nouns
from api.services.style_engine.limits import check_field_limits
from api.services.style_engine.lint import run_lint
from api.services.style_engine.phase_io import (
    CHAPTER_LINE_RE,
    FieldSpan,
    SeoFields,
    emit_timestamp_report,
    extract_seo_fields,
    parse_chapter_list,
    splice_seo_fields,
)
from api.services.style_engine.post_stage import run_post_stage
from api.services.style_engine.pre_stage import run_pre_stage
from api.services.style_engine.prompt_blocks import (
    PromptBlockError,
    render_prompt_blocks,
    resolve_prompt_profile,
    strip_style_tokens,
    validate_prompt_blocks,
)
from api.services.style_engine.qa_merge import merge_style_flags
from api.services.style_engine.review_notes import check_review_notes_placement
from api.services.style_engine.rules import (
    DEFAULT_RULES_PATH,
    StyleRules,
    StyleRulesError,
    load_rules,
)
from api.services.style_engine.scanner import scan_forbidden, scan_person_voice
from api.services.style_engine.substitutions import (
    apply_substitutions,
    apply_substitutions_with_fixes,
    normalize_speaker_turns,
)
from api.services.style_engine.timecodes import (
    Chapter,
    emit_media_manager_table,
    emit_youtube_list,
    format_media_manager,
    format_youtube,
    parse_timecode_to_ms,
    snap_chapters,
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
    "PromptBlockError",
    "render_prompt_blocks",
    "resolve_prompt_profile",
    "strip_style_tokens",
    "validate_prompt_blocks",
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
    "apply_substitutions",
    "apply_substitutions_with_fixes",
    "normalize_speaker_turns",
    "check_review_notes_placement",
    "run_pre_stage",
    "run_post_stage",
    "run_lint",
    "merge_style_flags",
    "Chapter",
    "CHAPTER_LINE_RE",
    "parse_timecode_to_ms",
    "format_media_manager",
    "format_youtube",
    "snap_chapters",
    "emit_media_manager_table",
    "emit_youtube_list",
    "parse_chapter_list",
    "emit_timestamp_report",
]
