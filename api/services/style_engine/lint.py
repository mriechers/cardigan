"""Deterministic validator checklist -- the code replacement for most of the
LLM validator's mechanical checks (``prompts/validator.md``).

Pure stdlib + style_engine internals -- no worker/DB/async/FastAPI imports.
``run_lint`` re-runs the validator's "output missing", "placeholder text",
"character limits", "review notes in body", "speaker label consistency",
"content past duration", and "truncation suspect" checks over the phase
outputs already sitting in the job's ``context`` bus, producing the exact
same ``RuleViolation``/``PhaseCheckResult`` shapes the rest of style_engine
uses. Detection only -- nothing here rewrites a phase's output.

Every numeric limit is read from ``StyleRules`` (via
``limits.check_field_limits``/``rules.limits_for``) at call time, never
hard-coded, so this module tracks whatever ``config/house_style.yaml`` (or a
caller's synthetic ``StyleRules``) says even though the character counts
quoted in ``prompts/validator.md`` itself are stale.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from api.services.style_engine.limits import check_field_limits
from api.services.style_engine.phase_io import extract_seo_fields
from api.services.style_engine.rules import StyleRules
from api.services.style_engine.types import PhaseCheckResult, RuleViolation

# The validator's contract covers exactly these three required phases --
# "timestamp" is optional/out of scope for v1 (see qa_merge.py's docstring).
_CANONICAL_PHASES = ("analyst", "formatter", "seo")

# A phase output shorter than this (after stripping HTML comments and all
# whitespace) is treated as functionally missing -- matches the validator's
# "missing/empty output -> automatic fail" rule (prompts/validator.md #6).
_MIN_SUBSTANTIVE_CHARS = 50

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# Literal template artifacts that mean the model echoed prompt scaffolding
# instead of real content. Deliberately literal/case-sensitive -- these are
# exact placeholder tokens quoted in the phase prompts, not general English.
_PLACEHOLDER_LITERALS = ("{media_id}", "{TODAY", "[INSERT", "{model name")

# A "**Recommended:**" line (seo.md's field template) whose captured value is
# empty, or is itself still a bracketed placeholder like
# "[55-60 character...]", means the model never filled in the field. Single
# ``\n`` (not ``\n+``) so a genuinely blank value line is captured as "" --
# consuming multiple newlines would skip past it to the next non-blank line.
_RECOMMENDED_VALUE_RE = re.compile(r"\*\*Recommended:\*\*[ \t]*\n([^\n]*)")
_BRACKET_PLACEHOLDER_RE = re.compile(r"^\[.*\]$")

# seo_output.md's "### YouTube Tags (15-20 recommended)" section is the only
# keyword/tag block with a well-defined item count -- the comma-separated
# list lives in the fenced code block immediately under the heading.
_YOUTUBE_TAGS_RE = re.compile(r"###\s*YouTube Tags.*?```[ \t]*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)

# limits.check_field_limits rule_ids -> this module's lint.* rule_ids. Reused
# rather than duplicated: check_field_limits already reads its bounds from
# StyleRules.limits_for(), so this mapping is the only seo-limits logic here.
_LIMIT_RULE_ID_MAP = {
    "limits.title.max": "lint.seo.title_over_limit",
    "limits.short_description.max": "lint.seo.short_over_limit",
    "limits.long_description.max": "lint.seo.long_over_limit",
    "limits.keywords.count": "lint.seo.keywords_count",
}

_HR_LINE_RE = re.compile(r"^-{3,}[ \t]*$", re.MULTILINE)
_REVIEW_NOTE_MARKER_RE = re.compile(r"<!--\s*review|NEEDS_REVIEW|^##\s*Review Notes", re.IGNORECASE | re.MULTILINE)

_HONORIFIC_RE = re.compile(r"^(?:Dr|Mr|Ms|Mrs|Prof)\.?\s", re.IGNORECASE)

# Loose "**Name:**" bold-colon speaker-label shape used to COLLECT candidate
# labels for the speaker_label_inconsistent check. Deliberately more
# permissive than any configured house_style.yaml speaker_label.pattern
# (which typically requires 2+ words) -- collection and validation are
# separate concerns. If candidates were collected with the strict configured
# pattern, a malformed single-word label like "**Sarah:**" would never enter
# the pool and the single-word check below could never fire. The configured
# pattern remains the definition of a *conforming* label; this pattern's job
# is only to find everything that looks label-shaped so it can be judged.
#
# The continuation group accepts either another capitalized word OR a bare
# number token -- this is what lets generic numbered labels like
# "**Speaker 1:**" / "**Reporter 2:**" enter the candidate pool at all
# (previously invisible to collection since "1"/"2" don't match [A-Z]).
# Collecting them is for superset detection and future canon checks, not to
# flag them as malformed: a "Speaker 1" candidate has 2 tokens and no
# honorific, so it correctly passes the single-word/honorific checks
# silently -- generic labels are legitimate per analyst rules.
_LOOSE_SPEAKER_LABEL_RE = re.compile(r"^\*\*[A-Z][\w.'-]*(?:\s(?:[A-Z][\w.'-]*|\d+))*:\*\*", re.MULTILINE)

# Known non-name field labels that share the loose "**Word:**" bold-colon
# shape (e.g. "**Note:** inline annotation") but are never speaker labels.
# Checked case-insensitively against a candidate's FIRST token only, so a
# label like "**Notes on Sarah:**" is still skipped (it is not a name) while
# a genuine name never collides (no legitimate speaker is named "Note").
# Candidates matching this stoplist are skipped at COLLECTION time, before
# the single-word/honorific/superset checks ever see them.
_FIELD_LABEL_STOPLIST = frozenset(
    {"note", "notes", "status", "warning", "important", "update", "correction", "source", "example"}
)

# (MM:SS) or (H:MM:SS) timecode markers, e.g. "(12:34)" / "(1:02:03)".
_TIMECODE_RE = re.compile(r"\((\d{1,2}(?::\d{2}){1,2})\)")
_DURATION_SLACK_SECONDS = 60

_TERMINAL_PUNCT = (".", "?", "!", '"', "'", "”", "’")
_STATUS_FOOTER_RE = re.compile(r"^\*\*Status:\*\*", re.IGNORECASE)

# Minimum fraction of the stated episode duration the LAST parsed
# (MM:SS)/(H:MM:SS) timecode marker in the formatter body must reach before
# truncation_suspect's coverage-vs-duration path fires. Overridable per-call
# via _check_truncation_suspect's ``coverage_floor`` keyword arg -- same
# module-constant-default pattern as
# api.services.completeness.DEFAULT_COVERAGE_THRESHOLD.
DEFAULT_TRUNCATION_COVERAGE_FLOOR = 0.85


def run_lint(context: Mapping[str, Any], rules: StyleRules) -> dict[str, PhaseCheckResult]:
    """Deterministic validator checklist over the phase outputs in ``context``.

    ``context`` keys read (all optional): ``analyst_output``,
    ``formatter_output``, ``seo_output`` (the worker's per-phase output bus),
    plus ``transcript``, ``transcript_file``, ``duration_minutes``,
    ``content_type``, ``program``.

    Always returns exactly one entry per canonical phase (``analyst``,
    ``formatter``, ``seo``) -- a phase whose ``<phase>_output`` key is absent,
    ``None``, or effectively empty (comment-only/whitespace-only, under 50
    substantive characters) gets a ``PhaseCheckResult`` carrying only the
    ``lint.output_missing`` violation, since the validator's contract is
    "missing/empty output -> auto-fail" and there is no real content left to
    run the other checks against.
    """
    results: dict[str, PhaseCheckResult] = {}

    for phase in _CANONICAL_PHASES:
        raw_output = context.get(f"{phase}_output")

        if _substantive_length(raw_output) < _MIN_SUBSTANTIVE_CHARS:
            results[phase] = PhaseCheckResult(
                phase=phase,
                violations=[
                    RuleViolation(
                        rule_id="lint.output_missing",
                        phase=phase,
                        severity="error",
                        message=f"{phase} output is missing or has fewer than "
                        f"{_MIN_SUBSTANTIVE_CHARS} characters of substantive content",
                        model_fixable=False,
                    )
                ],
            )
            continue

        violations: list[RuleViolation] = list(_check_placeholder_text(raw_output, phase))

        if phase == "seo":
            violations += _check_seo_limits(raw_output, context, rules, phase)
        elif phase == "formatter":
            violations += _check_review_notes_in_body(raw_output, rules, phase)
            violations += _check_speaker_label_inconsistent(raw_output, rules, phase)
            violations += _check_content_past_duration(raw_output, context, phase)
            violations += _check_truncation_suspect(raw_output, context, phase)

        results[phase] = PhaseCheckResult(phase=phase, violations=violations)

    return results


# ---------------------------------------------------------------------------
# lint.output_missing helper
# ---------------------------------------------------------------------------


def _substantive_length(raw_output: str | None) -> int:
    """Length of ``raw_output`` with HTML comments and all whitespace removed."""
    text = raw_output or ""
    text = _HTML_COMMENT_RE.sub("", text)
    return len(re.sub(r"\s+", "", text))


# ---------------------------------------------------------------------------
# lint.placeholder_text -- all 3 phases
# ---------------------------------------------------------------------------


def _check_placeholder_text(raw_output: str, phase: str) -> list[RuleViolation]:
    violations: list[RuleViolation] = []

    for literal in _PLACEHOLDER_LITERALS:
        if literal in raw_output:
            violations.append(
                RuleViolation(
                    rule_id="lint.placeholder_text",
                    phase=phase,
                    severity="error",
                    message=f'Template placeholder "{literal}" found in {phase} output',
                    model_fixable=True,
                )
            )

    for match in _RECOMMENDED_VALUE_RE.finditer(raw_output):
        value = match.group(1).strip()
        if value == "" or _BRACKET_PLACEHOLDER_RE.match(value):
            shown = value if value else "(empty)"
            violations.append(
                RuleViolation(
                    rule_id="lint.placeholder_text",
                    phase=phase,
                    severity="error",
                    message=f'"**Recommended:**" line has an unfilled placeholder value: {shown}',
                    model_fixable=True,
                )
            )

    return violations


# ---------------------------------------------------------------------------
# lint.seo.title_over_limit / short_over_limit / long_over_limit /
# keywords_count -- seo phase only
# ---------------------------------------------------------------------------


def _check_seo_limits(
    raw_output: str, context: Mapping[str, Any], rules: StyleRules, phase: str
) -> list[RuleViolation]:
    fields = extract_seo_fields(raw_output)
    values: dict[str, str | list[str]] = {}
    if fields.title is not None:
        values["title"] = fields.title.value
    if fields.short_description is not None:
        values["short_description"] = fields.short_description.value
    if fields.long_description is not None:
        values["long_description"] = fields.long_description.value

    keywords = _extract_keyword_tags(raw_output)
    if keywords is not None:
        values["keywords"] = keywords

    program = context.get("program")
    content_type = context.get("content_type") or "full"

    raw_violations = check_field_limits(values, rules, phase, program=program, content_type=content_type)

    return [replace(v, rule_id=_LIMIT_RULE_ID_MAP.get(v.rule_id, v.rule_id)) for v in raw_violations]


def _extract_keyword_tags(seo_output: str) -> list[str] | None:
    """Comma-separated items from the "### YouTube Tags" fenced block, if present.

    Returns ``None`` when the section can't be identified -- callers must
    skip the keywords-count check silently in that case, never guess.
    """
    match = _YOUTUBE_TAGS_RE.search(seo_output)
    if not match:
        return None
    items = [item.strip() for item in match.group(1).split(",")]
    items = [item for item in items if item]
    return items or None


# ---------------------------------------------------------------------------
# lint.formatter.review_notes_in_body -- formatter phase only
# ---------------------------------------------------------------------------


def _check_review_notes_in_body(raw_output: str, rules: StyleRules, phase: str) -> list[RuleViolation]:
    review_notes_cfg = _phase_cfg(rules, "formatter").get("review_notes") or {}
    if review_notes_cfg.get("placement") != "top":
        return []

    hr_match = _HR_LINE_RE.search(raw_output)
    if not hr_match:
        return []

    after_first_rule = raw_output[hr_match.end() :]
    marker_match = _REVIEW_NOTE_MARKER_RE.search(after_first_rule)
    if not marker_match:
        return []

    return [
        RuleViolation(
            rule_id="lint.formatter.review_notes_in_body",
            phase=phase,
            severity="error",
            message=(
                f'Review-note marker "{marker_match.group(0).strip()}" appears after the first '
                "horizontal rule -- review notes must sit at the top of the document"
            ),
            model_fixable=False,
        )
    ]


# ---------------------------------------------------------------------------
# lint.formatter.speaker_label_inconsistent -- formatter phase only
# ---------------------------------------------------------------------------


def _check_speaker_label_inconsistent(raw_output: str, rules: StyleRules, phase: str) -> list[RuleViolation]:
    speaker_label_cfg = _phase_cfg(rules, "formatter").get("speaker_label") or {}
    if not speaker_label_cfg.get("pattern"):
        return []
    no_honorifics = bool(speaker_label_cfg.get("no_honorifics"))

    labels: list[str] = []
    seen: set[str] = set()
    for match in _LOOSE_SPEAKER_LABEL_RE.finditer(_body_region(raw_output)):
        name = _label_text(match.group(0))
        if not name or name in seen:
            continue
        if name.split()[0].lower() in _FIELD_LABEL_STOPLIST:
            continue
        seen.add(name)
        labels.append(name)

    violations: list[RuleViolation] = []

    for name in labels:
        if len(name.split()) == 1:
            violations.append(_speaker_violation(phase, f'Speaker label "{name}" is a single word (expected first + last name)'))
        if no_honorifics and _HONORIFIC_RE.match(name):
            violations.append(
                _speaker_violation(phase, f'Speaker label "{name}" carries an honorific (house style: names only)')
            )

    for i, a in enumerate(labels):
        a_words = set(a.split())
        for b in labels[i + 1 :]:
            b_words = set(b.split())
            if a_words == b_words or not (a_words < b_words or b_words < a_words):
                continue
            shorter, longer = (a, b) if a_words < b_words else (b, a)
            violations.append(
                _speaker_violation(
                    phase,
                    f'Speaker labels "{shorter}" and "{longer}" look like the same person labeled inconsistently',
                )
            )

    return violations


def _speaker_violation(phase: str, message: str) -> RuleViolation:
    return RuleViolation(
        rule_id="lint.formatter.speaker_label_inconsistent",
        phase=phase,
        severity="warning",
        message=message,
        model_fixable=True,
    )


def _label_text(matched: str) -> str:
    """Strip a matched "**Name:**"-shaped label down to the bare name."""
    text = re.sub(r"^\*+", "", matched)
    text = re.sub(r":?\*+$", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# lint.formatter.content_past_duration -- formatter phase only
# ---------------------------------------------------------------------------


def _check_content_past_duration(raw_output: str, context: Mapping[str, Any], phase: str) -> list[RuleViolation]:
    duration_minutes = context.get("duration_minutes")
    if not duration_minutes:
        return []

    limit_seconds = duration_minutes * 60 + _DURATION_SLACK_SECONDS

    violations: list[RuleViolation] = []
    seen_markers: set[str] = set()
    for match in _TIMECODE_RE.finditer(raw_output):
        marker = match.group(0)
        if marker in seen_markers:
            continue
        seconds = _parse_timecode_seconds(match.group(1))
        if seconds > limit_seconds:
            seen_markers.add(marker)
            violations.append(
                RuleViolation(
                    rule_id="lint.formatter.content_past_duration",
                    phase=phase,
                    severity="warning",
                    message=(
                        f"Timecode marker {marker} exceeds the content duration "
                        f"({duration_minutes} min) plus {_DURATION_SLACK_SECONDS}s slack"
                    ),
                    model_fixable=False,
                )
            )

    return violations


def _parse_timecode_seconds(text: str) -> int:
    parts = [int(p) for p in text.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    hours, minutes, seconds = parts
    return hours * 3600 + minutes * 60 + seconds


# ---------------------------------------------------------------------------
# lint.formatter.truncation_suspect -- formatter phase only
# ---------------------------------------------------------------------------


def _check_truncation_suspect(
    raw_output: str,
    context: Mapping[str, Any],
    phase: str,
    coverage_floor: float = DEFAULT_TRUNCATION_COVERAGE_FLOOR,
) -> list[RuleViolation]:
    """Two independent detection paths, same rule_id/severity/model_fixable.

    1. Last-line punctuation (original): the last visible prose line lacks
       terminal punctuation -- a same-document mid-sentence cutoff.
    2. Coverage-vs-duration (new): the LAST parsed timecode marker in the
       body falls short of ``coverage_floor`` of the stated episode
       duration -- catches the dominant real-world gap the punctuation path
       cannot see at all: content that stops well short of the full episode
       while still closing with a clean, fully-punctuated sign-off
       paragraph (measured at a 0% hit rate across 21 production jobs in
       the Stage-2 agreement study despite 8 real truncations in that
       sample). Silently skipped when duration_minutes is missing/zero or
       no timecode marker is present in the body -- never guesses.
    """
    violations: list[RuleViolation] = []
    violations += _check_truncation_punctuation(raw_output, phase)
    violations += _check_truncation_coverage(raw_output, context, phase, coverage_floor)
    return violations


def _check_truncation_punctuation(raw_output: str, phase: str) -> list[RuleViolation]:
    text = _HTML_COMMENT_RE.sub("", raw_output)

    last_prose: str | None = None
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if _STATUS_FOOTER_RE.match(stripped):
            continue
        if re.fullmatch(r"-{3,}", stripped):
            continue
        last_prose = stripped

    if last_prose is None or last_prose[-1] in _TERMINAL_PUNCT:
        return []

    excerpt = last_prose if len(last_prose) <= 60 else f"...{last_prose[-60:]}"
    return [
        RuleViolation(
            rule_id="lint.formatter.truncation_suspect",
            phase=phase,
            severity="warning",
            message=f'Last line lacks terminal punctuation, possible mid-sentence cutoff: "{excerpt}"',
            model_fixable=False,
        )
    ]


def _check_truncation_coverage(
    raw_output: str, context: Mapping[str, Any], phase: str, coverage_floor: float
) -> list[RuleViolation]:
    duration_minutes = context.get("duration_minutes")
    if not duration_minutes:
        return []

    # HTML comments (review notes, provenance headers) are stripped before
    # scanning -- a timecode mentioned in passing inside freeform review-note
    # prose (e.g. "the Hendrickson paragraph (00:07:44)") says nothing about
    # how much of the actual transcript content is covered, and treating it
    # as a coverage marker produces both false negatives (a genuine cutoff
    # note that isn't the LAST such mention gets shadowed) and false
    # positives (an unrelated aside coincidentally reads as a low-coverage
    # marker). Mirrors _check_truncation_punctuation's HTML-comment handling
    # of the same raw_output.
    body_text = _HTML_COMMENT_RE.sub("", raw_output)
    markers = list(_TIMECODE_RE.finditer(body_text))
    if not markers:
        return []

    last_marker = markers[-1]
    last_seconds = _parse_timecode_seconds(last_marker.group(1))
    duration_seconds = duration_minutes * 60
    coverage_ratio = last_seconds / duration_seconds if duration_seconds else 0.0

    if coverage_ratio >= coverage_floor:
        return []

    duration_display = _format_seconds_as_timecode(duration_seconds)
    return [
        RuleViolation(
            rule_id="lint.formatter.truncation_suspect",
            phase=phase,
            severity="warning",
            message=(
                f"Last timecode marker {last_marker.group(0)} covers only {coverage_ratio:.1%} of the "
                f"{duration_display} content duration (coverage floor {coverage_floor:.0%}) -- possible truncation"
            ),
            model_fixable=False,
        )
    ]


def _format_seconds_as_timecode(total_seconds: float) -> str:
    """Render a seconds count as ``H:MM:SS`` (or ``M:SS`` under an hour)."""
    whole_seconds = int(round(total_seconds))
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _body_region(text: str) -> str:
    """Best-effort slice between the first and last horizontal-rule lines.

    The formatter document template is header / ``---`` / body (speaker
    turns, optionally review notes) / ``---`` / ``**Status:**`` footer. The
    header and footer both use the same "**Field:**" bold-colon markdown as
    real speaker labels (``**Project:**``, ``**Status:**``, ...), so speaker
    -label collection is scoped to strictly between the two rules when both
    are present -- otherwise those metadata fields would be mistaken for
    (single-word) speaker labels. Falls back to the full text when fewer
    than two horizontal rules are found.
    """
    matches = list(_HR_LINE_RE.finditer(text))
    if len(matches) >= 2:
        return text[matches[0].end() : matches[-1].start()]
    return text


def _phase_cfg(rules: StyleRules, phase: str) -> dict:
    """Read-only access to ``rules.raw["phases"][phase]``.

    ``rules.raw`` is the mtime-cached document shared by every caller of
    ``load_rules`` for this path -- the returned mapping (and anything nested
    in it) must never be mutated, only read.
    """
    phases = rules.raw.get("phases") or {}
    return phases.get(phase) or {}
