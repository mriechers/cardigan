"""Tests for the eval harness's style-report instrumentation.

Covers:
  - ``scripts.eval_pipeline.compute_style_report`` -- the pure, LLM-free
    function factored out of the ``--style-report`` CLI flag so it can be
    tested without invoking the LLM path. Fed synthetic seo/analyst
    markdown built inline (mirrors ``tests/test_style_phase_io.py`` and
    ``tests/test_style_scanner_limits.py``'s fixture style).
  - ``scripts.eval_compare.build_report`` -- the pure report builder behind
    ``python -m scripts.eval_compare``, driven against synthetic run
    directories written with pytest's ``tmp_path`` fixture (real
    ``metrics.json`` files, no eval_pipeline.py invocation).

Neither test touches the LLM/API/DB -- eval_pipeline.py's ``main()`` (which
does) is out of scope here by design.
"""

from __future__ import annotations

import json

from api.services.style_engine.rules import StyleRules
from scripts.eval_compare import build_report
from scripts.eval_pipeline import compute_style_report

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _rules(**overrides) -> StyleRules:
    raw = {
        "meta": {"version": 1},
        "voice": {
            "forbidden_phrases": [
                {"match": "we break down", "category": "first_person_promo", "severity": "error"},
            ],
            "first_person_markers": [r"\bwe\b"],
            "second_person_markers": [r"\byou\b"],
        },
        "casing": {
            "proper_nouns": ["Wisconsin"],
            "acronyms": [],
            "casing_variants": {"gov": "Gov."},
        },
        "limits": {
            # Raw title is 32 chars, normalized ("gov" -> "Gov." adds a
            # period) is 33 -- max=32 makes the title clean PRE-normalization
            # and violating POST-normalization, exercising a real pre/post
            # divergence rather than a static count.
            "fields": {"title": {"max": 32}},
            "content_type_overrides": {},
        },
    }
    raw.update(overrides)
    return StyleRules(raw=raw)


TITLE_RAW = "gov evers visits wisconsin today"  # 32 chars
assert len(TITLE_RAW) == 32

SHORT_DESC = "We break down the state budget for Wisconsin viewers."
LONG_DESC = "A calm, descriptive account of the state budget process in Wisconsin."


def _seo_md(title: str = TITLE_RAW, short_desc: str = SHORT_DESC, long_desc: str = LONG_DESC) -> str:
    return f"""# SEO Report

### Title (Final Recommendation)

**Recommended:**
{title}

**Character Count:** {len(title)}/60

---

### Short Description (150 chars max)

**Recommended:**
{short_desc}

**Character Count:** {len(short_desc)}/150

---

### Long Description (300 chars max)

**Recommended:**
{long_desc}

**Character Count:** {len(long_desc)}/300
"""


ANALYST_MD = """<!-- Provenance -->

## Speakers & Roles

| Speaker | Role | Context | First Appearance |
|---|---|---|---|
| Tony Evers | Governor | Budget debate | 1:20 |
"""

NO_TITLE_SEO_MD = "# SEO Report\n\nNo recommendations generated.\n"


# ---------------------------------------------------------------------------
# compute_style_report -- happy path: fields extracted, pre/post violations
# ---------------------------------------------------------------------------


class TestComputeStyleReportHappyPath:
    def test_fields_extracted_lists_all_three_present_fields(self):
        result = compute_style_report(_seo_md(), ANALYST_MD, _rules())
        assert result["fields_extracted"] == ["title", "short_description", "long_description"]

    def test_proper_nouns_used_from_analyst_speaker_table(self):
        result = compute_style_report(_seo_md(), ANALYST_MD, _rules())
        assert result["proper_nouns_used"] == ["Tony Evers", "Evers"]

    def test_title_raw_and_normalized(self):
        result = compute_style_report(_seo_md(), ANALYST_MD, _rules())
        assert result["title_raw"] == TITLE_RAW
        assert result["title_normalized"] == "Gov. Evers visits Wisconsin today"
        assert result["title_changed"] is True

    def test_pre_violations_from_short_description_only(self):
        # Title is clean pre-normalization (32 chars, limit 32); short_description
        # carries both a forbidden phrase ("we break down") and a first-person
        # marker ("we") -- two violations, both field=short_description.
        result = compute_style_report(_seo_md(), ANALYST_MD, _rules())
        pre = result["violations_pre"]
        assert len(pre) == 2
        assert all(v["field"] == "short_description" for v in pre)
        rule_ids = {v["rule_id"] for v in pre}
        assert rule_ids == {"voice.forbidden.first_person_promo", "voice.first_person"}
        assert not any(v["rule_id"] == "limits.title.max" for v in pre)

    def test_post_violations_add_title_limit_violation(self):
        # Normalization lengthens the title by one char (gov -> Gov.), pushing
        # it from 32 (clean) to 33 (over the 32-char limit) -- this is the
        # concrete pre/post divergence the checks must actually re-run to catch.
        result = compute_style_report(_seo_md(), ANALYST_MD, _rules())
        post = result["violations_post"]
        assert len(post) == 3
        title_violations = [v for v in post if v["field"] == "title"]
        assert len(title_violations) == 1
        assert title_violations[0]["rule_id"] == "limits.title.max"
        # The two short_description violations are unaffected (title-only
        # normalization scope) and still present post.
        short_desc_violations = [v for v in post if v["field"] == "short_description"]
        assert len(short_desc_violations) == 2

    def test_violations_are_json_serializable_dicts(self):
        result = compute_style_report(_seo_md(), ANALYST_MD, _rules())
        # RuleViolation.to_dict() output -- must round-trip through json.dumps.
        json.dumps(result)


# ---------------------------------------------------------------------------
# compute_style_report -- analyst_text optional
# ---------------------------------------------------------------------------


class TestComputeStyleReportNoAnalystText:
    def test_none_analyst_text_yields_no_proper_nouns(self):
        result = compute_style_report(_seo_md(), None, _rules())
        assert result["proper_nouns_used"] == []

    def test_none_analyst_text_still_normalizes_seed_terms(self):
        # "wisconsin" and "gov" come from the rules seed, not the analyst
        # table, so normalization still applies them even with no analyst
        # context -- only the per-job "Evers" restoration is lost.
        result = compute_style_report(_seo_md(), None, _rules())
        assert result["title_normalized"] == "Gov. evers visits Wisconsin today"

    def test_empty_string_analyst_text_same_as_none(self):
        result_none = compute_style_report(_seo_md(), None, _rules())
        result_empty = compute_style_report(_seo_md(), "", _rules())
        assert result_none["title_normalized"] == result_empty["title_normalized"]
        assert result_none["proper_nouns_used"] == result_empty["proper_nouns_used"]


# ---------------------------------------------------------------------------
# compute_style_report -- skipped / unparseable, never raises
# ---------------------------------------------------------------------------


class TestComputeStyleReportSkipped:
    def test_empty_seo_text_is_skipped(self):
        result = compute_style_report("", ANALYST_MD, _rules())
        assert result == {"skipped": True, "reason": "seo output missing or empty"}

    def test_whitespace_only_seo_text_is_skipped(self):
        result = compute_style_report("   \n\n  ", ANALYST_MD, _rules())
        assert result["skipped"] is True

    def test_no_title_section_is_skipped(self):
        result = compute_style_report(NO_TITLE_SEO_MD, ANALYST_MD, _rules())
        assert result == {
            "skipped": True,
            "reason": "seo output unparseable: no title field found",
        }

    def test_skipped_never_raises_on_garbage_input(self):
        # Must never raise, even on nonsense input.
        compute_style_report("not markdown at all $$$ {{{ ", ANALYST_MD, _rules())


# ---------------------------------------------------------------------------
# eval_compare.build_report -- per-phase + style + convergence + delta
# ---------------------------------------------------------------------------


def _write_metrics(run_dir, metrics: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "metrics.json").write_text(json.dumps(metrics))


def _base_metrics(label: str, total_tokens: int, cost: float, wall_s: float) -> dict:
    return {
        "label": label,
        "transcript": "t.srt",
        "backend": "local-llm",
        "phases": [
            {
                "phase": "seo",
                "ok": True,
                "model": "x/model",
                "total_tokens": total_tokens,
                "cost": cost,
                "wall_s": wall_s,
                "words": 80,
            },
        ],
        "completeness": None,
    }


class TestEvalCompareConvergence:
    def test_converged_runs_report_full_match_rate(self, tmp_path):
        run_a = tmp_path / "run_a"
        run_b = tmp_path / "run_b"
        m_a = _base_metrics("run_a", 1000, 0.0, 5.0)
        m_a["style"] = {"seo": {
            "fields_extracted": ["title"], "violations_pre": [], "violations_post": [],
            "title_raw": "gov evers today", "title_normalized": "Gov. Evers Today",
            "title_changed": True, "proper_nouns_used": ["Evers"],
        }}
        m_b = _base_metrics("run_b", 1100, 0.0, 5.5)
        m_b["style"] = {"seo": dict(m_a["style"]["seo"])}
        _write_metrics(run_a, m_a)
        _write_metrics(run_b, m_b)

        report = build_report([run_a, run_b])
        assert "2 of 2 runs byte-identical." in report
        assert "Value: `Gov. Evers Today`" in report

    def test_diverged_runs_list_distinct_normalized_titles(self, tmp_path):
        run_a = tmp_path / "run_a"
        run_b = tmp_path / "run_b"
        m_a = _base_metrics("run_a", 1000, 0.0, 5.0)
        m_a["style"] = {"seo": {
            "fields_extracted": ["title"], "violations_pre": [], "violations_post": [],
            "title_raw": "raw a", "title_normalized": "Normalized A",
            "title_changed": True, "proper_nouns_used": [],
        }}
        m_b = _base_metrics("run_b", 1100, 0.0, 5.5)
        m_b["style"] = {"seo": {
            "fields_extracted": ["title"], "violations_pre": [], "violations_post": [],
            "title_raw": "raw b", "title_normalized": "Normalized B",
            "title_changed": True, "proper_nouns_used": [],
        }}
        _write_metrics(run_a, m_a)
        _write_metrics(run_b, m_b)

        report = build_report([run_a, run_b])
        assert "1 of 2 runs byte-identical." in report
        assert "`Normalized A`" in report
        assert "`Normalized B`" in report
        # Raw-title convergence section is present too, showing the delta.
        assert "`raw a`" in report
        assert "`raw b`" in report


class TestEvalCompareGracefulMissingStyle:
    def test_no_runs_have_style_key_omits_style_sections(self, tmp_path):
        run_a = tmp_path / "run_a"
        run_b = tmp_path / "run_b"
        _write_metrics(run_a, _base_metrics("run_a", 1000, 0.0, 5.0))
        _write_metrics(run_b, _base_metrics("run_b", 1100, 0.0, 5.5))

        report = build_report([run_a, run_b])
        assert "## Style violations" not in report
        assert "## Convergence" not in report
        # Per-phase table is still present -- style is the only thing skipped.
        assert "## Per-phase metrics" in report

    def test_mixed_style_presence_only_includes_runs_that_have_it(self, tmp_path):
        run_a = tmp_path / "run_a"
        run_b = tmp_path / "run_b"
        m_a = _base_metrics("run_a", 1000, 0.0, 5.0)
        m_a["style"] = {"seo": {
            "fields_extracted": ["title"], "violations_pre": [], "violations_post": [],
            "title_raw": "raw a", "title_normalized": "Normalized A",
            "title_changed": False, "proper_nouns_used": [],
        }}
        m_b = _base_metrics("run_b", 1100, 0.0, 5.5)  # no "style" key at all
        _write_metrics(run_a, m_a)
        _write_metrics(run_b, m_b)

        report = build_report([run_a, run_b])
        assert "## Style violations" in report
        assert "run_a" in report
        # run_b never appears in the style table (only run_a has a style key).
        style_section = report.split("## Style violations", 1)[1].split("## Convergence", 1)[0]
        assert "run_b" not in style_section

    def test_skipped_style_shown_as_skipped_not_crashing(self, tmp_path):
        run_a = tmp_path / "run_a"
        m_a = _base_metrics("run_a", 1000, 0.0, 5.0)
        m_a["style"] = {"seo": {"skipped": True, "reason": "seo output missing or empty"}}
        _write_metrics(run_a, m_a)

        report = build_report([run_a])
        assert "skipped: seo output missing or empty" in report


class TestEvalCompareDeltaVsBaseline:
    def test_default_baseline_is_first_run_dir(self, tmp_path):
        run_a = tmp_path / "run_a"
        run_b = tmp_path / "run_b"
        _write_metrics(run_a, _base_metrics("run_a", 1000, 0.0, 5.0))
        _write_metrics(run_b, _base_metrics("run_b", 1500, 0.0, 10.0))

        report = build_report([run_a, run_b])
        assert "Delta vs baseline (`run_a`)" in report
        # tokens: 1000 -> 1500 == +50.0%; wall_s: 5.0 -> 10.0 == +100.0%.
        assert "+50.0%" in report
        assert "+100.0%" in report

    def test_explicit_baseline_overrides_default(self, tmp_path):
        run_a = tmp_path / "run_a"
        run_b = tmp_path / "run_b"
        _write_metrics(run_a, _base_metrics("run_a", 1000, 0.0, 5.0))
        _write_metrics(run_b, _base_metrics("run_b", 2000, 0.0, 5.0))

        report = build_report([run_a, run_b], baseline_dir=run_b)
        assert "Delta vs baseline (`run_b`)" in report
        # run_a vs run_b baseline: tokens 1000 vs 2000 == -50.0%.
        assert "-50.0%" in report

    def test_per_phase_table_includes_model_tokens_cost_wall_words(self, tmp_path):
        run_a = tmp_path / "run_a"
        _write_metrics(run_a, _base_metrics("run_a", 1000, 0.25, 5.0))

        report = build_report([run_a])
        assert "model" in report.split("## Per-phase metrics", 1)[1].split("\n")[2].lower()
        assert "1000" in report
        assert "0.25" in report
        assert "5.0" in report
        assert "80" in report  # words
