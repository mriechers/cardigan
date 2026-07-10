"""Tests for api.services.style_engine.lint.run_lint -- the deterministic
validator checklist (task 2a).

All rule data and sample phase output is synthetic -- built inline as
StyleRules(raw=...) plus small doc-builder helpers -- never depends on
config/house_style.yaml or prompts/*.md. Mirrors the fixture/helper style
of tests/test_style_stages.py and tests/test_style_scanner_limits.py.
"""

from __future__ import annotations

from api.services.style_engine.lint import run_lint
from api.services.style_engine.rules import StyleRules

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_PERMISSIVE_SPEAKER_PATTERN = r"^\*\*[A-Z][\w.'-]*(?:\s[A-Z][\w.'-]*)*:\*\*"


def _rules(
    title_max: int = 80,
    short_max: int = 90,
    long_max: int = 350,
    keyword_min: int = 15,
    keyword_max: int = 20,
    speaker_label_pattern: str | None = _PERMISSIVE_SPEAKER_PATTERN,
    no_honorifics: bool = True,
    review_notes_placement: str | None = "top",
) -> StyleRules:
    speaker_label: dict = {}
    if speaker_label_pattern:
        speaker_label = {"pattern": speaker_label_pattern, "no_honorifics": no_honorifics}

    review_notes: dict = {}
    if review_notes_placement is not None:
        review_notes = {"placement": review_notes_placement, "format": "html_comment", "tier": "flag"}

    raw = {
        "meta": {"version": 1},
        "limits": {
            "fields": {
                "title": {"max": title_max},
                "short_description": {"max": short_max},
                "long_description": {"max": long_max},
                "keywords": {"count": {"min": keyword_min, "max": keyword_max}},
            },
            "content_type_overrides": {},
        },
        "phases": {
            "formatter": {
                "speaker_label": speaker_label,
                "review_notes": review_notes,
            },
        },
    }
    return StyleRules(raw=raw)


def _seo_output(
    title: str = "Wisconsin budget deal reached in Madison",
    short: str = "Lawmakers reach a bipartisan budget agreement in Madison.",
    long: str = (
        "Wisconsin lawmakers reached a bipartisan state budget agreement after "
        "weeks of negotiation, Gov. Evers said Tuesday in Madison."
    ),
    tags: list[str] | None = None,
    include_tags_section: bool = True,
) -> str:
    if tags is None:
        tags = [f"keyword{i}" for i in range(1, 18)]  # 17 items -- within 15-20
    tags_section = ""
    if include_tags_section:
        tags_line = ", ".join(tags)
        tags_section = "## Tags (Platform-Specific)\n\n### YouTube Tags (15-20 recommended)\n\n```\n" f"{tags_line}\n```\n"
    return (
        "# SEO Report\n\n"
        "### Title (Final Recommendation)\n\n"
        "**Recommended:**\n"
        f"{title}\n\n"
        "---\n\n"
        "### Short Description (90 chars max)\n\n"
        "**Recommended:**\n"
        f"{short}\n\n"
        "---\n\n"
        "### Long Description (350 chars max)\n\n"
        "**Recommended:**\n"
        f"{long}\n\n"
        "---\n\n" + tags_section
    )


_DEFAULT_FORMATTER_BODY = (
    "**John Smith:**\n"
    "Today we discuss the state budget agreement reached in Madison this week.\n\n"
    "**Sarah Johnson:**\n"
    "That's right, and it comes after weeks of intense negotiation."
)

_FORMATTER_HEADER = (
    "# Formatted Transcript\n"
    "**Project:** 2WLI1234HD\n"
    "**Program:** Here & Now\n"
    "**Duration:** 10:00\n"
    "**Date Processed:** 2026-07-10\n"
)


def _formatter_output(
    body: str = _DEFAULT_FORMATTER_BODY,
    review_notes: str | None = None,
    review_notes_before_header: bool = False,
    status: str = "ready_for_editing",
) -> str:
    notes_block = f"{review_notes}\n\n" if review_notes else ""
    if review_notes_before_header:
        return f"{notes_block}{_FORMATTER_HEADER}\n---\n\n{body}\n\n---\n\n**Status:** {status}\n"
    return f"{_FORMATTER_HEADER}\n---\n\n{notes_block}{body}\n\n---\n\n**Status:** {status}\n"


def _analyst_output(text: str = "Analyst structural breakdown of the segment with themes, topics, and speaker notes.") -> str:
    return text


def _violations_for(result, phase: str, rule_id: str) -> list:
    return [v for v in result[phase].violations if v.rule_id == rule_id]


# ---------------------------------------------------------------------------
# run_lint -- top-level shape
# ---------------------------------------------------------------------------


class TestRunLintShape:
    def test_always_returns_all_three_canonical_phases(self):
        rules = _rules()
        result = run_lint({}, rules)
        assert set(result.keys()) == {"analyst", "formatter", "seo"}

    def test_ignores_unknown_phases_in_context(self):
        rules = _rules()
        result = run_lint({"timestamp_output": "irrelevant"}, rules)
        assert set(result.keys()) == {"analyst", "formatter", "seo"}


# ---------------------------------------------------------------------------
# lint.output_missing -- all 3 phases
# ---------------------------------------------------------------------------


class TestOutputMissing:
    def test_absent_key_fires_for_all_three_phases(self):
        rules = _rules()
        result = run_lint({}, rules)
        for phase in ("analyst", "formatter", "seo"):
            violations = _violations_for(result, phase, "lint.output_missing")
            assert len(violations) == 1
            assert violations[0].model_fixable is False
            assert violations[0].severity == "error"

    def test_comment_only_content_fires(self):
        rules = _rules()
        context = {
            "analyst_output": "<!-- nothing but a provenance comment here, no real analyst content at all -->"
        }
        result = run_lint(context, rules)
        violations = _violations_for(result, "analyst", "lint.output_missing")
        assert len(violations) == 1

    def test_substantial_content_does_not_fire(self):
        rules = _rules()
        context = {"analyst_output": _analyst_output("x" * 60)}
        result = run_lint(context, rules)
        assert _violations_for(result, "analyst", "lint.output_missing") == []

    def test_none_value_fires(self):
        rules = _rules()
        result = run_lint({"seo_output": None}, rules)
        assert len(_violations_for(result, "seo", "lint.output_missing")) == 1


# ---------------------------------------------------------------------------
# lint.placeholder_text -- all 3 phases
# ---------------------------------------------------------------------------


class TestPlaceholderText:
    def test_media_id_literal_fires(self):
        rules = _rules()
        context = {"analyst_output": _analyst_output("Project {media_id} covers the state budget debate in detail.")}
        result = run_lint(context, rules)
        violations = _violations_for(result, "analyst", "lint.placeholder_text")
        assert len(violations) == 1
        assert violations[0].model_fixable is True
        assert violations[0].severity == "error"

    def test_today_literal_fires(self):
        rules = _rules()
        context = {"formatter_output": _formatter_output().replace("2026-07-10", "{TODAY'S DATE}")}
        result = run_lint(context, rules)
        assert len(_violations_for(result, "formatter", "lint.placeholder_text")) == 1

    def test_insert_literal_fires(self):
        rules = _rules()
        context = {"analyst_output": _analyst_output("[INSERT episode summary here] plus additional analyst notes text.")}
        result = run_lint(context, rules)
        assert len(_violations_for(result, "analyst", "lint.placeholder_text")) == 1

    def test_model_name_literal_fires(self):
        rules = _rules()
        context = {"analyst_output": _analyst_output("Generated by {model name} during the analyst structural pass today.")}
        result = run_lint(context, rules)
        assert len(_violations_for(result, "analyst", "lint.placeholder_text")) == 1

    def test_recommended_empty_value_fires(self):
        rules = _rules()
        seo = _seo_output(title="")  # blank line follows **Recommended:**
        result = run_lint({"seo_output": seo}, rules)
        violations = _violations_for(result, "seo", "lint.placeholder_text")
        assert len(violations) == 1
        assert violations[0].model_fixable is True

    def test_recommended_bracket_placeholder_fires(self):
        rules = _rules()
        seo = _seo_output(title="[55-60 character title summarizing the episode...]")
        result = run_lint({"seo_output": seo}, rules)
        assert len(_violations_for(result, "seo", "lint.placeholder_text")) == 1

    def test_normal_content_does_not_fire(self):
        rules = _rules()
        result = run_lint({"seo_output": _seo_output()}, rules)
        assert _violations_for(result, "seo", "lint.placeholder_text") == []


# ---------------------------------------------------------------------------
# lint.seo.title_over_limit / short_over_limit / long_over_limit
# ---------------------------------------------------------------------------


class TestSeoOverLimit:
    def test_title_over_limit_uses_rules_not_hardcoded(self):
        # title_max=20 -- proves the bound comes from StyleRules, not 80.
        rules = _rules(title_max=20)
        title = "This title is deliberately much longer than twenty characters"
        assert len(title) > 20
        result = run_lint({"seo_output": _seo_output(title=title)}, rules)
        violations = _violations_for(result, "seo", "lint.seo.title_over_limit")
        assert len(violations) == 1
        assert violations[0].severity == "error"
        assert violations[0].model_fixable is True

    def test_same_title_under_default_80_limit_does_not_fire(self):
        rules = _rules(title_max=80)
        title = "This title is deliberately much longer than twenty characters"
        assert len(title) <= 80
        result = run_lint({"seo_output": _seo_output(title=title)}, rules)
        assert _violations_for(result, "seo", "lint.seo.title_over_limit") == []

    def test_short_description_over_limit(self):
        rules = _rules(short_max=20)
        short = "This short description exceeds twenty characters easily."
        result = run_lint({"seo_output": _seo_output(short=short)}, rules)
        violations = _violations_for(result, "seo", "lint.seo.short_over_limit")
        assert len(violations) == 1
        assert violations[0].model_fixable is True

    def test_short_description_within_limit_does_not_fire(self):
        rules = _rules(short_max=90)
        result = run_lint({"seo_output": _seo_output()}, rules)
        assert _violations_for(result, "seo", "lint.seo.short_over_limit") == []

    def test_long_description_over_limit(self):
        rules = _rules(long_max=30)
        long_desc = "This long description is deliberately far longer than thirty characters allow."
        result = run_lint({"seo_output": _seo_output(long=long_desc)}, rules)
        violations = _violations_for(result, "seo", "lint.seo.long_over_limit")
        assert len(violations) == 1
        assert violations[0].model_fixable is True

    def test_long_description_within_limit_does_not_fire(self):
        rules = _rules(long_max=350)
        result = run_lint({"seo_output": _seo_output()}, rules)
        assert _violations_for(result, "seo", "lint.seo.long_over_limit") == []


# ---------------------------------------------------------------------------
# lint.seo.keywords_count (warning)
# ---------------------------------------------------------------------------


class TestSeoKeywordsCount:
    def test_within_bounds_no_violation(self):
        rules = _rules(keyword_min=15, keyword_max=20)
        result = run_lint({"seo_output": _seo_output(tags=[f"kw{i}" for i in range(17)])}, rules)
        assert _violations_for(result, "seo", "lint.seo.keywords_count") == []

    def test_below_min_fires_warning(self):
        rules = _rules(keyword_min=15, keyword_max=20)
        result = run_lint({"seo_output": _seo_output(tags=["only", "three", "tags"])}, rules)
        violations = _violations_for(result, "seo", "lint.seo.keywords_count")
        assert len(violations) == 1
        assert violations[0].severity == "warning"
        assert violations[0].model_fixable is True

    def test_above_max_fires_warning(self):
        rules = _rules(keyword_min=5, keyword_max=10)
        result = run_lint({"seo_output": _seo_output(tags=[f"kw{i}" for i in range(20)])}, rules)
        violations = _violations_for(result, "seo", "lint.seo.keywords_count")
        assert len(violations) == 1
        assert violations[0].severity == "warning"

    def test_unidentifiable_section_skips_silently(self):
        rules = _rules(keyword_min=15, keyword_max=20)
        seo = _seo_output(include_tags_section=False)
        result = run_lint({"seo_output": seo}, rules)
        assert _violations_for(result, "seo", "lint.seo.keywords_count") == []


# ---------------------------------------------------------------------------
# lint.formatter.review_notes_in_body
# ---------------------------------------------------------------------------


class TestReviewNotesInBody:
    def test_review_notes_after_first_hr_fires(self):
        rules = _rules(review_notes_placement="top")
        formatter = _formatter_output(review_notes="<!-- REVIEW NOTES: speaker unclear at 2:30 -->")
        result = run_lint({"formatter_output": formatter}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.review_notes_in_body")
        assert len(violations) == 1
        assert violations[0].model_fixable is False
        assert violations[0].severity == "error"

    def test_review_notes_before_first_hr_does_not_fire(self):
        rules = _rules(review_notes_placement="top")
        formatter = _formatter_output(
            review_notes="<!-- REVIEW NOTES: speaker unclear at 2:30 -->",
            review_notes_before_header=True,
        )
        result = run_lint({"formatter_output": formatter}, rules)
        assert _violations_for(result, "formatter", "lint.formatter.review_notes_in_body") == []

    def test_needs_review_marker_after_first_hr_fires(self):
        rules = _rules(review_notes_placement="top")
        body = _DEFAULT_FORMATTER_BODY + "\n\n**Status:** needs_review\nNEEDS_REVIEW: speaker unclear"
        formatter = _formatter_output(body=body)
        result = run_lint({"formatter_output": formatter}, rules)
        assert len(_violations_for(result, "formatter", "lint.formatter.review_notes_in_body")) == 1

    def test_review_notes_heading_after_first_hr_fires(self):
        rules = _rules(review_notes_placement="top")
        body = _DEFAULT_FORMATTER_BODY + "\n\n## Review Notes\n- Speaker unclear at 2:30"
        formatter = _formatter_output(body=body)
        result = run_lint({"formatter_output": formatter}, rules)
        assert len(_violations_for(result, "formatter", "lint.formatter.review_notes_in_body")) == 1

    def test_no_review_notes_present_does_not_fire(self):
        rules = _rules(review_notes_placement="top")
        result = run_lint({"formatter_output": _formatter_output()}, rules)
        assert _violations_for(result, "formatter", "lint.formatter.review_notes_in_body") == []

    def test_config_placement_not_top_skips_check(self):
        rules = _rules(review_notes_placement="bottom")
        formatter = _formatter_output(review_notes="<!-- REVIEW NOTES: speaker unclear at 2:30 -->")
        result = run_lint({"formatter_output": formatter}, rules)
        assert _violations_for(result, "formatter", "lint.formatter.review_notes_in_body") == []


# ---------------------------------------------------------------------------
# lint.formatter.speaker_label_inconsistent (warning)
# ---------------------------------------------------------------------------


class TestSpeakerLabelInconsistent:
    def test_single_word_label_fires(self):
        rules = _rules()
        body = "**Sarah:**\nDialogue text here that is long enough.\n\n**John Smith:**\nMore dialogue text."
        result = run_lint({"formatter_output": _formatter_output(body=body)}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.speaker_label_inconsistent")
        assert any("Sarah" in v.message and "single word" in v.message for v in violations)
        assert all(v.model_fixable is True and v.severity == "warning" for v in violations)

    def test_two_word_labels_do_not_fire_single_word_check(self):
        rules = _rules()
        result = run_lint({"formatter_output": _formatter_output()}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.speaker_label_inconsistent")
        assert not any("single word" in v.message for v in violations)

    def test_honorific_label_fires(self):
        rules = _rules(no_honorifics=True)
        body = "**Dr. Sarah Johnson:**\nDialogue text that is long enough to matter.\n\n**John Smith:**\nMore text."
        result = run_lint({"formatter_output": _formatter_output(body=body)}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.speaker_label_inconsistent")
        assert any("honorific" in v.message for v in violations)

    def test_no_honorifics_false_does_not_flag_honorific(self):
        rules = _rules(no_honorifics=False)
        body = "**Dr. Sarah Johnson:**\nDialogue text that is long enough to matter.\n\n**John Smith:**\nMore text."
        result = run_lint({"formatter_output": _formatter_output(body=body)}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.speaker_label_inconsistent")
        assert not any("honorific" in v.message for v in violations)

    def test_superset_label_pair_fires(self):
        rules = _rules()
        body = (
            "**Sarah Johnson:**\nDialogue text that is long enough to matter here.\n\n"
            "**Dr. Sarah Johnson:**\nMore dialogue text that is also long enough."
        )
        result = run_lint({"formatter_output": _formatter_output(body=body)}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.speaker_label_inconsistent")
        assert any("labeled inconsistently" in v.message for v in violations)

    def test_identical_labels_do_not_fire_superset_check(self):
        rules = _rules()
        body = (
            "**Sarah Johnson:**\nFirst turn of dialogue that is long enough to matter here.\n\n"
            "**John Smith:**\nA reply.\n\n"
            "**Sarah Johnson:**\nSecond turn from the same speaker, same exact label as before."
        )
        result = run_lint({"formatter_output": _formatter_output(body=body)}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.speaker_label_inconsistent")
        assert not any("labeled inconsistently" in v.message for v in violations)

    def test_no_pattern_configured_returns_no_violations(self):
        rules = _rules(speaker_label_pattern=None)
        body = "**Sarah:**\nDialogue text here that is long enough to matter for this test."
        result = run_lint({"formatter_output": _formatter_output(body=body)}, rules)
        assert _violations_for(result, "formatter", "lint.formatter.speaker_label_inconsistent") == []

    def test_header_fields_do_not_leak_into_label_collection(self):
        # _body_region scoping (strictly between the two "---" rules) must
        # keep "**Project:**" / "**Date Processed:**"-style header fields
        # out of the candidate pool -- otherwise "Project" (a single bold
        # word) would spuriously trip the single-word check every time.
        rules = _rules()
        result = run_lint({"formatter_output": _formatter_output()}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.speaker_label_inconsistent")
        assert not any("Project" in v.message for v in violations)
        assert not any("Date Processed" in v.message for v in violations)
        assert not any("Program" in v.message for v in violations)
        assert not any("Duration" in v.message for v in violations)

    def test_field_label_stoplist_skips_note_annotation(self):
        # A "**Note:**" inline annotation in the body shares the loose
        # bold-colon shape with a real speaker label but is a known
        # non-name field -- must be skipped at collection, not flagged as
        # a malformed single-word speaker label.
        body = (
            "**John Smith:**\nDialogue text that is long enough to matter here.\n\n"
            "**Note:** inline annotation about the edit.\n\n"
            "**Sarah Johnson:**\nMore dialogue text that is also long enough."
        )
        rules = _rules()
        result = run_lint({"formatter_output": _formatter_output(body=body)}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.speaker_label_inconsistent")
        assert not any("Note" in v.message for v in violations)

    def test_field_label_stoplist_does_not_suppress_real_single_word_label(self):
        # The stoplist must be narrow -- a genuine single-word speaker label
        # not on the stoplist still fires.
        body = "**Sarah:**\nDialogue text here that is long enough.\n\n**John Smith:**\nMore dialogue text."
        rules = _rules()
        result = run_lint({"formatter_output": _formatter_output(body=body)}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.speaker_label_inconsistent")
        assert any("Sarah" in v.message and "single word" in v.message for v in violations)


# ---------------------------------------------------------------------------
# lint.formatter.speaker_label_inconsistent -- liveness against the REAL
# house_style.yaml strict pattern (2+ words required). Regression coverage
# for the bug where candidate labels were COLLECTED using the configured
# pattern itself: since the real config/house_style.yaml pattern requires
# 2+ words, a malformed single-word label like "**Sarah:**" would never
# enter the candidate pool at all, and the single-word branch could never
# fire in production. Collection must use a loose built-in pattern;
# classification (single-word / honorific / superset) runs over whatever
# that loose pattern finds, independent of how strict the configured
# pattern is.
# ---------------------------------------------------------------------------

# Copied verbatim from config/house_style.yaml's phases.formatter.speaker_label.pattern.
_REAL_STRICT_SPEAKER_PATTERN = r"^\*\*[A-Z][\w.'-]+(?: [A-Z][\w.'-]+)+:\*\*"


class TestSpeakerLabelInconsistentLiveAgainstRealPattern:
    def test_single_word_label_fires_with_real_strict_pattern_configured(self):
        rules = _rules(speaker_label_pattern=_REAL_STRICT_SPEAKER_PATTERN)
        body = (
            "**Sarah:**  \nDialogue text here that is long enough to matter for this test.\n\n"
            "**John Smith:**\nMore dialogue text that is also long enough."
        )
        result = run_lint({"formatter_output": _formatter_output(body=body)}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.speaker_label_inconsistent")
        assert any("Sarah" in v.message and "single word" in v.message for v in violations)
        assert all(v.model_fixable is True and v.severity == "warning" for v in violations)

    def test_honorific_label_fires_with_real_strict_pattern_configured(self):
        rules = _rules(speaker_label_pattern=_REAL_STRICT_SPEAKER_PATTERN, no_honorifics=True)
        body = (
            "**Dr. Sarah Johnson:**\nDialogue text that is long enough to matter here.\n\n"
            "**John Smith:**\nMore dialogue text that is also long enough."
        )
        result = run_lint({"formatter_output": _formatter_output(body=body)}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.speaker_label_inconsistent")
        assert any("honorific" in v.message for v in violations)

    def test_header_fields_do_not_leak_with_real_strict_pattern_configured(self):
        rules = _rules(speaker_label_pattern=_REAL_STRICT_SPEAKER_PATTERN)
        result = run_lint({"formatter_output": _formatter_output()}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.speaker_label_inconsistent")
        assert not any("Date Processed" in v.message for v in violations)


# ---------------------------------------------------------------------------
# lint.formatter.content_past_duration (warning)
# ---------------------------------------------------------------------------


class TestContentPastDuration:
    def test_within_duration_does_not_fire(self):
        rules = _rules()
        body = "**John Smith:**\nThe segment wraps up around (9:30) in the recording, right on schedule."
        context = {"formatter_output": _formatter_output(body=body), "duration_minutes": 10}
        result = run_lint(context, rules)
        assert _violations_for(result, "formatter", "lint.formatter.content_past_duration") == []

    def test_exactly_at_slack_boundary_does_not_fire(self):
        # duration_minutes=10 -> limit = 600 + 60 = 660s = 11:00 exactly.
        rules = _rules()
        body = "**John Smith:**\nThe segment closes right at (11:00) in the recording, exactly on the slack line."
        context = {"formatter_output": _formatter_output(body=body), "duration_minutes": 10}
        result = run_lint(context, rules)
        assert _violations_for(result, "formatter", "lint.formatter.content_past_duration") == []

    def test_one_second_past_slack_boundary_fires(self):
        rules = _rules()
        body = "**John Smith:**\nThe segment somehow references (11:01) which is one second past the slack line."
        context = {"formatter_output": _formatter_output(body=body), "duration_minutes": 10}
        result = run_lint(context, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.content_past_duration")
        assert len(violations) == 1
        assert violations[0].severity == "warning"
        assert violations[0].model_fixable is False

    def test_duration_missing_skips_check(self):
        rules = _rules()
        body = "**John Smith:**\nThe segment somehow references (99:59) which would otherwise be way over."
        context = {"formatter_output": _formatter_output(body=body)}
        result = run_lint(context, rules)
        assert _violations_for(result, "formatter", "lint.formatter.content_past_duration") == []

    def test_duration_zero_skips_check(self):
        rules = _rules()
        body = "**John Smith:**\nThe segment somehow references (99:59) which would otherwise be way over."
        context = {"formatter_output": _formatter_output(body=body), "duration_minutes": 0}
        result = run_lint(context, rules)
        assert _violations_for(result, "formatter", "lint.formatter.content_past_duration") == []


# ---------------------------------------------------------------------------
# lint.formatter.truncation_suspect (warning)
# ---------------------------------------------------------------------------


class TestTruncationSuspect:
    def test_missing_terminal_punctuation_fires(self):
        rules = _rules()
        body = "**John Smith:**\nThe budget debate continued late into the evening and"
        result = run_lint({"formatter_output": _formatter_output(body=body)}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.truncation_suspect")
        assert len(violations) == 1
        assert violations[0].severity == "warning"
        assert violations[0].model_fixable is False

    def test_terminal_punctuation_present_does_not_fire(self):
        rules = _rules()
        result = run_lint({"formatter_output": _formatter_output()}, rules)
        assert _violations_for(result, "formatter", "lint.formatter.truncation_suspect") == []

    def test_ignores_status_footer_when_finding_last_line(self):
        rules = _rules()
        # Body ends cleanly; the Status footer line ("**Status:** ready_for_editing")
        # has no terminal punctuation but must not be treated as the last prose line.
        result = run_lint({"formatter_output": _formatter_output(status="ready_for_editing")}, rules)
        assert _violations_for(result, "formatter", "lint.formatter.truncation_suspect") == []

    def test_ignores_trailing_html_comment_when_finding_last_line(self):
        rules = _rules()
        body = _DEFAULT_FORMATTER_BODY + "\n\n<!-- trailing note with no punctuation at all -->"
        result = run_lint({"formatter_output": _formatter_output(body=body)}, rules)
        assert _violations_for(result, "formatter", "lint.formatter.truncation_suspect") == []

    def test_cutoff_hidden_behind_trailing_comment_still_fires(self):
        rules = _rules()
        body = "**John Smith:**\nThe budget debate continued late into the evening and\n\n<!-- trailing comment -->"
        result = run_lint({"formatter_output": _formatter_output(body=body)}, rules)
        assert len(_violations_for(result, "formatter", "lint.formatter.truncation_suspect")) == 1


# ---------------------------------------------------------------------------
# model_fixable contract -- explicit per rule_id, since escalation routing
# depends on these being correct.
# ---------------------------------------------------------------------------


class TestModelFixableContract:
    def test_output_missing_is_not_model_fixable(self):
        rules = _rules()
        result = run_lint({}, rules)
        for phase in ("analyst", "formatter", "seo"):
            for v in _violations_for(result, phase, "lint.output_missing"):
                assert v.model_fixable is False

    def test_placeholder_text_is_model_fixable(self):
        rules = _rules()
        context = {"analyst_output": _analyst_output("Contains {media_id} literally in analyst prose here today.")}
        result = run_lint(context, rules)
        for v in _violations_for(result, "analyst", "lint.placeholder_text"):
            assert v.model_fixable is True

    def test_seo_over_limit_checks_are_model_fixable(self):
        rules = _rules(title_max=5, short_max=5, long_max=5)
        result = run_lint({"seo_output": _seo_output()}, rules)
        for rule_id in ("lint.seo.title_over_limit", "lint.seo.short_over_limit", "lint.seo.long_over_limit"):
            violations = _violations_for(result, "seo", rule_id)
            assert violations, f"expected {rule_id} to fire for this fixture"
            for v in violations:
                assert v.model_fixable is True

    def test_seo_keywords_count_is_model_fixable(self):
        rules = _rules(keyword_min=15, keyword_max=20)
        result = run_lint({"seo_output": _seo_output(tags=["one", "two"])}, rules)
        violations = _violations_for(result, "seo", "lint.seo.keywords_count")
        assert violations
        for v in violations:
            assert v.model_fixable is True

    def test_review_notes_in_body_is_not_model_fixable(self):
        rules = _rules()
        formatter = _formatter_output(review_notes="<!-- REVIEW NOTES: unresolved -->")
        result = run_lint({"formatter_output": formatter}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.review_notes_in_body")
        assert violations
        for v in violations:
            assert v.model_fixable is False

    def test_speaker_label_inconsistent_is_model_fixable(self):
        rules = _rules()
        body = "**Sarah:**\nDialogue text here that is long enough to matter for this test."
        result = run_lint({"formatter_output": _formatter_output(body=body)}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.speaker_label_inconsistent")
        assert violations
        for v in violations:
            assert v.model_fixable is True

    def test_content_past_duration_is_not_model_fixable(self):
        rules = _rules()
        body = "**John Smith:**\nReferences a time far past the end (99:59) of the recording here."
        context = {"formatter_output": _formatter_output(body=body), "duration_minutes": 10}
        result = run_lint(context, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.content_past_duration")
        assert violations
        for v in violations:
            assert v.model_fixable is False

    def test_truncation_suspect_is_not_model_fixable(self):
        rules = _rules()
        body = "**John Smith:**\nThe budget debate continued late into the evening and"
        result = run_lint({"formatter_output": _formatter_output(body=body)}, rules)
        violations = _violations_for(result, "formatter", "lint.formatter.truncation_suspect")
        assert violations
        for v in violations:
            assert v.model_fixable is False
