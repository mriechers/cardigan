"""Tests for api.services.style_engine.qa_merge.merge_style_flags (task 2a).

All validator/style-check data is synthetic, built as plain dicts shaped
like api.services.worker.JobWorker._parse_validation_result's output and
api.services.style_engine.types.PhaseCheckResult.to_dict() respectively --
never touches a real job or the DB.
"""

from __future__ import annotations

import copy

from api.services.style_engine.qa_merge import merge_style_flags

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _violation(
    rule_id: str = "lint.seo.title_over_limit",
    severity: str = "error",
    message: str = "title is 90 chars (limit 80)",
    model_fixable: bool = True,
    phase: str = "seo",
    field: str | None = "title",
) -> dict:
    return {
        "rule_id": rule_id,
        "phase": phase,
        "severity": severity,
        "message": message,
        "field": field,
        "span": None,
        "model_fixable": model_fixable,
    }


def _check(*violations: dict) -> dict:
    return {"phase": "seo", "violations": list(violations), "fixes": [], "parse_ok": True, "skipped": False}


def _validation_data(**phase_overrides) -> dict:
    phases = {
        "analyst": {"status": "pass", "flags": []},
        "formatter": {"status": "pass", "flags": []},
        "seo": {"status": "pass", "flags": []},
    }
    for phase, override in phase_overrides.items():
        phases[phase] = override
    return {"phase_results": phases, "overall": "pass"}


_ENABLED_CFG = {"merge_flags": True, "fail_on_error": True}
_MERGE_ONLY_CFG = {"merge_flags": True, "fail_on_error": False}
_DISABLED_CFG = {"merge_flags": False, "fail_on_error": True}


# ---------------------------------------------------------------------------
# merge_flags gating
# ---------------------------------------------------------------------------


class TestMergeFlagsGating:
    def test_merge_flags_false_returns_validation_data_unchanged(self):
        validation_data = _validation_data()
        style_checks = {"seo": _check(_violation())}

        result = merge_style_flags(validation_data, style_checks, _DISABLED_CFG)

        assert result == validation_data
        assert result["phase_results"]["seo"]["flags"] == []
        assert result["overall"] == "pass"

    def test_empty_style_checks_returns_validation_data_unchanged(self):
        validation_data = _validation_data()

        result = merge_style_flags(validation_data, {}, _ENABLED_CFG)

        assert result == validation_data

    def test_none_style_checks_returns_validation_data_unchanged(self):
        validation_data = _validation_data()

        result = merge_style_flags(validation_data, None, _ENABLED_CFG)

        assert result == validation_data

    def test_none_cfg_treated_as_merge_disabled(self):
        validation_data = _validation_data()
        style_checks = {"seo": _check(_violation())}

        result = merge_style_flags(validation_data, style_checks, None)

        assert result["phase_results"]["seo"]["flags"] == []


# ---------------------------------------------------------------------------
# validation_data is None -> skeleton
# ---------------------------------------------------------------------------


class TestValidationDataNone:
    def test_none_with_merge_disabled_returns_bare_skeleton(self):
        result = merge_style_flags(None, {"seo": _check(_violation())}, _DISABLED_CFG)

        assert result["_merged_from_none"] is True
        assert result["overall"] == "pass"
        for phase in ("analyst", "formatter", "seo"):
            assert result["phase_results"][phase] == {"status": "pass", "flags": []}

    def test_none_with_merge_enabled_builds_skeleton_then_merges(self):
        style_checks = {"seo": _check(_violation(rule_id="lint.seo.title_over_limit", severity="error"))}

        result = merge_style_flags(None, style_checks, _ENABLED_CFG)

        assert result["_merged_from_none"] is True
        assert result["phase_results"]["seo"]["flags"] == ["[style:lint.seo.title_over_limit] title is 90 chars (limit 80)"]
        assert result["phase_results"]["seo"]["status"] == "fail"
        assert result["overall"] == "fail"
        # Untouched phases stay clean.
        assert result["phase_results"]["analyst"] == {"status": "pass", "flags": []}


# ---------------------------------------------------------------------------
# Flag text reconstruction + dedupe
# ---------------------------------------------------------------------------


class TestFlagTextAndDedupe:
    def test_model_fixable_violation_uses_style_prefix(self):
        validation_data = _validation_data()
        style_checks = {"seo": _check(_violation(model_fixable=True, message="title too long"))}

        result = merge_style_flags(validation_data, style_checks, _MERGE_ONLY_CFG)

        assert "[style:lint.seo.title_over_limit] title too long" in result["phase_results"]["seo"]["flags"]

    def test_nonfixable_violation_uses_style_nonfixable_prefix(self):
        validation_data = _validation_data()
        style_checks = {
            "formatter": _check(
                _violation(
                    rule_id="lint.output_missing",
                    severity="error",
                    message="formatter output is missing or empty",
                    model_fixable=False,
                    phase="formatter",
                    field=None,
                )
            )
        }

        result = merge_style_flags(validation_data, style_checks, _MERGE_ONLY_CFG)

        assert (
            "[style-nonfixable:lint.output_missing] formatter output is missing or empty"
            in result["phase_results"]["formatter"]["flags"]
        )

    def test_dedupes_against_existing_flag_text_exact_match(self):
        existing_flag = "[style:lint.seo.title_over_limit] title is 90 chars (limit 80)"
        validation_data = _validation_data(seo={"status": "fail", "flags": [existing_flag]})
        style_checks = {"seo": _check(_violation(message="title is 90 chars (limit 80)"))}

        result = merge_style_flags(validation_data, style_checks, _MERGE_ONLY_CFG)

        assert result["phase_results"]["seo"]["flags"] == [existing_flag]

    def test_flags_append_preserves_existing_order(self):
        existing = ["llm flag one", "llm flag two"]
        validation_data = _validation_data(seo={"status": "pass", "flags": list(existing)})
        style_checks = {
            "seo": _check(
                _violation(rule_id="lint.seo.title_over_limit", message="title issue"),
                _violation(rule_id="lint.seo.short_over_limit", message="short issue", field="short_description"),
            )
        }

        result = merge_style_flags(validation_data, style_checks, _MERGE_ONLY_CFG)

        flags = result["phase_results"]["seo"]["flags"]
        assert flags[:2] == existing
        assert flags[2] == "[style:lint.seo.title_over_limit] title issue"
        assert flags[3] == "[style:lint.seo.short_over_limit] short issue"

    def test_never_removes_or_reorders_existing_llm_flags(self):
        existing = ["review note: media_id unresolved", "second llm flag"]
        validation_data = _validation_data(formatter={"status": "fail", "flags": list(existing)})
        style_checks = {"formatter": _check(_violation(rule_id="lint.formatter.truncation_suspect", phase="formatter", field=None))}

        result = merge_style_flags(validation_data, style_checks, _MERGE_ONLY_CFG)

        flags = result["phase_results"]["formatter"]["flags"]
        assert flags[0] == existing[0]
        assert flags[1] == existing[1]


# ---------------------------------------------------------------------------
# fail_on_error
# ---------------------------------------------------------------------------


class TestFailOnError:
    def test_error_severity_flips_phase_and_overall(self):
        validation_data = _validation_data()
        style_checks = {"seo": _check(_violation(severity="error"))}

        result = merge_style_flags(validation_data, style_checks, _ENABLED_CFG)

        assert result["phase_results"]["seo"]["status"] == "fail"
        assert result["overall"] == "fail"

    def test_warning_severity_never_flips_phase_or_overall(self):
        validation_data = _validation_data()
        style_checks = {"seo": _check(_violation(rule_id="lint.seo.keywords_count", severity="warning"))}

        result = merge_style_flags(validation_data, style_checks, _ENABLED_CFG)

        assert result["phase_results"]["seo"]["status"] == "pass"
        assert result["overall"] == "pass"

    def test_fail_on_error_false_never_flips_even_with_error(self):
        validation_data = _validation_data()
        style_checks = {"seo": _check(_violation(severity="error"))}

        result = merge_style_flags(validation_data, style_checks, _MERGE_ONLY_CFG)

        assert result["phase_results"]["seo"]["status"] == "pass"
        assert result["overall"] == "pass"

    def test_one_phase_error_does_not_flip_unrelated_phase_status(self):
        validation_data = _validation_data()
        style_checks = {"seo": _check(_violation(severity="error"))}

        result = merge_style_flags(validation_data, style_checks, _ENABLED_CFG)

        assert result["phase_results"]["analyst"]["status"] == "pass"
        assert result["phase_results"]["formatter"]["status"] == "pass"
        # overall still flips because ANY phase failing fails the whole verdict.
        assert result["overall"] == "fail"

    def test_already_failed_phase_stays_failed_without_error_violation(self):
        validation_data = _validation_data(seo={"status": "fail", "flags": ["pre-existing llm failure"]})
        style_checks = {"seo": _check(_violation(rule_id="lint.seo.keywords_count", severity="warning"))}

        result = merge_style_flags(validation_data, style_checks, _ENABLED_CFG)

        # Merge never demotes a phase; pre-existing fail status is untouched.
        assert result["phase_results"]["seo"]["status"] == "fail"


# ---------------------------------------------------------------------------
# Unknown phases ignored
# ---------------------------------------------------------------------------


class TestUnknownPhaseIgnored:
    def test_style_checks_phase_not_in_phase_results_is_ignored(self):
        validation_data = _validation_data()
        style_checks = {
            "timestamp": _check(_violation(rule_id="lint.timestamp.something", phase="timestamp", field=None)),
        }

        result = merge_style_flags(validation_data, style_checks, _ENABLED_CFG)

        assert "timestamp" not in result["phase_results"]
        assert result["overall"] == "pass"
        for phase in ("analyst", "formatter", "seo"):
            assert result["phase_results"][phase]["flags"] == []


# ---------------------------------------------------------------------------
# No input mutation
# ---------------------------------------------------------------------------


class TestNoInputMutation:
    def test_validation_data_not_mutated(self):
        validation_data = _validation_data()
        original = copy.deepcopy(validation_data)
        style_checks = {"seo": _check(_violation(severity="error"))}

        merge_style_flags(validation_data, style_checks, _ENABLED_CFG)

        assert validation_data == original

    def test_style_checks_not_mutated(self):
        style_checks = {"seo": _check(_violation(severity="error"))}
        original = copy.deepcopy(style_checks)
        validation_data = _validation_data()

        merge_style_flags(validation_data, style_checks, _ENABLED_CFG)

        assert style_checks == original

    def test_cfg_not_mutated(self):
        cfg = dict(_ENABLED_CFG)
        original = copy.deepcopy(cfg)
        validation_data = _validation_data()
        style_checks = {"seo": _check(_violation())}

        merge_style_flags(validation_data, style_checks, cfg)

        assert cfg == original

    def test_returned_dict_is_not_the_same_object_as_input(self):
        validation_data = _validation_data()

        result = merge_style_flags(validation_data, {}, _ENABLED_CFG)

        assert result is not validation_data

    def test_mutating_result_does_not_affect_original_validation_data(self):
        validation_data = _validation_data()
        style_checks = {"seo": _check(_violation(severity="error"))}

        result = merge_style_flags(validation_data, style_checks, _ENABLED_CFG)
        result["phase_results"]["seo"]["flags"].append("mutated after the fact")
        result["phase_results"]["analyst"]["status"] = "fail"

        assert validation_data["phase_results"]["seo"]["flags"] == []
        assert validation_data["phase_results"]["analyst"]["status"] == "pass"
