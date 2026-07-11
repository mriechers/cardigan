"""Deterministic voice / forbidden-phrase scanning.

Scans model output text against ``StyleRules``' ``voice.forbidden_phrases``
entries and first/second-person markers, producing ``RuleViolation``
objects. Detection only -- no text is modified here (that's the formatter
substitution engine's job, a later task). Pure stdlib regex over the loaded
rule data; no I/O.
"""

from __future__ import annotations

import re

from api.services.style_engine.rules import StyleRules
from api.services.style_engine.types import RuleViolation


def scan_forbidden(text: str, rules: StyleRules, phase: str, field: str | None = None) -> list[RuleViolation]:
    """Flag every match of a ``voice.forbidden_phrases`` entry in ``text``.

    Literal entries (no ``regex: true``) match case-insensitively as a
    whole word/phrase: the phrase is ``re.escape``'d and wrapped in
    ``\\b...\\b``. Entries with ``regex: true`` compile their ``match``
    string as-is (also case-insensitively) -- this is why a pattern like
    ``\\bfree\\b`` can still fire inside "gluten-free" (a hyphen is a word
    boundary too); that known false-positive shape is exactly why such
    entries are authored at severity "warning" rather than "error".

    One violation per match: ``rule_id`` is ``voice.forbidden.<category>``,
    ``severity`` comes from the entry, ``span`` is the match span, and the
    message quotes the matched text. Always ``model_fixable=True``.
    """
    violations: list[RuleViolation] = []
    for entry in rules.forbidden():
        raw_pattern = entry.get("match", "")
        if not raw_pattern:
            continue
        is_regex = bool(entry.get("regex", False))
        pattern = raw_pattern if is_regex else rf"\b{re.escape(raw_pattern)}\b"
        category = entry.get("category", "general")
        severity = entry.get("severity", "error")

        for match in re.finditer(pattern, text, re.IGNORECASE):
            violations.append(
                RuleViolation(
                    rule_id=f"voice.forbidden.{category}",
                    phase=phase,
                    severity=severity,
                    message=f'Forbidden phrase "{match.group(0)}" (category: {category})',
                    field=field,
                    span=match.span(),
                    model_fixable=True,
                )
            )
    return violations


def scan_person_voice(text: str, rules: StyleRules, phase: str, field: str | None = None) -> list[RuleViolation]:
    """Flag first- and second-person voice markers in ``text``.

    ``first_person_markers`` -> ``rule_id="voice.first_person"``, severity
    ``"error"`` (house style is firmly third-person, descriptive). Note:
    the ``rule_id`` is not the entry's own regex string but this fixed
    identifier -- markers are alternate spellings of the same underlying
    rule. ``second_person_markers`` -> ``rule_id="voice.second_person"``,
    severity ``"warning"`` -- that list is authored, not sourced from the
    style guide, so it stays advisory. Markers are regex strings, compiled
    case-insensitively. One violation per match, with span.
    """
    violations: list[RuleViolation] = []

    for pattern in rules.first_person_markers():
        for match in re.finditer(pattern, text, re.IGNORECASE):
            violations.append(
                RuleViolation(
                    rule_id="voice.first_person",
                    phase=phase,
                    severity="error",
                    message=f'First-person voice marker "{match.group(0)}"',
                    field=field,
                    span=match.span(),
                    model_fixable=True,
                )
            )

    for pattern in rules.second_person_markers():
        for match in re.finditer(pattern, text, re.IGNORECASE):
            violations.append(
                RuleViolation(
                    rule_id="voice.second_person",
                    phase=phase,
                    severity="warning",
                    message=f'Second-person voice marker "{match.group(0)}"',
                    field=field,
                    span=match.span(),
                    model_fixable=True,
                )
            )

    return violations
