"""Tests for the style_engine rule types and YAML rule loader.

Covers the pure dataclasses in api.services.style_engine.types (violations,
fixes, phase/pre/post stage results) and the mtime-cached YAML loader in
api.services.style_engine.rules. All rule data is synthetic — built with
pytest's tmp_path fixture — and mirrors the shape of the real
config/house_style.yaml schema without depending on that file.
"""

import json
import os
import time
from pathlib import Path

import pytest

from api.services.style_engine.rules import (
    StyleRules,
    StyleRulesError,
    load_rules,
)
from api.services.style_engine.types import (
    AppliedFix,
    PhaseCheckResult,
    PostStageResult,
    PreStageResult,
    RuleViolation,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# Mirrors the production config/house_style.yaml schema (see task-0.2 brief).
SAMPLE_YAML = r"""
meta: {version: 1, style_guide_synced: "2026-07-10"}
voice:
  forbidden_phrases:
    - {match: "watch as", category: viewer_directive, tier: flag, severity: error}
    - {match: "\\bfree\\b", category: sales, tier: flag, severity: warning, regex: true}
  first_person_markers: ["\\bwe\\b"]
  second_person_markers: ["\\byou\\b"]
casing:
  style: down
  proper_nouns: [Wisconsin, "Marquette Poll"]
  acronyms: [PBS]
  casing_variants: {gov: "Gov."}
  surname_stoplist: [van, der]
limits:
  fields:
    title: {max: 80}
    short_description: {max: 90}
    keywords: {count: {min: 15, max: 20}}
  content_type_overrides:
    short: {keywords: {count: {min: 5, max: 10}}}
phases:
  formatter:
    substitutions:
      - {find: "\\b[Oo]kay\\b", replace: "OK", tier: enforce}
      - {id: oxford_comma, detect: ",\\s+and\\b", tier: flag, severity: warning}
  timestamp:
    chapter_max_by_duration: [{lt: 5, max: 3}, {lt: 15, max: 5}, {lt: null, max: 10}]
programs:
  "Here & Now": {livestream_title_suffix: " | Here & Now"}
"""

MINIMAL_YAML = 'meta: {version: 1, style_guide_synced: "2026-07-10"}\n'


def _write(tmp_path: Path, content: str, name: str = "house_style.yaml") -> Path:
    """Write synthetic YAML content to a file under tmp_path and return its path."""
    path = tmp_path / name
    path.write_text(content)
    return path


def _load_sample(tmp_path: Path) -> StyleRules:
    return load_rules(_write(tmp_path, SAMPLE_YAML))


# ---------------------------------------------------------------------------
# load_rules — happy path
# ---------------------------------------------------------------------------


class TestLoadRulesHappyPath:
    def test_raw_round_trips(self, tmp_path):
        rules = _load_sample(tmp_path)
        assert rules.raw["meta"]["version"] == 1
        assert rules.raw["meta"]["style_guide_synced"] == "2026-07-10"
        assert rules.raw["casing"]["proper_nouns"] == ["Wisconsin", "Marquette Poll"]

    def test_returns_style_rules_instance(self, tmp_path):
        rules = _load_sample(tmp_path)
        assert isinstance(rules, StyleRules)

    def test_accepts_str_path(self, tmp_path):
        path = _write(tmp_path, SAMPLE_YAML)
        rules = load_rules(str(path))
        assert rules.raw["meta"]["version"] == 1


# ---------------------------------------------------------------------------
# load_rules — mtime caching
# ---------------------------------------------------------------------------


class TestLoadRulesCaching:
    def test_unchanged_file_returns_same_object(self, tmp_path):
        path = _write(tmp_path, SAMPLE_YAML)
        first = load_rules(path)
        second = load_rules(path)
        assert first is second

    def test_changed_mtime_reloads(self, tmp_path):
        path = _write(tmp_path, SAMPLE_YAML)
        first = load_rules(path)

        updated = SAMPLE_YAML.replace("version: 1", "version: 2")
        path.write_text(updated)
        future = time.time() + 10
        os.utime(path, (future, future))

        second = load_rules(path)
        assert second is not first
        assert second.raw["meta"]["version"] == 2

    def test_different_paths_are_independent(self, tmp_path):
        path_a = _write(tmp_path, SAMPLE_YAML, name="a.yaml")
        path_b = _write(tmp_path, MINIMAL_YAML, name="b.yaml")
        rules_a = load_rules(path_a)
        rules_b = load_rules(path_b)
        assert rules_a is not rules_b
        assert "voice" in rules_a.raw
        assert "voice" not in rules_b.raw


# ---------------------------------------------------------------------------
# load_rules — error handling
# ---------------------------------------------------------------------------


class TestLoadRulesErrors:
    def test_missing_file_raises(self, tmp_path):
        missing = tmp_path / "does_not_exist.yaml"
        with pytest.raises(StyleRulesError, match="not found"):
            load_rules(missing)

    def test_invalid_yaml_raises(self, tmp_path):
        path = _write(tmp_path, "[unclosed")
        with pytest.raises(StyleRulesError, match="YAML"):
            load_rules(path)

    def test_non_dict_root_raises(self, tmp_path):
        path = _write(tmp_path, "- a\n- b\n")
        with pytest.raises(StyleRulesError, match="mapping"):
            load_rules(path)

    def test_missing_meta_section_raises(self, tmp_path):
        path = _write(tmp_path, "voice:\n  forbidden_phrases: []\n")
        with pytest.raises(StyleRulesError, match="meta"):
            load_rules(path)


# ---------------------------------------------------------------------------
# StyleRules.limits_for
# ---------------------------------------------------------------------------


class TestLimitsFor:
    def test_default_content_type(self, tmp_path):
        rules = _load_sample(tmp_path)
        limits = rules.limits_for()
        assert limits["title"]["max"] == 80
        assert limits["short_description"]["max"] == 90
        assert limits["keywords"]["count"] == {"min": 15, "max": 20}

    def test_content_type_override_deep_merges(self, tmp_path):
        rules = _load_sample(tmp_path)
        limits = rules.limits_for(content_type="short")
        assert limits["keywords"]["count"] == {"min": 5, "max": 10}
        # Untouched fields survive the merge.
        assert limits["title"]["max"] == 80
        assert limits["short_description"]["max"] == 90

    def test_unknown_content_type_returns_base(self, tmp_path):
        rules = _load_sample(tmp_path)
        limits = rules.limits_for(content_type="nonexistent")
        assert limits["title"]["max"] == 80
        assert limits["keywords"]["count"] == {"min": 15, "max": 20}

    def test_program_param_is_currently_a_no_op(self, tmp_path):
        rules = _load_sample(tmp_path)
        with_program = rules.limits_for(program="Here & Now")
        without_program = rules.limits_for(program=None)
        assert with_program == without_program


# ---------------------------------------------------------------------------
# StyleRules.substitutions
# ---------------------------------------------------------------------------


class TestSubstitutions:
    def test_returns_all_by_default(self, tmp_path):
        rules = _load_sample(tmp_path)
        subs = rules.substitutions()
        assert len(subs) == 2

    def test_filters_by_tier(self, tmp_path):
        rules = _load_sample(tmp_path)
        subs = rules.substitutions(tier="enforce")
        assert len(subs) == 1
        assert subs[0]["find"] == r"\b[Oo]kay\b"

    def test_filters_by_tier_no_matches(self, tmp_path):
        rules = _load_sample(tmp_path)
        assert rules.substitutions(tier="nonexistent") == []


# ---------------------------------------------------------------------------
# StyleRules — voice pass-throughs
# ---------------------------------------------------------------------------


class TestVoiceAccessors:
    def test_forbidden_pass_through(self, tmp_path):
        rules = _load_sample(tmp_path)
        forbidden = rules.forbidden()
        assert len(forbidden) == 2
        assert forbidden[0] == {
            "match": "watch as",
            "category": "viewer_directive",
            "tier": "flag",
            "severity": "error",
        }

    def test_first_person_markers(self, tmp_path):
        rules = _load_sample(tmp_path)
        assert rules.first_person_markers() == [r"\bwe\b"]

    def test_second_person_markers(self, tmp_path):
        rules = _load_sample(tmp_path)
        assert rules.second_person_markers() == [r"\byou\b"]

    def test_missing_voice_section_is_graceful(self, tmp_path):
        rules = load_rules(_write(tmp_path, MINIMAL_YAML))
        assert rules.forbidden() == []
        assert rules.first_person_markers() == []
        assert rules.second_person_markers() == []


# ---------------------------------------------------------------------------
# StyleRules.canonical_seed / surname_stoplist
# ---------------------------------------------------------------------------


class TestCanonicalSeed:
    def test_maps_proper_nouns_including_multi_word(self, tmp_path):
        rules = _load_sample(tmp_path)
        seed = rules.canonical_seed()
        assert seed["wisconsin"] == "Wisconsin"
        assert seed["marquette poll"] == "Marquette Poll"

    def test_maps_acronyms(self, tmp_path):
        rules = _load_sample(tmp_path)
        seed = rules.canonical_seed()
        assert seed["pbs"] == "PBS"

    def test_maps_casing_variants(self, tmp_path):
        rules = _load_sample(tmp_path)
        seed = rules.canonical_seed()
        assert seed["gov"] == "Gov."

    def test_missing_casing_section_returns_empty(self, tmp_path):
        rules = load_rules(_write(tmp_path, MINIMAL_YAML))
        assert rules.canonical_seed() == {}


class TestSurnameStoplist:
    def test_returns_set(self, tmp_path):
        rules = _load_sample(tmp_path)
        stoplist = rules.surname_stoplist()
        assert stoplist == {"van", "der"}
        assert isinstance(stoplist, set)

    def test_missing_casing_section_returns_empty_set(self, tmp_path):
        rules = load_rules(_write(tmp_path, MINIMAL_YAML))
        assert rules.surname_stoplist() == set()


# ---------------------------------------------------------------------------
# StyleRules.chapter_max
# ---------------------------------------------------------------------------


class TestChapterMax:
    def test_short_duration_uses_first_bucket(self, tmp_path):
        rules = _load_sample(tmp_path)
        assert rules.chapter_max(3) == 3

    def test_mid_duration_uses_second_bucket(self, tmp_path):
        rules = _load_sample(tmp_path)
        assert rules.chapter_max(10) == 5

    def test_long_duration_uses_null_catch_all(self, tmp_path):
        rules = _load_sample(tmp_path)
        assert rules.chapter_max(90) == 10

    def test_boundary_at_bucket_edge_uses_next_bucket(self, tmp_path):
        rules = _load_sample(tmp_path)
        # duration_min == lt is NOT "< lt", so 5 falls into the next bucket.
        assert rules.chapter_max(5) == 5


# ---------------------------------------------------------------------------
# StyleRules.program_rules
# ---------------------------------------------------------------------------


class TestProgramRules:
    def test_known_program(self, tmp_path):
        rules = _load_sample(tmp_path)
        assert rules.program_rules("Here & Now") == {"livestream_title_suffix": " | Here & Now"}

    def test_unknown_program_returns_empty_dict(self, tmp_path):
        rules = _load_sample(tmp_path)
        assert rules.program_rules("Nope") == {}

    def test_none_program_returns_empty_dict(self, tmp_path):
        rules = _load_sample(tmp_path)
        assert rules.program_rules(None) == {}


# ---------------------------------------------------------------------------
# types.RuleViolation
# ---------------------------------------------------------------------------


class TestRuleViolation:
    def test_to_flag_text_model_fixable(self):
        violation = RuleViolation(
            rule_id="limits.short_description.max",
            phase="seo",
            severity="error",
            message="Short description exceeds 90 characters",
        )
        assert (
            violation.to_flag_text()
            == "[style:limits.short_description.max] Short description exceeds 90 characters"
        )

    def test_to_flag_text_non_fixable(self):
        violation = RuleViolation(
            rule_id="voice.forbidden.cta",
            phase="seo",
            severity="error",
            message="Contains a viewer directive",
            model_fixable=False,
        )
        assert violation.to_flag_text() == "[style-nonfixable:voice.forbidden.cta] Contains a viewer directive"

    def test_to_dict_defaults(self):
        violation = RuleViolation(rule_id="x", phase="p", severity="warning", message="m")
        d = violation.to_dict()
        assert d["field"] is None
        assert d["span"] is None
        assert d["model_fixable"] is True

    def test_to_dict_span_tuple_becomes_list(self):
        violation = RuleViolation(
            rule_id="x", phase="p", severity="error", message="m", field="title", span=(5, 10)
        )
        d = violation.to_dict()
        assert d["span"] == [5, 10]
        assert isinstance(d["span"], list)

    def test_to_dict_is_json_serializable(self):
        violation = RuleViolation(
            rule_id="x", phase="p", severity="error", message="m", span=(0, 3)
        )
        assert json.dumps(violation.to_dict())


# ---------------------------------------------------------------------------
# types.AppliedFix
# ---------------------------------------------------------------------------


class TestAppliedFix:
    def test_to_dict_short_text_unchanged(self):
        fix = AppliedFix(rule_id="formatter.okay", before="okay", after="OK", count=3)
        d = fix.to_dict()
        assert d["before"] == "okay"
        assert d["after"] == "OK"
        assert d["count"] == 3

    def test_to_dict_caps_excerpts_at_200_chars(self):
        long_before = "a" * 500
        long_after = "b" * 500
        fix = AppliedFix(rule_id="x", before=long_before, after=long_after)
        d = fix.to_dict()
        assert len(d["before"]) == 200
        assert len(d["after"]) == 200

    def test_default_count_is_one(self):
        fix = AppliedFix(rule_id="x", before="a", after="b")
        assert fix.count == 1


# ---------------------------------------------------------------------------
# types.PhaseCheckResult
# ---------------------------------------------------------------------------


class TestPhaseCheckResult:
    def test_defaults(self):
        result = PhaseCheckResult(phase="seo")
        assert result.violations == []
        assert result.fixes == []
        assert result.parse_ok is True
        assert result.skipped is False
        assert result.error_flags == []

    def test_error_flags_filters_by_severity(self):
        error_v = RuleViolation(rule_id="a", phase="seo", severity="error", message="m1")
        warning_v = RuleViolation(rule_id="b", phase="seo", severity="warning", message="m2")
        result = PhaseCheckResult(phase="seo", violations=[error_v, warning_v])
        assert result.error_flags == [error_v]

    def test_to_dict_nests_violations_and_fixes(self):
        violation = RuleViolation(rule_id="a", phase="seo", severity="error", message="m")
        fix = AppliedFix(rule_id="b", before="x", after="y")
        result = PhaseCheckResult(phase="seo", violations=[violation], fixes=[fix])
        d = result.to_dict()
        assert d["violations"][0]["rule_id"] == "a"
        assert d["fixes"][0]["rule_id"] == "b"
        assert d["parse_ok"] is True
        assert d["skipped"] is False

    def test_to_dict_is_json_serializable(self):
        violation = RuleViolation(rule_id="a", phase="seo", severity="error", message="m", span=(1, 2))
        result = PhaseCheckResult(phase="seo", violations=[violation])
        assert json.dumps(result.to_dict())


# ---------------------------------------------------------------------------
# types.PreStageResult
# ---------------------------------------------------------------------------


class TestPreStageResult:
    def test_to_dict(self):
        result = PreStageResult(
            phase="seo",
            prompt_section="## House style\n- Keep it down-style.",
            data={"limits": {"title": {"max": 80}}},
        )
        d = result.to_dict()
        assert d["phase"] == "seo"
        assert d["prompt_section"] == "## House style\n- Keep it down-style."
        assert d["data"]["limits"]["title"]["max"] == 80

    def test_defaults_to_empty_data(self):
        result = PreStageResult(phase="seo", prompt_section="")
        assert result.data == {}

    def test_to_dict_is_json_serializable(self):
        result = PreStageResult(phase="seo", prompt_section="x", data={"a": 1})
        assert json.dumps(result.to_dict())


# ---------------------------------------------------------------------------
# types.PostStageResult
# ---------------------------------------------------------------------------


class TestPostStageResult:
    def test_to_dict_nests_check_result(self):
        violation = RuleViolation(rule_id="a", phase="seo", severity="error", message="m", span=(1, 2))
        check = PhaseCheckResult(phase="seo", violations=[violation])
        result = PostStageResult(phase="seo", normalized_output="Some text.", changed=True, check=check)
        d = result.to_dict()
        assert d["phase"] == "seo"
        assert d["normalized_output"] == "Some text."
        assert d["changed"] is True
        assert d["check"]["violations"][0]["span"] == [1, 2]

    def test_to_dict_is_json_serializable(self):
        check = PhaseCheckResult(phase="seo")
        result = PostStageResult(phase="seo", normalized_output="x", changed=False, check=check)
        assert json.dumps(result.to_dict())
