"""Tests for the validator phase."""

import json

import pytest

VALID_RESULT = {
    "phase_results": {
        "analyst": {"status": "pass", "flags": []},
        "formatter": {"status": "fail", "flags": ["review notes in body"]},
        "seo": {"status": "pass", "flags": []},
    },
    "overall": "fail",
}


def test_parse_valid_json_result():
    """Validator output should be parseable as JSON."""
    from api.services.worker import JobWorker

    raw = json.dumps(VALID_RESULT)
    parsed = JobWorker._parse_validation_result(raw)
    assert parsed["overall"] == "fail"
    assert parsed["phase_results"]["formatter"]["status"] == "fail"
    assert len(parsed["phase_results"]["formatter"]["flags"]) == 1


def test_parse_json_with_markdown_fences():
    """Validator might wrap JSON in markdown fences — parser should handle this."""
    from api.services.worker import JobWorker

    raw = f"```json\n{json.dumps(VALID_RESULT)}\n```"
    parsed = JobWorker._parse_validation_result(raw)
    assert parsed["overall"] == "fail"


def test_overall_pass_when_all_phases_pass():
    """Overall should be pass only when all phases pass."""
    result = {
        "phase_results": {
            "analyst": {"status": "pass", "flags": []},
            "formatter": {"status": "pass", "flags": []},
            "seo": {"status": "pass", "flags": []},
        },
        "overall": "pass",
    }
    assert result["overall"] == "pass"
    assert all(p["status"] == "pass" for p in result["phase_results"].values())


def test_validation_result_structure():
    """Validation result must have required keys."""
    result = VALID_RESULT
    assert "phase_results" in result
    assert "overall" in result
    for phase_name in ["analyst", "formatter", "seo"]:
        assert phase_name in result["phase_results"]
        phase = result["phase_results"][phase_name]
        assert "status" in phase
        assert "flags" in phase
        assert phase["status"] in ("pass", "fail")
        assert isinstance(phase["flags"], list)


def test_parse_invalid_json_raises():
    """Invalid JSON should raise JSONDecodeError."""
    from api.services.worker import JobWorker

    with pytest.raises(json.JSONDecodeError):
        JobWorker._parse_validation_result("this is not json")


def test_phases_constant():
    """PHASES should include validator, not manager."""
    from api.services.worker import JobWorker

    assert "validator" in JobWorker.PHASES
    assert "manager" not in JobWorker.PHASES
