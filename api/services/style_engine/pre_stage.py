"""Pipeline pre-generation stage: per-job deterministic data + prompt section.

Pure stdlib + style_engine internals -- no worker/DB/async/FastAPI imports.
Computes the per-job data a phase's prompt needs (proper nouns, character
budgets, transcript-verified keyword candidates, program formula) and
renders it as a markdown "Style Rules (authoritative)" section that gets
injected into the agent prompt ahead of generation. Only the ``seo`` phase
has behavior today; every other phase name degrades gracefully to an empty
result so later tasks can register formatter/timestamp/etc. behind the same
interface without touching this module's call sites.

Every value quoted in the rendered prompt section is read from ``rules`` (or
computed from ``context``) at call time -- nothing here hard-codes a limit
number or a forbidden-phrase list, so the section always reflects whatever
``config/house_style.yaml`` (or a caller's synthetic ``StyleRules``) says.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from api.services.style_engine.entities import extract_proper_nouns
from api.services.style_engine.rules import StyleRules
from api.services.style_engine.types import PreStageResult

# Human-friendly labels for forbidden_phrases categories seen in
# config/house_style.yaml. Any category not listed here falls back to a
# generic underscore->space transform (_category_label), so a synthetic
# test's made-up category names still render sensibly.
_CATEGORY_LABELS: dict[str, str] = {
    "viewer_directive": "viewer directives",
    "promise": "promises",
    "cta": "calls to action",
    "superlative": "unevidenced superlatives",
    "sales": "sales language",
    "emotional_prediction": "emotional predictions",
    "first_person_promo": "first-person promotional language",
}

# Human-friendly labels for programs.<name>.verbs role keys (house_style.yaml
# only defines "elected" / "non_elected" today). Unknown keys fall back to a
# generic underscore->space transform.
_ROLE_LABELS: dict[str, str] = {
    "elected": "elected officials/candidates",
    "non_elected": "non-elected",
}

_MAX_EXAMPLES_PER_CATEGORY = 3


def run_pre_stage(phase: str, context: Mapping[str, Any], rules: StyleRules) -> PreStageResult:
    """Compute per-job deterministic data for ``phase`` and render its prompt section.

    ``context`` keys read (all optional; every absence degrades gracefully):
      analyst_output -- analyst phase markdown (source of proper nouns)
      transcript -- source transcript text
      content_type -- "full" | "short" (default "full")
      program -- program name if known (e.g. "Here & Now")

    For ``phase == "seo"`` this computes ``proper_nouns``, ``char_budgets``,
    ``keyword_candidates``, and ``program_rules`` and renders them into a
    "## Style Rules (authoritative)" markdown section. For any other phase
    (v1), returns ``PreStageResult(phase=phase, prompt_section="", data={})``.
    """
    if phase != "seo":
        return PreStageResult(phase=phase, prompt_section="", data={})

    analyst_output = context.get("analyst_output") or ""
    transcript = context.get("transcript") or ""
    content_type = context.get("content_type") or "full"
    program = context.get("program")

    proper_nouns = extract_proper_nouns(analyst_output, rules.surname_stoplist())

    limits = rules.limits_for(program, content_type)
    char_budgets = {
        field_name: limit["max"]
        for field_name, limit in limits.items()
        if isinstance(limit, dict) and "max" in limit
    }

    transcript_lower = transcript.lower()
    keyword_candidates = [noun for noun in proper_nouns if noun.lower() in transcript_lower]

    program_rules = rules.program_rules(program)

    data = {
        "proper_nouns": proper_nouns,
        "char_budgets": char_budgets,
        "keyword_candidates": keyword_candidates,
        "program_rules": program_rules,
    }

    prompt_section = _render_seo_prompt_section(data, rules, program)

    return PreStageResult(phase=phase, prompt_section=prompt_section, data=data)


def _render_seo_prompt_section(data: dict, rules: StyleRules, program: str | None) -> str:
    lines = [
        "## Style Rules (authoritative)",
        "",
        "These values are computed and enforced by the pipeline — they override "
        "anything else in this prompt.",
        "",
        f"**Character limits (hard):** {_render_char_budgets(data['char_budgets'])}",
        f"**Proper nouns (authoritative spellings, use exactly):** {_render_list(data['proper_nouns'])}",
        f"**Keyword candidates verified present in the transcript:** {_render_list(data['keyword_candidates'])}",
        f"**Voice:** {_render_voice(rules)}",
        "**Titles and descriptions use down style:** capitalize only the first "
        "word and proper nouns.",
    ]

    program_block = _render_program_block(program, data["program_rules"])
    if program_block:
        lines.append(program_block)

    return "\n".join(lines) + "\n"


def _render_list(items: list[str]) -> str:
    return ", ".join(items) if items else "(none)"


def _render_char_budgets(budgets: dict[str, int]) -> str:
    if not budgets:
        return "(none)"
    parts = [f"{field_name.replace('_', ' ')} ≤ {max_len}" for field_name, max_len in budgets.items()]
    return ", ".join(parts)


def _category_label(category: str) -> str:
    return _CATEGORY_LABELS.get(category, category.replace("_", " "))


def _strip_regex_boundary(pattern: str) -> str:
    """Strip a leading/trailing ``\\b`` word-boundary anchor for display."""
    text = pattern
    if text.startswith(r"\b"):
        text = text[2:]
    if text.endswith(r"\b"):
        text = text[:-2]
    return text


def _render_voice(rules: StyleRules) -> str:
    grouped: dict[str, list[str]] = {}
    for entry in rules.forbidden():
        category = entry.get("category", "general")
        match = entry.get("match", "")
        if not match:
            continue
        # Regex entries (e.g. "\bfree\b") are authored for matching, not
        # display -- strip the word-boundary anchors so the prompt shows a
        # readable phrase instead of raw regex syntax.
        display = _strip_regex_boundary(match) if entry.get("regex") else match
        examples = grouped.setdefault(category, [])
        if display not in examples and len(examples) < _MAX_EXAMPLES_PER_CATEGORY:
            examples.append(display)

    category_bits = [
        f"{_category_label(category)} ({', '.join(examples)})"
        for category, examples in grouped.items()
        if examples
    ]
    never_use = ", ".join(category_bits) if category_bits else "(none)"

    fp_examples = [_strip_regex_boundary(marker) for marker in rules.first_person_markers()]
    fp_text = ", ".join(fp_examples) if fp_examples else "(none)"

    return f"third person, descriptive. Never use: {never_use}. No first-person ({fp_text})."


def _render_program_block(program: str | None, program_rules: dict) -> str:
    if not program or not program_rules:
        return ""

    header = f"**Program formula ({program}):**"

    formula_bits = []
    title_format = program_rules.get("title_format")
    if title_format:
        formula_bits.append(f'title = "{title_format}"')
    short_formula = program_rules.get("short_description_formula")
    if short_formula:
        formula_bits.append(f'short description = "{short_formula}"')
    long_formula = program_rules.get("long_description_formula")
    if long_formula:
        formula_bits.append(f'long description = "{long_formula}"')

    verb_bits = []
    verbs = program_rules.get("verbs") or {}
    for role_key, verb_list in verbs.items():
        if not verb_list:
            continue
        verb_str = "/".join(verb_list)
        role_label = _ROLE_LABELS.get(role_key, role_key.replace("_", " "))
        verb_bits.append(f'use "{verb_str}" for {role_label}')

    if not formula_bits and not verb_bits:
        # Program has rule data but none of the recognized formula/verb
        # keys -- fall back to a generic rendering of the raw entries so
        # the block still surfaces something instead of going silent.
        generic = "; ".join(f"{key.replace('_', ' ')}: {value}" for key, value in program_rules.items())
        return f"{header} {generic}"

    line = header
    if formula_bits:
        line += " " + "; ".join(formula_bits) + "."
    if verb_bits:
        line += " — " + "; ".join(verb_bits) + "."
    return line
