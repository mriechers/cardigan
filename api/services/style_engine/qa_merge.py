"""Deterministic merge of style/lint findings into the LLM validator's verdict.

Pure stdlib -- no worker/DB/async/FastAPI imports. ``merge_style_flags``
folds the deterministic ``PhaseCheckResult`` violations produced by
``post_stage.run_post_stage`` and ``lint.run_lint`` into the parsed
validator JSON (see ``api.services.worker.JobWorker._parse_validation_result``
for that shape), without ever changing the JSON's top-level contract:

    {"phase_results": {"<phase>": {"status": "pass"|"fail", "flags": [str, ...]}, ...},
     "overall": "pass"|"fail"}

This module never removes or reorders an existing LLM-authored flag, and
never mutates any input it's given -- callers get back a fresh structure
built from a deep copy (or a freshly built skeleton).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

# The validator's required phases (matches lint.py's _CANONICAL_PHASES /
# api.services.worker.JobWorker.PHASES minus "validator" itself). Violations
# for any other phase key present in style_checks (e.g. "timestamp") are
# ignored in v1 -- see the docstring on merge_style_flags below.
_SKELETON_PHASES = ("analyst", "formatter", "seo")


def merge_style_flags(
    validation_data: dict | None,
    style_checks: Mapping[str, Mapping] | None,
    cfg: Mapping | None,
) -> dict:
    """Deterministically merge style/lint findings into the LLM validator's verdict.

    Args:
        validation_data: parsed validator JSON (phase_results/overall shape
            above) or ``None`` when the LLM's output was unparseable. When
            ``None``, this starts from a skeleton with all three phases at
            status ``"pass"``, empty flags, ``overall="pass"``, plus a
            ``"_merged_from_none": True`` marker.
        style_checks: ``{"seo": PhaseCheckResult.to_dict(), "analyst": ...,
            "formatter": ...}`` -- the deterministic check results
            accumulated by the post-stages and ``lint.run_lint``. Consumed
            purely as ``to_dict()``-shaped mappings; never mutated.
        cfg: the ``routing.style_engine.qa_gate`` config block
            (``{"merge_flags": bool, "fail_on_error": bool}``).

    Returns:
        A new dict (never the same object as ``validation_data``). When
        ``cfg["merge_flags"]`` is falsy, or ``style_checks`` is empty/None,
        the result is the unmodified ``validation_data`` (or the None
        skeleton) -- no merging happens. Otherwise, for every phase present
        in *both* ``style_checks`` and ``phase_results``, each violation's
        flag text (``"[style:{rule_id}] {message}"`` for model-fixable
        violations, ``"[style-nonfixable:{rule_id}] {message}"`` otherwise --
        mirrors ``RuleViolation.to_flag_text()``) is appended to that
        phase's flags list, deduped against flags already present (exact
        string match) and never reordering or removing what was already
        there. When ``cfg["fail_on_error"]`` is truthy and a phase has at
        least one ``severity == "error"`` violation among its style_checks,
        that phase's status is set to ``"fail"`` and ``overall`` is set to
        ``"fail"`` (warnings alone never flip anything). A style_checks key
        with no matching entry in ``phase_results`` (e.g. ``"timestamp"``)
        is ignored.
    """
    cfg = cfg or {}
    style_checks = style_checks or {}

    if validation_data is None:
        result: dict[str, Any] = {
            "phase_results": {phase: {"status": "pass", "flags": []} for phase in _SKELETON_PHASES},
            "overall": "pass",
            "_merged_from_none": True,
        }
    else:
        result = copy.deepcopy(validation_data)

    if not cfg.get("merge_flags") or not style_checks:
        return result

    phase_results = result.setdefault("phase_results", {})
    overall_failed = False

    for phase_name, check in style_checks.items():
        if phase_name not in phase_results:
            continue

        phase_result = phase_results[phase_name]
        existing_flags = phase_result.setdefault("flags", [])
        violations = (check or {}).get("violations") or []

        has_error = False
        for violation in violations:
            flag_text = _to_flag_text(violation)
            if flag_text not in existing_flags:
                existing_flags.append(flag_text)
            if violation.get("severity") == "error":
                has_error = True

        if cfg.get("fail_on_error") and has_error:
            phase_result["status"] = "fail"
            overall_failed = True

    if overall_failed:
        result["overall"] = "fail"

    return result


def _to_flag_text(violation: Mapping[str, Any]) -> str:
    """Reconstruct ``RuleViolation.to_flag_text()``'s string from a to_dict() mapping."""
    prefix = "style" if violation.get("model_fixable", True) else "style-nonfixable"
    return f"[{prefix}:{violation.get('rule_id')}] {violation.get('message')}"
