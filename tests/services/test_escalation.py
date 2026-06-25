import pytest

from api.models.job import JobCreate, JobStatus
from api.services.database import create_job, get_job
from api.services.escalation import bump_family, parse_model_family, pause_and_suggest
from tests.api.test_database import test_db  # reuse fixture


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
async def test_pause_and_suggest_sets_paused_and_marker(test_db):
    job = await create_job(JobCreate(project_name="p", project_path="/p", transcript_file="/t.txt"))
    await pause_and_suggest(job.id, trigger="qa_fail", message="QA failed — review or retry.", mark_escalated=True)
    refreshed = await get_job(job.id)
    assert refreshed.status == JobStatus.paused
    assert "QA failed" in refreshed.error_message
    assert refreshed.auto_escalated_at is not None
