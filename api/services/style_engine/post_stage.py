"""Pipeline post-generation stage: normalize (enforce tier), then validate (flag tier).

Pure stdlib + style_engine internals -- no worker/DB/async/FastAPI imports.
Runs after a phase's LLM call returns raw markdown. Two tiers, never
conflated:

- **Enforce tier** -- deterministic rewrites that never change meaning. For
  ``seo`` this is limited to down-styling the TITLE field's casing (spliced
  back into the document via its exact span). Nothing else is ever rewritten
  here.
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

``seo`` and ``formatter`` phases have behavior today; every other phase name
degrades gracefully to a ``skipped=True`` passthrough. This module never
raises on malformed model output (a bad ``raw_output`` yields
``parse_ok=False``, not an exception) -- it may only propagate genuine
programming errors, which is by design the worker's fail-open catch's job,
not this module's.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from api.services.completeness import count_content_words
from api.services.style_engine.casing import build_canonical, to_down_style
from api.services.style_engine.entities import extract_proper_nouns
from api.services.style_engine.limits import check_field_limits
from api.services.style_engine.phase_io import extract_seo_fields, splice_seo_fields
from api.services.style_engine.review_notes import check_review_notes_placement
from api.services.style_engine.rules import StyleRules
from api.services.style_engine.scanner import scan_forbidden, scan_person_voice
from api.services.style_engine.substitutions import apply_substitutions_with_fixes, normalize_speaker_turns
from api.services.style_engine.types import (
    AppliedFix,
    PhaseCheckResult,
    PostStageResult,
    RuleViolation,
)

# Word-count guard band (task 3a's "the enforcer must never eat content"
# acceptance gate). Every real formatter substitution pair is word-count
# neutral by construction (abbreviations, spelling fixes, and
# de-italicization never add/remove a whitespace-delimited token), so this
# band should never be approached in production -- it exists to catch a
# pathological rule-data mistake (e.g. a substitution that deletes a common
# word) before normalized output ever reaches a human editor.
_GUARD_MIN_RATIO = 0.995
_GUARD_MAX_RATIO = 1.005


def run_post_stage(
    phase: str, raw_output: str, context: Mapping[str, Any], rules: StyleRules
) -> PostStageResult:
    """Normalize (enforce tier) then validate (flag tier) one phase's raw LLM output.

    ``context`` keys read (all optional): ``analyst_output`` (source of
    per-job proper nouns for the casing canonical map), ``program`` and
    ``content_type`` (passed through to ``check_field_limits``).

    For ``phase == "formatter"``: applies ``rules.substitutions(tier=
    "enforce")`` + speaker-turn whitespace normalization, guarded by the
    word-count tripwire described in the module docstring, then runs
    flag-tier detection (substitution ``detect`` entries + review-notes
    placement) over the normalized text.

    For any other phase (v1): passthrough, ``changed=False``,
    ``PhaseCheckResult(phase=phase, skipped=True)``.
    """
    if phase == "formatter":
        return _run_formatter_post_stage(raw_output, rules)

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

    raw_title = fields.title.value
    normalized_title = to_down_style(raw_title, canonical)
    changed = normalized_title != raw_title

    fixes: list[AppliedFix] = []
    if changed:
        normalized_output = splice_seo_fields(raw_output, fields, {"title": normalized_title})
        fixes.append(
            AppliedFix(
                rule_id="casing.down_style.title",
                before=raw_title,
                after=normalized_title,
            )
        )
    else:
        normalized_output = raw_output

    program = context.get("program")
    content_type = context.get("content_type") or "full"

    final_values: dict[str, str] = {"title": normalized_title}
    if fields.short_description is not None:
        final_values["short_description"] = fields.short_description.value
    if fields.long_description is not None:
        final_values["long_description"] = fields.long_description.value

    violations = list(
        check_field_limits(final_values, rules, phase, program=program, content_type=content_type)
    )
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
