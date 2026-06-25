import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from api.models.job import JobCreate, JobStatus
from api.services import escalation
from api.services.database import create_job, get_job
from api.services.escalation import bump_family, parse_model_family, pause_and_suggest, select_escalation_phases
from tests.api.test_database import test_db  # noqa: F401

PHASE_ORDER = ["analyst", "formatter", "seo", "validator", "timestamp"]


@pytest.mark.parametrize(
    "slug,expected",
    [
        ("anthropic/claude-4.5-haiku-20251001", "haiku"),
        ("anthropic/claude-4.6-sonnet-20260217", "sonnet"),
        ("anthropic/claude-sonnet-4.6", "sonnet"),  # word order varies
        ("anthropic/claude-opus-4-8", "opus"),
        ("openai/gpt-4o", None),
        (None, None),
        ("", None),
    ],
)
def test_parse_model_family(slug, expected):
    assert parse_model_family(slug) == expected


@pytest.mark.parametrize(
    "family,expected",
    [
        ("haiku", "sonnet"),
        ("sonnet", "opus"),
        ("opus", None),  # terminal
        (None, None),
        ("mystery", None),
    ],
)
def test_bump_family(family, expected):
    assert bump_family(family) == expected


@pytest.mark.asyncio
async def test_pause_and_suggest_sets_paused_and_marker(test_db):  # noqa: F811
    job = await create_job(JobCreate(project_name="p", project_path="/p", transcript_file="/t.txt"))
    await pause_and_suggest(job.id, trigger="qa_fail", message="QA failed — review or retry.", mark_escalated=True)
    refreshed = await get_job(job.id)
    assert refreshed.status == JobStatus.paused
    assert "QA failed" in refreshed.error_message
    assert refreshed.auto_escalated_at is not None


def test_selects_earliest_flagged_plus_downstream():
    vr = {
        "overall": "fail",
        "phase_results": {
            "analyst": {"status": "pass", "flags": []},
            "formatter": {"status": "fail", "flags": ["x"]},
            "seo": {"status": "pass", "flags": []},
        },
    }
    assert select_escalation_phases(vr, PHASE_ORDER) == ["formatter", "seo", "validator", "timestamp"]


def test_no_flags_returns_empty():
    vr = {"overall": "pass", "phase_results": {"formatter": {"status": "pass", "flags": []}}}
    assert select_escalation_phases(vr, PHASE_ORDER) == []


@pytest.mark.asyncio
async def test_resolve_escalated_model_bumps_and_resolves():
    with patch.object(
        escalation.model_roster, "newest_in_family", AsyncMock(return_value="anthropic/claude-sonnet-4-6")
    ) as m:
        got = await escalation.resolve_escalated_model("anthropic/claude-4.5-haiku-20251001", ["fast", "fable"])
    assert got == "anthropic/claude-sonnet-4-6"
    m.assert_awaited_once_with("sonnet", ["fast", "fable"])


@pytest.mark.asyncio
async def test_resolve_escalated_model_none_when_opus():
    assert await escalation.resolve_escalated_model("anthropic/claude-opus-4-8", ["fast"]) is None


def test_config_has_qa_escalation_and_sonnet_validator():
    cfg = json.loads((Path(__file__).resolve().parents[2] / "config" / "llm-config.json").read_text())
    qa = cfg["qa_escalation"]
    assert qa["on_validation_fail"] is True
    assert qa["max_auto_escalations"] == 1
    assert qa["exclude_variants"] == ["fast", "fable"]
    # validator no longer on the cheapskate tier
    assert cfg["phase_backends"]["validator"] != "openrouter-cheapskate"
