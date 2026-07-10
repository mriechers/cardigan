"""Pipeline pre-generation stage: per-job deterministic data + prompt section.

Pure stdlib + style_engine internals -- no worker/DB/async/FastAPI imports.
Computes the per-job data a phase's prompt needs (proper nouns, character
budgets, transcript-verified keyword candidates, program formula for
``seo``; a summary of already-run deterministic checks for ``validator``)
and renders it as a markdown prompt section injected into the agent prompt
ahead of generation.

Every value quoted in the rendered prompt section is read from ``rules`` (or
computed from ``context``) at call time -- nothing here hard-codes a limit
number or a forbidden-phrase list, so the section always reflects whatever
``config/house_style.yaml`` (or a caller's synthetic ``StyleRules``) says.

The ``seo``, ``validator``, and ``formatter`` phases have behavior today;
every other phase name degrades gracefully to an empty result so later tasks
can register timestamp/etc. behind the same interface without touching this
module's call sites.
"""

from __future__ import annotations

import re
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
      style_checks -- validator phase only; ``{"seo": PhaseCheckResult.to_dict(),
        ...}`` accumulated by the post-stages and ``lint.run_lint``

    For ``phase == "seo"`` this computes ``proper_nouns``, ``char_budgets``,
    ``keyword_candidates``, and ``program_rules`` and renders them into a
    "## Style Rules (authoritative)" markdown section.

    For ``phase == "validator"``, when ``context["style_checks"]`` is
    non-empty, renders a "## Deterministic checks already performed" section
    summarizing each phase's error/warning flag counts plus an instruction
    to skip re-checking mechanics and judge only the semantic checks listed
    in ``rules`` ``phases.validator.semantic_checks``. An empty/missing
    ``style_checks`` renders nothing (prompt unchanged).

    For ``phase == "formatter"``, computes ``proper_nouns`` (same source as
    ``seo``), the raw ``speaker_label_spec`` dict
    (``phases.formatter.speaker_label``), and ``enforce_substitutions``
    (``rules.substitutions(tier="enforce")``), and renders them into a
    "## Style Rules (authoritative)" section: the authoritative name list,
    the speaker-label format spec in prose, the enforce-tier pairs rendered
    as "write it right the first time" rules, and a review-notes placement
    line.

    For any other phase (v1), returns
    ``PreStageResult(phase=phase, prompt_section="", data={})``.
    """
    if phase == "validator":
        return _run_validator_pre_stage(context, rules)

    if phase == "formatter":
        return _run_formatter_pre_stage(context, rules)

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


# ---------------------------------------------------------------------------
# validator phase
# ---------------------------------------------------------------------------

_VALIDATOR_INSTRUCTION_PREFIX = (
    "Do NOT re-check character limits, casing, or format mechanics — they are "
    "handled deterministically. Judge ONLY: "
)
# Semantic checks beyond the data-driven title/description pair -- keyword
# relevance is inherently subjective (unlike the now-deterministic keyword
# *count* check in lint.py) so it stays a fixed instruction, not YAML data.
_VALIDATOR_INSTRUCTION_SUFFIX = "keywords relevant to content."


def _run_validator_pre_stage(context: Mapping[str, Any], rules: StyleRules) -> PreStageResult:
    style_checks = context.get("style_checks") or {}
    if not style_checks:
        return PreStageResult(phase="validator", prompt_section="", data={})

    summary = {
        phase_name: _phase_flag_counts(check) for phase_name, check in style_checks.items()
    }
    semantic_checks = list(
        (rules.raw.get("phases", {}) or {}).get("validator", {}).get("semantic_checks") or []
    )

    prompt_section = _render_validator_prompt_section(summary, semantic_checks)

    return PreStageResult(
        phase="validator",
        prompt_section=prompt_section,
        data={"style_checks_summary": summary},
    )


def _phase_flag_counts(check: Mapping[str, Any]) -> dict[str, int]:
    violations = (check or {}).get("violations") or []
    error_count = sum(1 for v in violations if v.get("severity") == "error")
    warning_count = sum(1 for v in violations if v.get("severity") == "warning")
    return {"error_count": error_count, "warning_count": warning_count}


def _render_validator_prompt_section(summary: dict[str, dict[str, int]], semantic_checks: list[str]) -> str:
    lines = ["## Deterministic checks already performed", ""]
    for phase_name, counts in summary.items():
        lines.append(
            f"- {phase_name}: {counts['error_count']} error flag(s), {counts['warning_count']} warning(s) "
            "— mechanical limits/casing already enforced/flagged by the pipeline"
        )
    lines.append("")
    lines.append(_render_semantic_instruction(semantic_checks))
    return "\n".join(lines) + "\n"


def _render_semantic_instruction(semantic_checks: list[str]) -> str:
    numbered = "; ".join(f"({i}) {check}" for i, check in enumerate(semantic_checks, start=1))
    body = f"{numbered}; {_VALIDATOR_INSTRUCTION_SUFFIX}" if numbered else _VALIDATOR_INSTRUCTION_SUFFIX
    return f"{_VALIDATOR_INSTRUCTION_PREFIX}{body}"


# ---------------------------------------------------------------------------
# formatter phase
# ---------------------------------------------------------------------------


def _run_formatter_pre_stage(context: Mapping[str, Any], rules: StyleRules) -> PreStageResult:
    analyst_output = context.get("analyst_output") or ""
    proper_nouns = extract_proper_nouns(analyst_output, rules.surname_stoplist())

    formatter_cfg = (rules.raw.get("phases", {}) or {}).get("formatter", {}) or {}
    speaker_label_spec = dict(formatter_cfg.get("speaker_label") or {})
    review_notes_cfg = formatter_cfg.get("review_notes") or {}
    enforce_substitutions = rules.substitutions(tier="enforce")

    data = {
        "proper_nouns": proper_nouns,
        "speaker_label_spec": speaker_label_spec,
        "enforce_substitutions": enforce_substitutions,
    }

    prompt_section = _render_formatter_prompt_section(data, review_notes_cfg)

    return PreStageResult(phase="formatter", prompt_section=prompt_section, data=data)


def _render_formatter_prompt_section(data: dict, review_notes_cfg: Mapping[str, Any]) -> str:
    lines = [
        "## Style Rules (authoritative)",
        "",
        "These values are computed and enforced by the pipeline — they override "
        "anything else in this prompt.",
        "",
    ]

    proper_nouns = data["proper_nouns"]
    if proper_nouns:
        lines.append(
            f"**Proper nouns (authoritative spellings — use exactly these):** {_render_list(proper_nouns)}"
        )

    speaker_label_line = _render_speaker_label_line(data["speaker_label_spec"])
    if speaker_label_line:
        lines.append(speaker_label_line)

    pairs_line = _render_substitution_pairs_line(data["enforce_substitutions"])
    if pairs_line:
        lines.append(pairs_line)

    review_notes_line = _render_review_notes_line(review_notes_cfg)
    if review_notes_line:
        lines.append(review_notes_line)

    return "\n".join(lines) + "\n"


def _render_speaker_label_line(spec: Mapping[str, Any]) -> str:
    if not spec.get("pattern"):
        return ""

    bits = ["**First Last:** — bold, first + last name, colon inside the bold"]

    trailing_spaces = spec.get("trailing_spaces")
    if trailing_spaces is not None:
        plural = "" if trailing_spaces == 1 else "s"
        bits.append(f"{trailing_spaces} trailing space{plural} after the colon")

    blank_lines = spec.get("blank_lines_between_turns")
    if blank_lines is not None:
        plural = "" if blank_lines == 1 else "s"
        bits.append(f"exactly {blank_lines} blank line{plural} between speaker turns")

    if spec.get("no_honorifics"):
        bits.append("no honorifics in labels")

    return f"**Speaker labels:** {'; '.join(bits)}."


_BACKREFERENCE_RE = re.compile(r"\\\d")


def _render_substitution_pairs_line(substitutions: list[dict]) -> str:
    pairs = []
    for sub in substitutions:
        find = sub.get("find")
        replace = sub.get("replace")
        if not find or replace is None:
            continue
        note = sub.get("note")

        if _BACKREFERENCE_RE.search(replace):
            # `replace` contains a regex backreference (e.g. "\1") -- it is
            # not a literal replacement string outside an actual match, so
            # it can't be shown via the normal "X (never Y)" template
            # (that would literally print "\1" into the prompt). Prefer the
            # entry's note (expected to describe the transform in prose);
            # fall back to a generic description built from the humanized
            # find when no note is present.
            pairs.append(note or f"apply consistent formatting to {_humanize_substitution_find(find, None)}")
            continue

        humanized_find = _humanize_substitution_find(find, note)
        pairs.append(f"{replace} (never {humanized_find})")

    if not pairs:
        return ""

    return f"**Write it right the first time:** {'; '.join(pairs)}."


def _humanize_substitution_find(find: str, note: str | None) -> str:
    cleaned = _strip_regex_boundary(find)
    cleaned = _CHAR_CLASS_RE.sub(lambda m: m.group(1).lower(), cleaned)
    if note and _REGEXY_RE.search(cleaned):
        return note
    return cleaned


def _render_review_notes_line(review_notes_cfg: Mapping[str, Any]) -> str:
    placement = review_notes_cfg.get("placement")
    if not placement:
        return ""
    fmt = review_notes_cfg.get("format")
    fmt_text = f", formatted as an {fmt.replace('_', ' ')}" if fmt else ""
    return f"**Review notes:** place at the {placement} of the document{fmt_text}."


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


# Collapses a two-letter case-alternation class like "[Oo]" down to its
# lowercase representative letter ("o") for display -- used by the
# formatter substitution-pairs renderer's best-effort cleanup.
_CHAR_CLASS_RE = re.compile(r"\[(\w)\w\]")

# Leftover regex metacharacters after boundary-stripping + char-class
# collapse mean best-effort cleanup couldn't produce a clean phrase (e.g. an
# alternation like "(Here & Now|Wisconsin Life)") -- signals the renderer to
# prefer an entry's ``note`` field, when present, over the raw pattern text.
_REGEXY_RE = re.compile(r"[()|\[\]\\]")


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
