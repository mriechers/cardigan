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

Only the ``seo`` phase has behavior today; every other phase name degrades
gracefully to a ``skipped=True`` passthrough. This module never raises on
malformed model output (a bad ``raw_output`` yields ``parse_ok=False``, not
an exception) -- it may only propagate genuine programming errors, which is
by design the worker's fail-open catch's job, not this module's.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from api.services.style_engine.casing import build_canonical, to_down_style
from api.services.style_engine.entities import extract_proper_nouns
from api.services.style_engine.limits import check_field_limits
from api.services.style_engine.phase_io import extract_seo_fields, splice_seo_fields
from api.services.style_engine.rules import StyleRules
from api.services.style_engine.scanner import scan_forbidden, scan_person_voice
from api.services.style_engine.types import (
    AppliedFix,
    PhaseCheckResult,
    PostStageResult,
    RuleViolation,
)


def run_post_stage(
    phase: str, raw_output: str, context: Mapping[str, Any], rules: StyleRules
) -> PostStageResult:
    """Normalize (enforce tier) then validate (flag tier) one phase's raw LLM output.

    ``context`` keys read (all optional): ``analyst_output`` (source of
    per-job proper nouns for the casing canonical map), ``program`` and
    ``content_type`` (passed through to ``check_field_limits``).

    For any phase other than ``seo`` (v1): passthrough, ``changed=False``,
    ``PhaseCheckResult(phase=phase, skipped=True)``.
    """
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
