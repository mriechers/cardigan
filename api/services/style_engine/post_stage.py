"""Pipeline post-generation stage: normalize (enforce tier), then validate (flag tier).

Pure stdlib + style_engine internals -- no worker/DB/async/FastAPI imports.
Runs after a phase's LLM call returns raw markdown. Two tiers, never
conflated:

- **Enforce tier** -- deterministic rewrites that never change meaning. For
  ``seo`` this is down-styling the TITLE field's casing and truncating an
  over-limit ``short_description``/``long_description`` to its hard character
  budget at a word boundary (both spliced back into the document via their
  exact spans). No other field or text is rewritten here.
- **Flag tier** -- everything else (over-limit text, forbidden phrases,
  first/second-person voice) is detection-only. Violations are surfaced as
  ``RuleViolation``s for a human or a later model-fixable pass; this module
  never rewrites or drops the offending text.

For ``formatter``, enforce tier is narrower still than ``seo``'s: it is
LIMITED to :func:`substitutions.apply_substitutions` (word-boundary lexical
find/replace pairs, guarded against code fences/URLs/sentence-initial
case-folds) and :func:`substitutions.normalize_speaker_turns`
(whitespace-only). There is deliberately no down-style pass over dialogue
and no line dropping -- formatter output is a transcript, not metadata copy,
and "the enforcer must never eat content" is this phase's acceptance gate.
A word-count guard (:data:`_GUARD_MIN_RATIO`/:data:`_GUARD_MAX_RATIO`,
comparing ``api.services.completeness.count_content_words`` before/after)
is the tripwire for that gate: if normalization moved the content word
count outside a ±0.5% band, the ENTIRE normalized output is discarded and
raw output is returned unchanged with a non-model-fixable
``formatter.normalization_guard`` error -- this should never fire in
practice since every real substitution pair is word-count-neutral; it only
exists to catch a pathological rule-data mistake before it ships. Formatter
flag tier (oxford_comma/capitol_capital-shaped ``detect`` entries, plus the
shared review-notes-placement check) never scans for voice/forbidden-phrase
violations -- dialogue is not metadata copy; people may legitimately say
"amazing" or "we" on camera.

For ``timestamp``, the model's raw output is not free-text metadata copy --
it is a ```chapters fenced block (see ``phase_io.parse_chapter_list``) that
this stage parses, deterministically cleans up
(:func:`api.services.style_engine.timecodes.snap_chapters`), casing-
normalizes (title-only, same ``to_down_style`` engine as ``seo``), and
re-renders wholesale into the full ``timestamp_output.md`` body
(:func:`api.services.style_engine.phase_io.emit_timestamp_report`) -- so
``normalized_output`` is a different document shape than ``raw_output``, not
a spliced subset of it. Flag tier covers chapter-count-over-cap, chapter
naming length, forbidden/person-voice phrases in titles, and boundaries the
model chose outside the pre-stage's candidate list; every deterministic
``snap_chapters`` adjustment is also individually surfaced as an
informational (non-model-fixable) violation so the audit trail shows exactly
what the engine changed. The chapter-count-over-cap check fires on either of
two signals -- the model's raw chapter count already over the cap, OR
``snap_chapters``' final truncation step actually dropping a chapter -- since
checking the raw count alone under-reports the case where an at-or-under-cap
raw count is pushed over the line by the forced 0:00 first-chapter prepend
(see ``_run_timestamp_post_stage``).

For ``analyst``, there is no enforce tier at all -- ``normalized_output`` is
always byte-identical to ``raw_output`` and ``changed`` is always ``False``.
The analyst's brainstorming document is free-form prose (not a structured
metadata/transcript contract like ``seo``/``formatter``/``timestamp``), so
this phase is flag-only: ``analyst.section_missing`` (a required output
heading, from ``phases.analyst.required_sections``, is absent -- matched
case-insensitively as a substring of an actual markdown heading, so "SEO
Keywords" matches the real "## SEO Keywords (Preliminary)" heading),
``analyst.speaker_table_unparseable`` (:func:`entities.extract_proper_nouns`
found no names in a substantial (>50-word) output -- the formatter and seo
pre-stages both depend on that table), and ``analyst.truncation_suspect``
(the last prose line lacks terminal punctuation, reusing
:func:`lint.find_truncation_excerpt` -- the exact same detection the
formatter phase's ``lint.formatter.truncation_suspect`` check uses, never
duplicated).

``seo``, ``formatter``, ``timestamp``, and ``analyst`` phases have behavior
today; every other phase name degrades gracefully to a ``skipped=True``
passthrough. This module never raises on malformed model output (a bad
``raw_output`` yields ``parse_ok=False``, not an exception) -- it may only
propagate genuine programming errors, which is by design the worker's
fail-open catch's job, not this module's.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from api.services.completeness import count_content_words
from api.services.style_engine.casing import build_canonical, to_down_style
from api.services.style_engine.entities import extract_proper_nouns
from api.services.style_engine.limits import check_field_limits
from api.services.style_engine.lint import find_truncation_excerpt
from api.services.style_engine.phase_io import (
    emit_timestamp_report,
    extract_seo_fields,
    parse_chapter_list,
    splice_seo_fields,
)
from api.services.style_engine.review_notes import check_review_notes_placement
from api.services.style_engine.rules import StyleRules
from api.services.style_engine.scanner import scan_forbidden, scan_person_voice
from api.services.style_engine.substitutions import apply_substitutions_with_fixes, normalize_speaker_turns
from api.services.style_engine.timecodes import Chapter, format_youtube, snap_chapters
from api.services.style_engine.types import (
    AppliedFix,
    PhaseCheckResult,
    PostStageResult,
    RuleViolation,
)
from api.services.utils import get_srt_duration, parse_srt

# Word-count guard band (task 3a's "the enforcer must never eat content"
# acceptance gate). Every real formatter substitution pair is word-count
# neutral by construction (abbreviations, spelling fixes, and
# de-italicization never add/remove a whitespace-delimited token), so this
# band should never be approached in production -- it exists to catch a
# pathological rule-data mistake (e.g. a substitution that deletes a common
# word) before normalized output ever reaches a human editor.
_GUARD_MIN_RATIO = 0.995
_GUARD_MAX_RATIO = 1.005

# Trailing characters trimmed before appending the ellipsis so a truncated
# description doesn't end on a dangling comma/dash.
_TRUNCATE_TRIM = " ,;:—-"


def _truncate_to_limit(value: str, max_len: int) -> str:
    """Truncate ``value`` to at most ``max_len`` chars at a word boundary + "…".

    Returns ``value`` unchanged when already within ``max_len``. Reserves one
    char for the ellipsis and backs off to the last whitespace so a word is
    never cut mid-token (unless the first token alone already exceeds the
    budget, in which case it hard-cuts that token). The result is always
    ``<= max_len`` characters.
    """
    if len(value) <= max_len:
        return value
    budget = max_len - 1  # reserve one char for the ellipsis
    head = value[:budget]
    trimmed = head.rstrip()
    if " " in trimmed:
        head = trimmed[: trimmed.rfind(" ")]
    head = head.rstrip(_TRUNCATE_TRIM)
    if not head:  # a single token longer than the whole budget
        head = value[:budget].rstrip()
    return head + "…"


def run_post_stage(phase: str, raw_output: str, context: Mapping[str, Any], rules: StyleRules) -> PostStageResult:
    """Normalize (enforce tier) then validate (flag tier) one phase's raw LLM output.

    ``context`` keys read (all optional): ``analyst_output`` (source of
    per-job proper nouns for the casing canonical map), ``program`` and
    ``content_type`` (passed through to ``check_field_limits``).

    For ``phase == "formatter"``: applies ``rules.substitutions(tier=
    "enforce")`` + speaker-turn whitespace normalization, guarded by the
    word-count tripwire described in the module docstring, then runs
    flag-tier detection (substitution ``detect`` entries + review-notes
    placement) over the normalized text.

    For ``phase == "timestamp"``: parses the model's ```chapters fenced
    block (``None`` -> passthrough with a ``phase_io.timestamp.unparseable``
    warning), snaps it (``timecodes.snap_chapters``, using
    ``context["style_pre"]["srt_end_ms"]`` when the pre-stage ran, else
    re-parsing ``context["transcript"]``; ``max_chapters`` is always
    recomputed from ``rules`` -- never trusted from a possibly-stale
    ``style_pre`` cache), casing-normalizes each title, then rebuilds the
    entire ``timestamp_output.md`` body via ``phase_io.emit_timestamp_report``.

    For ``phase == "analyst"``: flag-only, never rewrites. Runs the three
    checks described in the module docstring (section_missing,
    speaker_table_unparseable, truncation_suspect) over ``raw_output``
    verbatim. ``normalized_output`` is always ``raw_output`` and ``changed``
    is always ``False``.

    For any other phase (v1): passthrough, ``changed=False``,
    ``PhaseCheckResult(phase=phase, skipped=True)``.
    """
    if phase == "formatter":
        return _run_formatter_post_stage(raw_output, rules)

    if phase == "timestamp":
        return _run_timestamp_post_stage(raw_output, context, rules)

    if phase == "analyst":
        return _run_analyst_post_stage(raw_output, rules)

    if phase != "seo":
        return PostStageResult(
            phase=phase,
            normalized_output=raw_output,
            changed=False,
            check=PhaseCheckResult(phase=phase, skipped=True),
        )

    fields = extract_seo_fields(raw_output)
    if fields.title is None:
        return PostStageResult(
            phase=phase,
            normalized_output=raw_output,
            changed=False,
            check=PhaseCheckResult(
                phase=phase,
                parse_ok=False,
                violations=[
                    RuleViolation(
                        rule_id="phase_io.seo.unparseable",
                        phase=phase,
                        severity="warning",
                        message="Could not extract a Title field from seo phase output",
                        model_fixable=True,
                    )
                ],
            ),
        )

    analyst_output = context.get("analyst_output") or ""
    canonical = build_canonical(rules, extract_proper_nouns(analyst_output, rules.surname_stoplist()))
    program = context.get("program")
    content_type = context.get("content_type") or "full"
    limits = rules.limits_for(program, content_type)

    # Enforce tier: down-style the TITLE casing and truncate over-limit
    # DESCRIPTIONS to their hard character budget. Both are spliced back by
    # exact span in a single pass over the original document, so the
    # Recommended value -- the only text the SST write path consumes -- is
    # always length-compliant.
    replacements: dict[str, str] = {}
    fixes: list[AppliedFix] = []

    raw_title = fields.title.value
    normalized_title = to_down_style(raw_title, canonical)
    if normalized_title != raw_title:
        replacements["title"] = normalized_title
        fixes.append(AppliedFix(rule_id="casing.down_style.title", before=raw_title, after=normalized_title))

    for field_name, span in (
        ("short_description", fields.short_description),
        ("long_description", fields.long_description),
    ):
        if span is None:
            continue
        max_len = (limits.get(field_name) or {}).get("max")
        if not isinstance(max_len, int):
            continue
        truncated = _truncate_to_limit(span.value, max_len)
        if truncated != span.value:
            replacements[field_name] = truncated
            fixes.append(AppliedFix(rule_id=f"limits.{field_name}.truncated", before=span.value, after=truncated))

    changed = bool(replacements)
    normalized_output = splice_seo_fields(raw_output, fields, replacements) if changed else raw_output

    # Flag tier runs over the post-enforcement values (limits now satisfied for
    # any field we truncated; a still-over field would only occur if it had no
    # configured max).
    final_values: dict[str, str] = {"title": replacements.get("title", raw_title)}
    if fields.short_description is not None:
        final_values["short_description"] = replacements.get("short_description", fields.short_description.value)
    if fields.long_description is not None:
        final_values["long_description"] = replacements.get("long_description", fields.long_description.value)

    violations = list(check_field_limits(final_values, rules, phase, program=program, content_type=content_type))
    for field_name, value in final_values.items():
        violations += scan_forbidden(value, rules, phase, field=field_name)
        violations += scan_person_voice(value, rules, phase, field=field_name)

    check = PhaseCheckResult(phase=phase, violations=violations, fixes=fixes, parse_ok=True)

    return PostStageResult(
        phase=phase,
        normalized_output=normalized_output,
        changed=changed,
        check=check,
    )


# ---------------------------------------------------------------------------
# formatter phase
# ---------------------------------------------------------------------------


def _run_formatter_post_stage(raw_output: str, rules: StyleRules) -> PostStageResult:
    phase = "formatter"

    formatter_cfg = (rules.raw.get("phases", {}) or {}).get("formatter", {}) or {}
    speaker_label_spec = formatter_cfg.get("speaker_label") or {}
    review_notes_cfg = formatter_cfg.get("review_notes") or {}

    normalized, fixes = apply_substitutions_with_fixes(raw_output, rules.substitutions(tier="enforce"))
    normalized = normalize_speaker_turns(normalized, speaker_label_spec)

    guard_violation = _check_normalization_guard(raw_output, normalized, phase)
    if guard_violation is not None:
        return PostStageResult(
            phase=phase,
            normalized_output=raw_output,
            changed=False,
            check=PhaseCheckResult(phase=phase, violations=[guard_violation], fixes=[], parse_ok=True),
        )

    changed = normalized != raw_output

    violations: list[RuleViolation] = list(
        _check_flag_tier_substitutions(normalized, rules.substitutions(tier="flag"), phase)
    )
    violations += check_review_notes_placement(normalized, review_notes_cfg, phase)

    check = PhaseCheckResult(phase=phase, violations=violations, fixes=fixes, parse_ok=True)

    return PostStageResult(phase=phase, normalized_output=normalized, changed=changed, check=check)


def _check_normalization_guard(raw_output: str, normalized: str, phase: str) -> RuleViolation | None:
    """The "enforcer must never eat content" tripwire.

    Compares ``completeness.count_content_words`` before/after
    normalization; ``None`` when the ratio is within the allowed band (the
    overwhelmingly common case), else a non-model-fixable error violation
    (the caller discards the normalized output and reverts to raw).
    """
    raw_words = count_content_words(raw_output)
    normalized_words = count_content_words(normalized)
    ratio = (normalized_words / raw_words) if raw_words else 1.0

    if _GUARD_MIN_RATIO <= ratio <= _GUARD_MAX_RATIO:
        return None

    return RuleViolation(
        rule_id="formatter.normalization_guard",
        phase=phase,
        severity="error",
        message=(
            f"Formatter normalization changed content word count from {raw_words} to "
            f"{normalized_words} (ratio {ratio:.3f}, outside [{_GUARD_MIN_RATIO}, {_GUARD_MAX_RATIO}]) "
            "-- reverting to raw output"
        ),
        model_fixable=False,
    )


def _check_flag_tier_substitutions(text: str, flag_substitutions: list[dict], phase: str) -> list[RuleViolation]:
    violations: list[RuleViolation] = []
    for sub in flag_substitutions:
        detect = sub.get("detect")
        if not detect:
            continue
        if not re.search(detect, text):
            continue
        rule_id = f"formatter.{sub.get('id') or 'unnamed_flag'}"
        message = f'Pattern "{detect}" detected in formatter output'
        note = sub.get("note")
        if note:
            message += f" -- {note}"
        violations.append(
            RuleViolation(
                rule_id=rule_id,
                phase=phase,
                severity=sub.get("severity", "warning"),
                message=message,
                model_fixable=True,
            )
        )
    return violations


# ---------------------------------------------------------------------------
# analyst phase
# ---------------------------------------------------------------------------

# ">50 words" -- below this the output is too thin for a missing speaker
# table to mean anything (lint.py's lint.output_missing already catches
# genuinely empty/near-empty phase output at a 50-*character* floor; this is
# a separate, word-count-based floor scoped to this one check).
_ANALYST_SPEAKER_CHECK_MIN_WORDS = 50

_ANALYST_HEADING_RE = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def _run_analyst_post_stage(raw_output: str, rules: StyleRules) -> PostStageResult:
    """Flag-only: never rewrites ``raw_output`` (``changed`` is always
    ``False``). See the module docstring for the three checks."""
    phase = "analyst"

    analyst_cfg = (rules.raw.get("phases", {}) or {}).get("analyst", {}) or {}
    required_sections = list(analyst_cfg.get("required_sections") or [])

    violations: list[RuleViolation] = []
    violations += _check_analyst_required_sections(raw_output, required_sections, phase)
    violations += _check_analyst_speaker_table(raw_output, rules, phase)
    violations += _check_analyst_truncation(raw_output, phase)

    check = PhaseCheckResult(phase=phase, violations=violations, fixes=[], parse_ok=True)
    return PostStageResult(phase=phase, normalized_output=raw_output, changed=False, check=check)


def _analyst_headings(raw_output: str) -> list[str]:
    return [match.group(1).strip() for match in _ANALYST_HEADING_RE.finditer(raw_output)]


def _check_analyst_required_sections(raw_output: str, required_sections: list[str], phase: str) -> list[RuleViolation]:
    """``analyst.section_missing`` -- a required heading (data-driven, from
    ``phases.analyst.required_sections``) is absent. Matched
    case-insensitively as a SUBSTRING of an actual markdown heading, so a
    required name of "SEO Keywords" matches the real analyst output heading
    "## SEO Keywords (Preliminary)" without the parenthetical needing to be
    spelled out in the rule data."""
    if not required_sections:
        return []

    headings_lower = [heading.lower() for heading in _analyst_headings(raw_output)]

    violations: list[RuleViolation] = []
    for section in required_sections:
        section_lower = section.lower()
        if any(section_lower in heading for heading in headings_lower):
            continue
        violations.append(
            RuleViolation(
                rule_id="analyst.section_missing",
                phase=phase,
                severity="warning",
                message=f'Required section heading "{section}" not found in analyst output',
                model_fixable=True,
            )
        )
    return violations


def _check_analyst_speaker_table(raw_output: str, rules: StyleRules, phase: str) -> list[RuleViolation]:
    """``analyst.speaker_table_unparseable`` -- :func:`extract_proper_nouns`
    found no names even though the output is substantial (>50 words). The
    formatter and seo pre-stages both source their authoritative proper-noun
    list from this same extraction, so an unparseable table here silently
    degrades two downstream phases."""
    if len(raw_output.split()) <= _ANALYST_SPEAKER_CHECK_MIN_WORDS:
        return []

    if extract_proper_nouns(raw_output, rules.surname_stoplist()):
        return []

    return [
        RuleViolation(
            rule_id="analyst.speaker_table_unparseable",
            phase=phase,
            severity="warning",
            message=(
                "Could not extract any proper nouns from a Speakers & Roles table in analyst "
                "output, even though the output is substantial -- the formatter and seo "
                "pre-stages depend on this table for authoritative name casing"
            ),
            model_fixable=True,
        )
    ]


def _check_analyst_truncation(raw_output: str, phase: str) -> list[RuleViolation]:
    """``analyst.truncation_suspect`` -- not model-fixable (same rationale as
    ``lint.formatter.truncation_suspect``: a mid-sentence cutoff needs a
    fresh generation, not a targeted textual fix). Reuses
    :func:`lint.find_truncation_excerpt` rather than duplicating the
    last-prose-line detection."""
    excerpt = find_truncation_excerpt(raw_output)
    if excerpt is None:
        return []

    return [
        RuleViolation(
            rule_id="analyst.truncation_suspect",
            phase=phase,
            severity="warning",
            message=f'Last line lacks terminal punctuation, possible mid-sentence cutoff: "{excerpt}"',
            model_fixable=False,
        )
    ]


# ---------------------------------------------------------------------------
# timestamp phase
# ---------------------------------------------------------------------------

# "Only nudge by ~1 second if the nearest speaker transition doesn't have an
# exact timecode match" (prompts/timestamp.md) -- the boundary-unlisted flag
# check tolerates the same window rather than demanding byte-exact ms
# equality against a candidate the model may have legitimately nudged.
_BOUNDARY_TOLERANCE_MS = 1000


def _run_timestamp_post_stage(raw_output: str, context: Mapping[str, Any], rules: StyleRules) -> PostStageResult:
    phase = "timestamp"

    chapters = parse_chapter_list(raw_output)
    if chapters is None:
        return PostStageResult(
            phase=phase,
            normalized_output=raw_output,
            changed=False,
            check=PhaseCheckResult(
                phase=phase,
                parse_ok=False,
                violations=[
                    RuleViolation(
                        rule_id="phase_io.timestamp.unparseable",
                        phase=phase,
                        severity="warning",
                        message="Could not extract a ```chapters fenced block from timestamp phase output",
                        model_fixable=True,
                    )
                ],
            ),
        )

    timestamp_cfg = (rules.raw.get("phases", {}) or {}).get("timestamp", {}) or {}
    first_chapter_title = (timestamp_cfg.get("first_chapter") or {}).get("title", "Episode intro")
    words_cfg = (timestamp_cfg.get("chapter_name") or {}).get("words") or {}
    min_words = words_cfg.get("min")
    max_words = words_cfg.get("max")

    style_pre = context.get("style_pre") or {}
    srt_end_ms = style_pre.get("srt_end_ms")
    if srt_end_ms is None:
        srt_end_ms = _resolve_srt_end_ms(context, chapters)

    # max_chapters is always recomputed from rules -- never trusted from a
    # possibly-stale style_pre cache (a prior phase's context could carry it
    # forward from a different duration in a pathological caller).
    max_chapters = rules.chapter_max(srt_end_ms / 60000 if srt_end_ms else 0)

    pre_snap_count = len(chapters)
    snapped, notes = snap_chapters(
        chapters,
        srt_end_ms=srt_end_ms,
        max_chapters=max_chapters,
        first_chapter_title=first_chapter_title,
    )

    analyst_output = context.get("analyst_output") or ""
    canonical = build_canonical(rules, extract_proper_nouns(analyst_output, rules.surname_stoplist()))

    cased_chapters: list[Chapter] = []
    fixes: list[AppliedFix] = []
    for chapter in snapped:
        cased_title = to_down_style(chapter.title, canonical)
        if cased_title != chapter.title:
            fixes.append(AppliedFix(rule_id="casing.down_style.chapter_title", before=chapter.title, after=cased_title))
        cased_chapters.append(Chapter(title=cased_title, start_ms=chapter.start_ms))

    normalized_output = emit_timestamp_report(
        cased_chapters, srt_end_ms=srt_end_ms, rules=rules, project_name=context.get("project_name")
    )
    fixes.append(AppliedFix(rule_id="timestamp.emit", before=raw_output, after=normalized_output, count=1))

    violations: list[RuleViolation] = []
    # chapter_count fires on EITHER of two independent signals:
    #   1. the model's raw (pre-snap) chapter count already exceeded the cap, or
    #   2. snap_chapters' final truncation step actually dropped a chapter --
    #      identified by its "beyond max_chapters" note (the only snap_chapters
    #      note that contains that substring; dedup/out-of-range notes read
    #      differently -- see timecodes.snap_chapters's docstring).
    # Checking (1) alone under-reports: if the model returns exactly
    # max_chapters chapters but none starts at 0:00, snap_chapters' forced
    # first-chapter prepend pushes the count to max_chapters + 1 *after*
    # pre_snap_count was already measured, so the resulting truncation (which
    # silently drops one of the model's real chapters) would go unflagged.
    truncation_note = next((note for note in notes if "beyond max_chapters" in note), None)
    model_over_cap = pre_snap_count > max_chapters
    if model_over_cap or truncation_note is not None:
        if truncation_note is not None:
            message = (
                f"Model produced {pre_snap_count} chapters; after deterministic cleanup "
                "(deduping, dropping out-of-range boundaries, and the pipeline's forced "
                f"0:00 first chapter) the list exceeded the max of {max_chapters} for this "
                f"duration and snap_chapters truncated it -- {truncation_note}"
            )
        else:
            message = (
                f"Model produced {pre_snap_count} chapters, exceeding the max of {max_chapters} "
                "for this duration -- truncated automatically"
            )
        violations.append(
            RuleViolation(
                rule_id="timestamp.chapter_count",
                phase=phase,
                severity="warning",
                message=message,
                model_fixable=True,
            )
        )

    candidate_ms_values = _candidate_ms_values(style_pre.get("boundary_candidates"))

    for index, chapter in enumerate(cased_chapters):
        word_count = len(chapter.title.split())
        if min_words is not None and max_words is not None and not (min_words <= word_count <= max_words):
            violations.append(
                RuleViolation(
                    rule_id="timestamp.chapter_name_length",
                    phase=phase,
                    severity="warning",
                    field="chapter_title",
                    message=(
                        f'Chapter title "{chapter.title}" has {word_count} word(s) '
                        f"(expected {min_words}-{max_words})"
                    ),
                    model_fixable=True,
                )
            )

        violations += scan_forbidden(chapter.title, rules, phase, field="chapter_title")
        violations += scan_person_voice(chapter.title, rules, phase, field="chapter_title")

        # The first chapter's boundary is always the enforced 0:00 -- it's
        # not a model choice, so it's exempt from candidate-list checking.
        if (
            index > 0
            and candidate_ms_values is not None
            and not _boundary_listed(chapter.start_ms, candidate_ms_values)
        ):
            violations.append(
                RuleViolation(
                    rule_id="timestamp.boundary_unlisted",
                    phase=phase,
                    severity="warning",
                    field="chapter_title",
                    message=(
                        f'Chapter "{chapter.title}" boundary {format_youtube(chapter.start_ms)} '
                        "is not in the candidate list"
                    ),
                    model_fixable=True,
                )
            )

    for note in notes:
        violations.append(
            RuleViolation(
                rule_id="timestamp.snapped",
                phase=phase,
                severity="warning",
                message=note,
                model_fixable=False,
            )
        )

    changed = normalized_output != raw_output

    check = PhaseCheckResult(phase=phase, violations=violations, fixes=fixes, parse_ok=True)
    return PostStageResult(phase=phase, normalized_output=normalized_output, changed=changed, check=check)


def _resolve_srt_end_ms(context: Mapping[str, Any], chapters: list[Chapter]) -> int:
    """Best-effort srt_end_ms when the pre-stage never ran (or ran on a
    different context): re-parse ``context["transcript"]`` when it looks
    like an SRT, else fall back to the latest chapter start the model gave
    us so ``snap_chapters`` always has a usable (if approximate) end."""
    transcript_file = context.get("transcript_file") or ""
    transcript = context.get("transcript") or ""
    if transcript_file.lower().endswith(".srt") and transcript:
        captions = parse_srt(transcript)
        if captions:
            return get_srt_duration(captions)
    return max((chapter.start_ms for chapter in chapters), default=0)


def _candidate_ms_values(boundary_candidates: Any) -> set[int] | None:
    if not boundary_candidates:
        return None
    values: set[int] = set()
    for candidate in boundary_candidates:
        time_ms = candidate.get("time_ms") if isinstance(candidate, Mapping) else None
        if time_ms is not None:
            values.add(int(time_ms))
    return values or None


def _boundary_listed(start_ms: int, candidate_ms_values: set[int]) -> bool:
    return any(abs(start_ms - candidate_ms) <= _BOUNDARY_TOLERANCE_MS for candidate_ms in candidate_ms_values)
