"""Tests for scripts/lint_agreement_study.py's pure comparison logic (task 2c).

Covers classification (classify_flag), matrix building (compare_phase,
build_job_matrix, aggregate_matrices), and the two small orchestration-adjacent
pure helpers (select_eligible_jobs, build_context). All synthetic -- no
network, no filesystem, no dependency on config/house_style.yaml or real
production data. Network/fetch functions (fetch_queue_cached,
fetch_job_bundle, main) are deliberately NOT covered here per the brief.
"""

from __future__ import annotations

from api.services.style_engine.types import PhaseCheckResult, RuleViolation
from scripts.lint_agreement_study import (
    CANONICAL_PHASES,
    aggregate_matrices,
    build_context,
    build_job_matrix,
    classify_flag,
    compare_phase,
    select_eligible_jobs,
)

# ---------------------------------------------------------------------------
# classify_flag
# ---------------------------------------------------------------------------


def test_classify_flag_output_missing():
    result = classify_flag("Output missing or empty for this phase")
    assert "output_missing" in result.categories
    assert "lint.output_missing" in result.rule_ids
    assert result.is_deterministic


def test_classify_flag_placeholder_text():
    result = classify_flag("Report contains placeholder text left unfilled")
    assert "placeholder_text" in result.categories
    assert "lint.placeholder_text" in result.rule_ids


def test_classify_flag_review_notes():
    result = classify_flag("Review notes appear in transcript body (HTML comment)")
    assert "review_notes" in result.categories
    assert "lint.formatter.review_notes_in_body" in result.rule_ids


def test_classify_flag_truncation():
    result = classify_flag("Transcript ends abruptly mid-sentence at 00:10:52")
    assert "truncation" in result.categories
    assert "lint.formatter.truncation_suspect" in result.rule_ids


def test_classify_flag_content_past_duration():
    result = classify_flag("Timecode content appears after the episode ends")
    assert "content_past_duration" in result.categories
    assert "lint.formatter.content_past_duration" in result.rule_ids


def test_classify_flag_speaker_label_format_included():
    result = classify_flag("Speaker label 'Sarah' is a single-word label, expected first + last name")
    assert "speaker_label_format" in result.categories
    assert "lint.formatter.speaker_label_inconsistent" in result.rule_ids


def test_classify_flag_speaker_attribution_excluded_as_semantic():
    """Content-judgment attribution issues must NOT land in speaker_label_format
    even though they mention "speaker" -- lint can't judge who said what."""
    result = classify_flag(
        "Speaker attribution ambiguity: 'Hendrickson' paragraph attribution unclear -- "
        "may represent host or second speaker interjection"
    )
    assert "speaker_label_format" not in result.categories
    assert not result.is_deterministic


def test_classify_flag_speaker_misattribution_excluded():
    result = classify_flag(
        "Speaker misattribution in word-frequency game section: lines attributed to the wrong speaker"
    )
    assert "speaker_label_format" not in result.categories


def test_classify_flag_char_limit_title():
    result = classify_flag("Recommended title is 66 characters, exceeding the 60-character limit")
    assert "char_limit" in result.categories
    assert "lint.seo.title_over_limit" in result.rule_ids


def test_classify_flag_char_limit_short_description():
    result = classify_flag("Short description exceeds 160-character limit: provided at 193 characters")
    assert "char_limit" in result.categories
    assert "lint.seo.short_over_limit" in result.rule_ids


def test_classify_flag_char_limit_long_description():
    result = classify_flag("Long description exceeds 300-character limit at 398 characters")
    assert "char_limit" in result.categories
    assert "lint.seo.long_over_limit" in result.rule_ids


def test_classify_flag_char_limit_requires_violation_word():
    """Mentioning a character count without an exceed/over/too-long word is not
    a char_limit flag -- e.g. a redundancy complaint that happens to cite a
    character count in passing."""
    result = classify_flag("Long description is 298 characters but contains redundant elements repeated twice")
    assert "char_limit" not in result.categories
    assert not result.is_deterministic


def test_classify_flag_char_limit_no_field_name_has_no_rule_id():
    result = classify_flag("Some field exceeds its 100-character limit")
    assert "char_limit" in result.categories
    # No "title"/"short description"/"long description" substring -> no rule_id.
    assert result.rule_ids == ()


def test_classify_flag_semantic_fallback():
    result = classify_flag("SEMRush validation incomplete -- awaiting screenshot or API export")
    assert result.categories == ()
    assert not result.is_deterministic


def test_classify_flag_can_match_multiple_categories():
    result = classify_flag("Review notes appear in transcript body and the transcript ends abruptly mid-sentence")
    assert "review_notes" in result.categories
    assert "truncation" in result.categories


# ---------------------------------------------------------------------------
# compare_phase
# ---------------------------------------------------------------------------


def _violation(rule_id: str, message: str = "msg", severity: str = "error") -> RuleViolation:
    return RuleViolation(rule_id=rule_id, phase="formatter", severity=severity, message=message)


def test_compare_phase_both_caught():
    lint_result = PhaseCheckResult(phase="formatter", violations=[_violation("lint.formatter.review_notes_in_body")])
    llm_flags = ["Review notes appear in transcript body"]

    comp = compare_phase("formatter", lint_result, llm_flags)

    assert len(comp.both_caught) == 1
    assert comp.both_caught[0]["rule_id"] == "lint.formatter.review_notes_in_body"
    assert comp.lint_only == []
    assert comp.llm_only_deterministic == []


def test_compare_phase_lint_only():
    lint_result = PhaseCheckResult(phase="formatter", violations=[_violation("lint.formatter.truncation_suspect")])
    comp = compare_phase("formatter", lint_result, llm_flags=[])

    assert comp.both_caught == []
    assert len(comp.lint_only) == 1
    assert comp.lint_only[0]["rule_id"] == "lint.formatter.truncation_suspect"


def test_compare_phase_llm_only_deterministic():
    lint_result = PhaseCheckResult(phase="formatter", violations=[])
    llm_flags = ["Review notes appear in transcript body"]

    comp = compare_phase("formatter", lint_result, llm_flags)

    assert comp.both_caught == []
    assert len(comp.llm_only_deterministic) == 1
    assert comp.llm_only_deterministic[0]["text"] == "Review notes appear in transcript body"


def test_compare_phase_semantic_flags_never_counted_as_matches():
    lint_result = PhaseCheckResult(phase="seo", violations=[])
    llm_flags = ["SEMRush validation incomplete -- no live data provided"]

    comp = compare_phase("seo", lint_result, llm_flags)

    assert comp.both_caught == []
    assert comp.llm_only_deterministic == []
    assert comp.llm_flags_semantic == ["SEMRush validation incomplete -- no live data provided"]


def test_compare_phase_extra_lint_violations_of_same_rule_id_stay_lint_only():
    """Two lint violations, one LLM flag naming that category: one claimed
    (both_caught), the other stays lint_only -- extras aren't silently absorbed."""
    lint_result = PhaseCheckResult(
        phase="formatter",
        violations=[
            _violation("lint.formatter.speaker_label_inconsistent", message="Sarah is single-word"),
            _violation("lint.formatter.speaker_label_inconsistent", message="Bob is single-word"),
        ],
    )
    llm_flags = ["Speaker label is a single-word label"]

    comp = compare_phase("formatter", lint_result, llm_flags)

    assert len(comp.both_caught) == 1
    assert len(comp.lint_only) == 1


def test_compare_phase_multiple_lint_violations_multiple_llm_flags_pair_up():
    lint_result = PhaseCheckResult(
        phase="seo",
        violations=[
            _violation("lint.seo.title_over_limit", message="title 90 chars"),
            _violation("lint.seo.short_over_limit", message="short 200 chars"),
        ],
    )
    llm_flags = [
        "Recommended title is 90 characters, exceeding the 60-character limit",
        "Short description exceeds 160-character limit at 200 characters",
    ]

    comp = compare_phase("seo", lint_result, llm_flags)

    assert len(comp.both_caught) == 2
    assert comp.lint_only == []
    assert comp.llm_only_deterministic == []


# ---------------------------------------------------------------------------
# build_job_matrix
# ---------------------------------------------------------------------------


def _lint_results(**phase_violations: list[RuleViolation]) -> dict[str, PhaseCheckResult]:
    return {
        phase: PhaseCheckResult(phase=phase, violations=phase_violations.get(phase, [])) for phase in CANONICAL_PHASES
    }


def test_build_job_matrix_null_validation_result_is_all_lint_only():
    lint_results = _lint_results(formatter=[_violation("lint.formatter.truncation_suspect")])

    jm = build_job_matrix(
        job_id=42,
        status="completed",
        content_type="full",
        duration_minutes=12.5,
        lint_results=lint_results,
        validation_result=None,
    )

    assert jm.validation_result_present is False
    assert len(jm.phases["formatter"].lint_only) == 1
    for phase in CANONICAL_PHASES:
        assert jm.phases[phase].both_caught == []
        assert jm.phases[phase].llm_only_deterministic == []


def test_build_job_matrix_missing_phase_key_in_validation_result_treated_as_no_flags():
    lint_results = _lint_results()
    validation_result = {
        "phase_results": {"formatter": {"status": "fail", "flags": ["Review notes in body"]}},
        "overall": "fail",
    }

    jm = build_job_matrix(
        job_id=1,
        status="completed",
        content_type="full",
        duration_minutes=5.0,
        lint_results=lint_results,
        validation_result=validation_result,
    )

    # "analyst" and "seo" absent from phase_results -> zero flags, no crash.
    assert jm.phases["analyst"].llm_flags_deterministic == []
    assert jm.phases["seo"].llm_flags_deterministic == []
    assert len(jm.phases["formatter"].llm_only_deterministic) == 1


def test_build_job_matrix_to_dict_roundtrips_json_shape():
    import json

    lint_results = _lint_results(seo=[_violation("lint.seo.title_over_limit")])
    validation_result = {"phase_results": {"seo": {"status": "fail", "flags": []}}, "overall": "fail"}
    jm = build_job_matrix(1, "completed", "full", 10.0, lint_results, validation_result)

    # Must be JSON-serializable without error.
    json.dumps(jm.to_dict())


# ---------------------------------------------------------------------------
# aggregate_matrices
# ---------------------------------------------------------------------------


def test_aggregate_matrices_sums_across_jobs_and_phases():
    lint_results_1 = _lint_results(formatter=[_violation("lint.formatter.review_notes_in_body")])
    vr_1 = {
        "phase_results": {"formatter": {"status": "fail", "flags": ["Review notes appear in transcript body"]}},
        "overall": "fail",
    }
    jm1 = build_job_matrix(1, "completed", "full", 10.0, lint_results_1, vr_1)

    lint_results_2 = _lint_results(seo=[_violation("lint.seo.title_over_limit")])
    vr_2 = {"phase_results": {"seo": {"status": "fail", "flags": []}}, "overall": "fail"}
    jm2 = build_job_matrix(2, "completed", "full", 5.0, lint_results_2, vr_2)

    agg = aggregate_matrices([jm1, jm2])

    assert agg["totals"]["both_caught"] == 1
    assert agg["totals"]["lint_only"] == 1
    assert agg["by_phase"]["formatter"]["both_caught"] == 1
    assert agg["by_phase"]["seo"]["lint_only"] == 1
    assert agg["by_category"]["review_notes"]["both_caught"] == 1


def test_aggregate_matrices_empty_list():
    agg = aggregate_matrices([])
    assert agg["totals"] == {"both_caught": 0, "lint_only": 0, "llm_only_deterministic": 0, "llm_semantic": 0}
    assert agg["by_category"] == {}


# ---------------------------------------------------------------------------
# select_eligible_jobs
# ---------------------------------------------------------------------------


def _job(job_id: int, status: str, **outputs: str | None) -> dict:
    return {"id": job_id, "status": status, "outputs": outputs}


def test_select_eligible_jobs_includes_completed():
    jobs = [_job(1, "completed", analysis="a.md", formatted_transcript="f.md", seo_metadata="s.md")]
    assert select_eligible_jobs(jobs) == [1]


def test_select_eligible_jobs_includes_paused_with_all_outputs():
    jobs = [_job(2, "paused", analysis="a.md", formatted_transcript="f.md", seo_metadata="s.md")]
    assert select_eligible_jobs(jobs) == [2]


def test_select_eligible_jobs_excludes_paused_missing_outputs():
    jobs = [_job(3, "paused", analysis="a.md", formatted_transcript=None, seo_metadata="s.md")]
    assert select_eligible_jobs(jobs) == []


def test_select_eligible_jobs_excludes_failed_without_outputs():
    jobs = [_job(4, "failed")]
    assert select_eligible_jobs(jobs) == []


def test_select_eligible_jobs_includes_completed_even_if_outputs_dict_missing():
    """Defensive: a completed job with a missing/null outputs block is still
    included by status alone (has_all_outputs is just False, not an error)."""
    jobs = [{"id": 5, "status": "completed", "outputs": None}]
    assert select_eligible_jobs(jobs) == [5]


def test_select_eligible_jobs_sorted_and_deduped_order():
    jobs = [
        _job(9, "completed", analysis="a", formatted_transcript="f", seo_metadata="s"),
        _job(2, "completed", analysis="a", formatted_transcript="f", seo_metadata="s"),
    ]
    assert select_eligible_jobs(jobs) == [2, 9]


# ---------------------------------------------------------------------------
# build_context
# ---------------------------------------------------------------------------


def test_build_context_maps_job_fields_and_outputs():
    job = {"duration_minutes": 12.5, "content_type": "short", "transcript_file": "6POL0201.srt"}
    outputs = {"analyst": "A", "formatter": "F", "seo": "S"}

    ctx = build_context(job, outputs)

    assert ctx["analyst_output"] == "A"
    assert ctx["formatter_output"] == "F"
    assert ctx["seo_output"] == "S"
    assert ctx["duration_minutes"] == 12.5
    assert ctx["content_type"] == "short"
    assert ctx["transcript_file"] == "6POL0201.srt"
    assert ctx["transcript"] is None
    assert ctx["program"] is None


def test_build_context_missing_outputs_pass_through_as_none():
    ctx = build_context({}, {})
    assert ctx["analyst_output"] is None
    assert ctx["formatter_output"] is None
    assert ctx["seo_output"] is None
    assert ctx["duration_minutes"] is None
    assert ctx["content_type"] is None
