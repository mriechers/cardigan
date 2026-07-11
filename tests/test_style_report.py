"""Tests for scripts/style_report.py -- the rule-update feedback-loop
aggregator (Task 6b).

All tests drive the PURE functions (counting/grouping, diff clustering,
zero-hit detection, proposal thresholding, report rendering) with synthetic
event-row dicts / rules dicts built inline. No DB, no filesystem, no
network -- ``fetch_events`` (sqlite3 I/O) and ``main`` (CLI plumbing) are
deliberately out of scope here, mirroring
tests/test_eval_style_report.py's split between pure-logic and
network/DB-touching code.
"""

from __future__ import annotations

from scripts.style_report import (
    PROPOSAL_BANNER,
    CandidateRule,
    build_candidate_rules,
    build_report,
    classify_action,
    cluster_corrections,
    diff_replacement_pairs,
    normalize_correction_record,
    normalize_violation_record,
    null_pipeline_correction_counts,
    propose_edits,
    summarize_violations,
    zero_hit_rules,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _violation_row(
    *,
    job_id: int = 1,
    rule_id: str = "voice.forbidden.viewer_directive",
    phase: str = "seo",
    severity: str = "error",
    model: str | None = None,
    app_version: str | None = "v4.2",
    action: str | None = "flagged",
    mode: str | None = "enforce",
    source: str | None = None,
    model_fixable: bool = True,
) -> dict:
    extra = {
        "rule_id": rule_id,
        "phase": phase,
        "severity": severity,
        "message": "test violation",
        "field": "title",
        "span": None,
        "model_fixable": model_fixable,
    }
    if action is not None:
        extra["action"] = action
    if mode is not None:
        extra["mode"] = mode
    if source is not None:
        extra["source"] = source
    if model is not None:
        extra["model"] = model
    return {
        "id": 1,
        "job_id": job_id,
        "timestamp": "2026-07-01 12:00:00",
        "event_type": "style_violation",
        "data": {"phase": phase, "extra": extra},
        "app_version": app_version,
    }


def _correction_row(
    *,
    job_id: int = 1,
    field: str = "title",
    pipeline_value: str | None,
    committed_value: str,
    media_id: str = "MEDIA1",
    app_version: str | None = "v4.2",
) -> dict:
    return {
        "id": 1,
        "job_id": job_id,
        "timestamp": "2026-07-01 12:00:00",
        "event_type": "editor_correction",
        "data": {
            "phase": "mcp_commit",
            "extra": {
                "field": field,
                "media_id": media_id,
                "committed_value": committed_value,
                "pipeline_value": pipeline_value,
                "original_value": "",
            },
        },
        "app_version": app_version,
    }


# ---------------------------------------------------------------------------
# classify_action
# ---------------------------------------------------------------------------


def test_classify_action_post_stage_enforce_mode_is_flagged():
    assert classify_action({"action": "flagged", "mode": "enforce"}) == "flagged"


def test_classify_action_post_stage_shadow_mode_is_shadow():
    assert classify_action({"action": "shadow", "mode": "shadow"}) == "shadow"


def test_classify_action_lint_enforce_mode_is_enforce():
    assert classify_action({"source": "lint", "mode": "enforce"}) == "enforce"


def test_classify_action_lint_shadow_mode_is_shadow():
    assert classify_action({"source": "lint", "mode": "shadow"}) == "shadow"


def test_classify_action_unrecognized_payload_is_unknown():
    assert classify_action({}) == "unknown"
    assert classify_action({"mode": "enforce"}) == "unknown"  # no action, no source=lint


# ---------------------------------------------------------------------------
# normalize_violation_record / summarize_violations
# ---------------------------------------------------------------------------


def test_normalize_violation_record_extracts_expected_fields():
    row = _violation_row(rule_id="voice.forbidden.cta", phase="seo", model="claude-x", app_version="v4.2")
    rec = normalize_violation_record(row)
    assert rec["rule_id"] == "voice.forbidden.cta"
    assert rec["phase"] == "seo"
    assert rec["model"] == "claude-x"
    assert rec["app_version"] == "v4.2"
    assert rec["action"] == "flagged"


def test_summarize_violations_counts_and_groups_by_rule_and_phase():
    records = [
        normalize_violation_record(_violation_row(rule_id="voice.forbidden.cta", phase="seo", action="flagged")),
        normalize_violation_record(_violation_row(rule_id="voice.forbidden.cta", phase="seo", action="flagged")),
        normalize_violation_record(_violation_row(rule_id="voice.forbidden.cta", phase="seo", action="shadow", mode="shadow")),
        # Different phase -- must NOT merge with the seo rows above.
        normalize_violation_record(_violation_row(rule_id="voice.forbidden.cta", phase="formatter", action="flagged")),
        # Different rule_id entirely.
        normalize_violation_record(_violation_row(rule_id="limits.title.max", phase="seo", action="flagged")),
    ]
    summary = summarize_violations(records)

    by_key = {(r["rule_id"], r["phase"]): r for r in summary}
    cta_seo = by_key[("voice.forbidden.cta", "seo")]
    assert cta_seo["flagged"] == 2
    assert cta_seo["shadow"] == 1
    assert cta_seo["enforce"] == 0
    assert cta_seo["total"] == 3

    cta_formatter = by_key[("voice.forbidden.cta", "formatter")]
    assert cta_formatter["total"] == 1

    limits_seo = by_key[("limits.title.max", "seo")]
    assert limits_seo["total"] == 1

    # Highest total sorts first.
    assert summary[0]["total"] >= summary[-1]["total"]


def test_summarize_violations_sub_breaks_by_model_and_app_version():
    records = [
        normalize_violation_record(_violation_row(rule_id="r1", phase="seo", model="model-a", app_version="v4.1")),
        normalize_violation_record(_violation_row(rule_id="r1", phase="seo", model="model-a", app_version="v4.2")),
        normalize_violation_record(_violation_row(rule_id="r1", phase="seo", model="model-b", app_version="v4.2")),
        # No model in payload -- must land in the "(unset)" bucket, not crash.
        normalize_violation_record(_violation_row(rule_id="r1", phase="seo", model=None, app_version="v4.2")),
    ]
    summary = summarize_violations(records)
    row = summary[0]
    assert row["by_model"]["model-a"] == 2
    assert row["by_model"]["model-b"] == 1
    assert row["by_model"]["(unset)"] == 1
    assert row["by_app_version"]["v4.2"] == 3
    assert row["by_app_version"]["v4.1"] == 1


# ---------------------------------------------------------------------------
# diff_replacement_pairs
# ---------------------------------------------------------------------------


def test_diff_replacement_pairs_single_word():
    pairs = diff_replacement_pairs("The show explores nature.", "The show examines nature.")
    assert pairs == [("explores", "examines")]


def test_diff_replacement_pairs_multi_word_replacement():
    pairs = diff_replacement_pairs("The program explores wildlife.", "The program examines wild animals.")
    assert pairs == [("explores wildlife", "examines wild animals")]


def test_diff_replacement_pairs_whitespace_only_change_ignored():
    pairs = diff_replacement_pairs("Hello  world.", "Hello world.")
    assert pairs == []


def test_diff_replacement_pairs_identical_values_ignored():
    pairs = diff_replacement_pairs("Same text here.", "Same text here.")
    assert pairs == []


def test_diff_replacement_pairs_null_pipeline_value_returns_empty():
    assert diff_replacement_pairs(None, "Whatever the editor wrote.") == []


# ---------------------------------------------------------------------------
# cluster_corrections / null_pipeline_correction_counts
# ---------------------------------------------------------------------------


def test_cluster_corrections_groups_recurring_single_word_replacement():
    records = [
        normalize_correction_record(
            _correction_row(field="short_description", pipeline_value="The show explores art.", committed_value="The show examines art.")
        ),
        normalize_correction_record(
            _correction_row(field="long_description", pipeline_value="It explores culture.", committed_value="It examines culture.")
        ),
        normalize_correction_record(
            _correction_row(field="short_description", pipeline_value="They explores nothing else.", committed_value="They examines nothing else.")
        ),
    ]
    clusters = cluster_corrections(records)
    assert clusters[0]["old"] == "explores"
    assert clusters[0]["new"] == "examines"
    assert clusters[0]["count"] == 3
    assert len(clusters[0]["examples"]) == 3


def test_cluster_corrections_ignores_null_pipeline_records():
    records = [
        normalize_correction_record(_correction_row(pipeline_value=None, committed_value="Whatever.")),
    ]
    assert cluster_corrections(records) == []


def test_null_pipeline_correction_counts_buckets_by_field():
    records = [
        normalize_correction_record(_correction_row(field="keywords", pipeline_value=None, committed_value="a, b, c")),
        normalize_correction_record(_correction_row(field="keywords", pipeline_value=None, committed_value="d, e, f")),
        normalize_correction_record(_correction_row(field="hashtags", pipeline_value=None, committed_value="#x")),
        # Has a pipeline_value -- must NOT be counted here.
        normalize_correction_record(_correction_row(field="title", pipeline_value="A title", committed_value="A Title")),
    ]
    counts = null_pipeline_correction_counts(records)
    assert counts == {"keywords": 2, "hashtags": 1}


# ---------------------------------------------------------------------------
# zero-hit rules
# ---------------------------------------------------------------------------


_SYNTHETIC_RULES = {
    "meta": {"version": 1},
    "voice": {
        "forbidden_phrases": [
            {"id": "watch_as", "match": "watch as", "category": "viewer_directive", "tier": "flag", "severity": "error"},
            {"id": "watch_how", "match": "watch how", "category": "viewer_directive", "tier": "flag", "severity": "error"},
            {"id": "join_us", "match": "join us", "category": "cta", "tier": "flag", "severity": "error"},
        ]
    },
    "phases": {
        "formatter": {
            "substitutions": [
                {"find": r"\b[Oo]kay\b", "replace": "OK", "tier": "enforce"},
                {"id": "oxford_comma", "detect": ",\\s+and\\b", "tier": "flag", "severity": "warning"},
            ]
        }
    },
}


def test_build_candidate_rules_covers_substitutions_and_forbidden_groups():
    candidates = build_candidate_rules(_SYNTHETIC_RULES)
    rule_ids = {c.rule_id for c in candidates}
    assert "OK" in rule_ids  # enforce-tier substitution with no explicit id -> rule_id is the replace text
    assert "formatter.oxford_comma" in rule_ids
    assert "voice.forbidden.viewer_directive" in rule_ids  # watch_as + watch_how collapse to one candidate
    assert "voice.forbidden.cta" in rule_ids

    viewer_directive = next(c for c in candidates if c.rule_id == "voice.forbidden.viewer_directive")
    assert set(viewer_directive.labels) == {"watch_as", "watch_how"}
    assert isinstance(viewer_directive, CandidateRule)


def test_zero_hit_rules_excludes_observed_and_keeps_unobserved():
    candidates = build_candidate_rules(_SYNTHETIC_RULES)
    # "cta" was observed; "viewer_directive" and the enforce substitution were not.
    observed = {"voice.forbidden.cta"}
    zero = zero_hit_rules(candidates, observed)
    zero_ids = {c.rule_id for c in zero}
    assert "voice.forbidden.cta" not in zero_ids
    assert "voice.forbidden.viewer_directive" in zero_ids
    assert "OK" in zero_ids
    assert "formatter.oxford_comma" in zero_ids


def test_zero_hit_rules_empty_when_everything_observed():
    candidates = build_candidate_rules(_SYNTHETIC_RULES)
    observed = {c.rule_id for c in candidates}
    assert zero_hit_rules(candidates, observed) == []


# ---------------------------------------------------------------------------
# propose_edits thresholding
# ---------------------------------------------------------------------------


def _synthetic_cluster(old: str, new: str, count: int) -> dict:
    return {"old": old, "new": new, "count": count, "examples": [{"field": "title", "job_id": 1, "media_id": "M1"}]}


def test_propose_edits_below_default_threshold_produces_nothing():
    clusters = [_synthetic_cluster("explores", "examines", 2)]
    assert propose_edits(clusters) == []


def test_propose_edits_at_default_threshold_produces_one_proposal():
    clusters = [_synthetic_cluster("explores", "examines", 3)]
    proposals = propose_edits(clusters)
    assert len(proposals) == 1
    assert proposals[0]["kind"] == "forbidden_phrases"
    assert "explores" in proposals[0]["yaml"]


def test_propose_edits_casing_only_difference_produces_casing_variant_proposal():
    clusters = [_synthetic_cluster("madison", "Madison", 3)]
    proposals = propose_edits(clusters)
    assert len(proposals) == 1
    assert proposals[0]["kind"] == "casing_variants"
    assert "casing_variants" in proposals[0]["yaml"]


def test_propose_edits_skips_pure_insertions_and_deletions():
    clusters = [_synthetic_cluster("", "added phrase", 5), _synthetic_cluster("removed phrase", "", 5)]
    assert propose_edits(clusters) == []


def test_propose_edits_respects_custom_threshold():
    clusters = [_synthetic_cluster("explores", "examines", 5)]
    assert propose_edits(clusters, threshold=10) == []
    assert len(propose_edits(clusters, threshold=5)) == 1


# ---------------------------------------------------------------------------
# build_report -- rendering
# ---------------------------------------------------------------------------


def test_build_report_renders_all_sections_and_banner():
    events = [
        _violation_row(rule_id="voice.forbidden.cta", phase="seo", action="flagged"),
        _correction_row(
            field="short_description",
            pipeline_value="The show explores art in Wisconsin.",
            committed_value="The show examines art in Wisconsin.",
        ),
    ]
    window = {"since": "2026-06-01", "until": "2026-07-01", "app_version": None, "db_path": "dashboard.db", "generated_at": "2026-07-10T00:00:00+00:00"}
    report = build_report(events, _SYNTHETIC_RULES, window)

    assert "# Style Feedback Report" in report
    assert "## Violations summary" in report
    assert "## Correction patterns" in report
    assert "## Zero-hit rules" in report
    assert "## Proposed YAML edits" in report
    assert "never auto-applied" in report
    assert PROPOSAL_BANNER in report
    # Header metadata + totals present.
    assert "2026-06-01" in report
    assert "Total events in window: **2**" in report


def test_build_report_handles_zero_events_without_raising():
    window = {"since": None, "until": None, "app_version": None, "db_path": "dashboard.db", "generated_at": "2026-07-10T00:00:00+00:00"}
    report = build_report([], {}, window)
    assert "# Style Feedback Report" in report
    assert "Total events in window: **0**" in report
    assert "never auto-applied" in report


def test_build_report_proposal_reaches_threshold_when_repeated_across_events():
    events = [
        _correction_row(field="short_description", pipeline_value="A explores B.", committed_value="A examines B."),
        _correction_row(field="long_description", pipeline_value="C explores D.", committed_value="C examines D."),
        _correction_row(field="short_description", pipeline_value="E explores F.", committed_value="E examines F."),
    ]
    window = {"since": None, "until": None, "app_version": None, "db_path": "dashboard.db", "generated_at": "now"}
    report = build_report(events, {}, window)
    assert "```yaml" in report
    assert "explores" in report
