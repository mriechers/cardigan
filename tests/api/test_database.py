"""Tests for database service layer."""

import os
import tempfile
from datetime import datetime

import pytest
import pytest_asyncio

from api.models.events import EventCreate, EventData, EventType
from api.models.job import JobCreate, JobStatus, JobUpdate
from api.services.database import (
    close_db,
    create_job,
    delete_job,
    get_config,
    get_events_for_job,
    get_job,
    get_next_pending_job,
    get_stale_jobs,
    init_db,
    list_config,
    list_jobs,
    log_event,
    reset_stuck_jobs,
    run_stuck_job_cleanup,
    sanitize_path_component,
    set_config,
    update_job,
)


@pytest_asyncio.fixture
async def test_db():
    """Create a temporary test database."""
    import api.services.database as db_mod

    # Save original engine state so we can restore after this test
    orig_engine = db_mod._engine
    orig_factory = db_mod._async_session_factory
    orig_db_path = os.environ.get("DATABASE_PATH")

    # Reset globals so init_db() creates a fresh engine for the temp DB
    db_mod._engine = None
    db_mod._async_session_factory = None

    # Create temp database file
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    # Set environment variable
    os.environ["DATABASE_PATH"] = db_path

    # Initialize database
    await init_db()

    # Create tables
    from api.services.database import _engine, metadata

    async with _engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    yield db_path

    # Cleanup: close temp DB and restore original engine
    await close_db()

    # Restore the original engine so other tests aren't affected
    db_mod._engine = orig_engine
    db_mod._async_session_factory = orig_factory
    if orig_db_path is not None:
        os.environ["DATABASE_PATH"] = orig_db_path

    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_create_and_get_job(test_db):
    """Test creating and retrieving a job."""
    # Create job
    job_create = JobCreate(
        project_name="test-project",
        project_path="/projects/test-project",
        transcript_file="/transcripts/test.txt",
        priority=5,
    )

    job = await create_job(job_create)

    # Verify creation
    assert job.id is not None
    assert job.project_path == "/projects/test-project"
    assert job.transcript_file == "/transcripts/test.txt"
    assert job.priority == 5
    assert job.status == JobStatus.pending
    assert job.project_name == "test-project"
    assert job.agent_phases == ["analyst", "formatter", "seo", "manager"]
    assert job.retry_count == 0
    assert job.max_retries == 3

    # Retrieve job
    retrieved = await get_job(job.id)
    assert retrieved is not None
    assert retrieved.id == job.id
    assert retrieved.project_path == job.project_path


@pytest.mark.asyncio
async def test_get_nonexistent_job(test_db):
    """Test retrieving a job that doesn't exist."""
    job = await get_job(99999)
    assert job is None


@pytest.mark.asyncio
async def test_list_jobs(test_db):
    """Test listing jobs with filtering."""
    # Create multiple jobs
    await create_job(
        JobCreate(
            project_name="job1",
            project_path="/projects/job1",
            transcript_file="/transcripts/1.txt",
            priority=1,
        )
    )

    await create_job(
        JobCreate(
            project_name="job2",
            project_path="/projects/job2",
            transcript_file="/transcripts/2.txt",
            priority=10,
        )
    )

    job3 = await create_job(
        JobCreate(
            project_name="job3",
            project_path="/projects/job3",
            transcript_file="/transcripts/3.txt",
            priority=5,
        )
    )

    # Update one job to in_progress
    await update_job(job3.id, JobUpdate(status=JobStatus.in_progress))

    # List all jobs (default sort: newest first by queued_at)
    all_jobs = await list_jobs()
    assert len(all_jobs) == 3

    # List only pending jobs
    pending_jobs = await list_jobs(status=JobStatus.pending)
    assert len(pending_jobs) == 2

    # List only in_progress jobs
    in_progress_jobs = await list_jobs(status=JobStatus.in_progress)
    assert len(in_progress_jobs) == 1
    assert in_progress_jobs[0].id == job3.id


@pytest.mark.asyncio
async def test_update_job(test_db):
    """Test updating job fields."""
    # Create job
    job = await create_job(
        JobCreate(
            project_name="test",
            project_path="/projects/test",
            transcript_file="/transcripts/test.txt",
        )
    )

    # Update status to in_progress
    updated = await update_job(job.id, JobUpdate(status=JobStatus.in_progress))
    assert updated.status == JobStatus.in_progress
    assert updated.started_at is not None

    # Update priority and current phase
    updated = await update_job(
        job.id,
        JobUpdate(
            priority=10,
            current_phase="analyst",
        ),
    )
    assert updated.priority == 10
    assert updated.current_phase == "analyst"

    # Update with error
    updated = await update_job(
        job.id,
        JobUpdate(
            status=JobStatus.failed,
            error_message="Test error",
        ),
    )
    assert updated.status == JobStatus.failed
    assert updated.error_message == "Test error"
    assert updated.error_timestamp is not None
    assert updated.completed_at is not None


@pytest.mark.asyncio
async def test_delete_job(test_db):
    """Test deleting a job."""
    # Create job
    job = await create_job(
        JobCreate(
            project_name="test",
            project_path="/projects/test",
            transcript_file="/transcripts/test.txt",
        )
    )

    # Delete job
    deleted = await delete_job(job.id)
    assert deleted is True

    # Verify it's gone
    retrieved = await get_job(job.id)
    assert retrieved is None

    # Try deleting again
    deleted = await delete_job(job.id)
    assert deleted is False


@pytest.mark.asyncio
async def test_get_next_pending_job(test_db):
    """Test getting next pending job by priority."""
    # Create jobs with different priorities
    await create_job(
        JobCreate(
            project_name="job1",
            project_path="/projects/job1",
            transcript_file="/transcripts/1.txt",
            priority=1,
        )
    )

    job2 = await create_job(
        JobCreate(
            project_name="job2",
            project_path="/projects/job2",
            transcript_file="/transcripts/2.txt",
            priority=10,
        )
    )

    job3 = await create_job(
        JobCreate(
            project_name="job3",
            project_path="/projects/job3",
            transcript_file="/transcripts/3.txt",
            priority=5,
        )
    )

    # Get next job - should be highest priority
    next_job = await get_next_pending_job()
    assert next_job is not None
    assert next_job.id == job2.id
    assert next_job.priority == 10

    # Mark it as in_progress
    await update_job(job2.id, JobUpdate(status=JobStatus.in_progress))

    # Get next job again - should be next highest
    next_job = await get_next_pending_job()
    assert next_job.id == job3.id
    assert next_job.priority == 5


@pytest.mark.asyncio
async def test_log_event(test_db):
    """Test logging session events."""
    # Create a job first
    job = await create_job(
        JobCreate(
            project_name="test",
            project_path="/projects/test",
            transcript_file="/transcripts/test.txt",
        )
    )

    # Log event with data
    event_data = EventData(
        cost=0.05,
        tokens=1000,
        backend="openai",
        model="gpt-4",
    )

    event = await log_event(
        EventCreate(
            job_id=job.id,
            event_type=EventType.job_started,
            data=event_data,
        )
    )

    assert event.id is not None
    assert event.job_id == job.id
    assert event.event_type == EventType.job_started
    assert event.data is not None
    assert event.data.cost == 0.05
    assert event.data.tokens == 1000
    assert event.timestamp is not None


@pytest.mark.asyncio
async def test_get_events_for_job(test_db):
    """Test retrieving events for a job."""
    # Create a job
    job = await create_job(
        JobCreate(
            project_name="test",
            project_path="/projects/test",
            transcript_file="/transcripts/test.txt",
        )
    )

    # Log multiple events
    await log_event(
        EventCreate(
            job_id=job.id,
            event_type=EventType.job_queued,
        )
    )

    await log_event(
        EventCreate(
            job_id=job.id,
            event_type=EventType.job_started,
        )
    )

    await log_event(
        EventCreate(
            job_id=job.id,
            event_type=EventType.phase_started,
            data=EventData(phase="analyst"),
        )
    )

    # Retrieve events
    events = await get_events_for_job(job.id)
    assert len(events) == 3
    assert events[0].event_type == EventType.job_queued
    assert events[1].event_type == EventType.job_started
    assert events[2].event_type == EventType.phase_started


@pytest.mark.asyncio
async def test_config_operations(test_db):
    """Test config get/set/list operations."""
    # Set a config value
    config = await set_config(
        key="test_key",
        value="test_value",
        value_type="string",
        description="Test config item",
    )

    assert config.key == "test_key"
    assert config.value == "test_value"
    assert config.value_type.value == "string"
    assert config.description == "Test config item"

    # Get the config value
    retrieved = await get_config("test_key")
    assert retrieved is not None
    assert retrieved.value == "test_value"

    # Update the config value
    updated = await set_config(
        key="test_key",
        value="new_value",
        value_type="string",
    )
    assert updated.value == "new_value"

    # Set another config
    await set_config(
        key="another_key",
        value="42",
        value_type="int",
    )

    # List all config
    all_config = await list_config()
    assert len(all_config) == 2

    # Get nonexistent config
    missing = await get_config("nonexistent")
    assert missing is None


@pytest.mark.asyncio
async def test_thread_safety(test_db):
    """Test concurrent operations work correctly."""
    import asyncio

    # Create multiple jobs concurrently
    async def create_test_job(i):
        return await create_job(
            JobCreate(
                project_name=f"job{i}",
                project_path=f"/projects/job{i}",
                transcript_file=f"/transcripts/{i}.txt",
                priority=i,
            )
        )

    jobs = await asyncio.gather(*[create_test_job(i) for i in range(10)])

    # Verify all jobs created
    assert len(jobs) == 10
    assert all(job.id is not None for job in jobs)

    # List all jobs
    all_jobs = await list_jobs(limit=20)
    assert len(all_jobs) == 10


@pytest.mark.asyncio
async def test_stuck_job_reset(test_db):
    """Test resetting stuck jobs."""
    from datetime import timedelta, timezone

    # Create a job and set it to in_progress
    job = await create_job(
        JobCreate(
            project_name="test",
            project_path="/projects/test",
            transcript_file="/transcripts/test.txt",
        )
    )

    # Update to in_progress
    await update_job(job.id, JobUpdate(status=JobStatus.in_progress))

    # Get the job to check started_at
    job = await get_job(job.id)
    assert job.status == JobStatus.in_progress
    assert job.started_at is not None

    # Manually set started_at to 15 minutes ago to simulate stuck job
    from api.services.database import get_session, jobs_table, update

    old_time = datetime.now(timezone.utc) - timedelta(minutes=15)
    async with get_session() as session:
        stmt = update(jobs_table).where(jobs_table.c.id == job.id).values(started_at=old_time, last_heartbeat=old_time)
        await session.execute(stmt)

    # Check that job is detected as stale
    stale = await get_stale_jobs(threshold_minutes=10)
    assert len(stale) == 1
    assert stale[0].id == job.id

    # Reset stuck jobs
    reset_jobs = await reset_stuck_jobs(threshold_minutes=10)
    assert len(reset_jobs) == 1
    assert reset_jobs[0].id == job.id
    assert reset_jobs[0].status == JobStatus.pending
    assert reset_jobs[0].retry_count == 1
    assert reset_jobs[0].started_at is None
    assert reset_jobs[0].current_phase is None

    # Verify event was logged
    events = await get_events_for_job(job.id)
    system_error_events = [e for e in events if e.event_type == EventType.system_error]
    assert len(system_error_events) == 1
    assert system_error_events[0].data is not None


@pytest.mark.asyncio
async def test_stuck_job_max_retries(test_db):
    """Test that stuck jobs exceeding max retries are marked as failed."""
    from datetime import timedelta, timezone

    # Create a job
    job = await create_job(
        JobCreate(
            project_name="test",
            project_path="/projects/test",
            transcript_file="/transcripts/test.txt",
        )
    )

    # Set retry_count to 2 (one less than max_retries of 3)
    from api.services.database import get_session, jobs_table, update

    async with get_session() as session:
        stmt = update(jobs_table).where(jobs_table.c.id == job.id).values(retry_count=2)
        await session.execute(stmt)

    # Update to in_progress with old timestamp
    await update_job(job.id, JobUpdate(status=JobStatus.in_progress))
    old_time = datetime.now(timezone.utc) - timedelta(minutes=15)
    async with get_session() as session:
        stmt = update(jobs_table).where(jobs_table.c.id == job.id).values(started_at=old_time, last_heartbeat=old_time)
        await session.execute(stmt)

    # Reset stuck jobs - this should mark as failed
    reset_jobs = await reset_stuck_jobs(threshold_minutes=10)
    assert len(reset_jobs) == 1
    assert reset_jobs[0].id == job.id
    assert reset_jobs[0].status == JobStatus.failed
    assert reset_jobs[0].retry_count == 3
    assert reset_jobs[0].error_message == "Max retries exceeded after stuck job reset"
    assert reset_jobs[0].completed_at is not None

    # Verify job_failed event was logged
    events = await get_events_for_job(job.id)
    failed_events = [e for e in events if e.event_type == EventType.job_failed]
    assert len(failed_events) == 1


@pytest.mark.asyncio
async def test_run_stuck_job_cleanup(test_db):
    """Test the cleanup routine returns correct summary."""
    from datetime import timedelta, timezone

    # Create multiple stuck jobs
    job1 = await create_job(
        JobCreate(
            project_name="job1",
            project_path="/projects/job1",
            transcript_file="/transcripts/1.txt",
        )
    )
    job2 = await create_job(
        JobCreate(
            project_name="job2",
            project_path="/projects/job2",
            transcript_file="/transcripts/2.txt",
        )
    )
    job3 = await create_job(
        JobCreate(
            project_name="job3",
            project_path="/projects/job3",
            transcript_file="/transcripts/3.txt",
        )
    )

    # Make job1 and job2 stuck (will be reset to pending)
    for job in [job1, job2]:
        await update_job(job.id, JobUpdate(status=JobStatus.in_progress))

    # Make job3 stuck and set retry_count to 2 (will be marked failed)
    await update_job(job3.id, JobUpdate(status=JobStatus.in_progress))
    from api.services.database import get_session, jobs_table, update

    async with get_session() as session:
        stmt = update(jobs_table).where(jobs_table.c.id == job3.id).values(retry_count=2)
        await session.execute(stmt)

    # Set all to old timestamp
    old_time = datetime.now(timezone.utc) - timedelta(minutes=15)
    async with get_session() as session:
        for job in [job1, job2, job3]:
            stmt = (
                update(jobs_table).where(jobs_table.c.id == job.id).values(started_at=old_time, last_heartbeat=old_time)
            )
            await session.execute(stmt)

    # Run cleanup
    summary = await run_stuck_job_cleanup(threshold_minutes=10)

    assert summary["reset_count"] == 2
    assert summary["failed_count"] == 1
    assert len(summary["job_ids"]) == 3
    assert job1.id in summary["job_ids"]
    assert job2.id in summary["job_ids"]
    assert job3.id in summary["job_ids"]


# ============================================================================
# Path Sanitization Tests
# ============================================================================


def test_sanitize_path_component_basic():
    """Test basic sanitization of valid names."""
    assert sanitize_path_component("simple") == "simple"
    assert sanitize_path_component("with-dashes") == "with-dashes"
    assert sanitize_path_component("with_underscores") == "with_underscores"
    assert sanitize_path_component("WithCaps123") == "WithCaps123"


def test_sanitize_path_component_invalid_chars():
    """Test sanitization removes/replaces invalid filesystem characters."""
    # Forward slash
    assert sanitize_path_component("test/project") == "test_project"

    # Backslash
    assert sanitize_path_component("test\\project") == "test_project"

    # Colon
    assert sanitize_path_component("project:v1") == "project_v1"

    # Asterisk
    assert sanitize_path_component("test*file") == "test_file"

    # Question mark (trailing underscore stripped)
    assert sanitize_path_component("what?") == "what"

    # Double quotes (trailing underscore stripped)
    assert sanitize_path_component('test"name"') == "test_name"

    # Angle brackets (trailing underscore stripped)
    assert sanitize_path_component("project<2024>") == "project_2024"

    # Pipe
    assert sanitize_path_component("test|name") == "test_name"


def test_sanitize_path_component_multiple_invalid():
    """Test sanitization with multiple invalid characters."""
    result = sanitize_path_component("test/file\\name:v1*2?")
    assert result == "test_file_name_v1_2"

    # Complex real-world example (spaces preserved, trailing underscore stripped)
    result = sanitize_path_component("My Project: Episode 1/Part 2 <Draft>")
    assert result == "My Project_ Episode 1_Part 2 _Draft"


def test_sanitize_path_component_consecutive_underscores():
    """Test that multiple consecutive underscores are collapsed."""
    assert sanitize_path_component("test___file") == "test_file"
    assert sanitize_path_component("a//b\\\\c") == "a_b_c"
    assert sanitize_path_component("test::project") == "test_project"


def test_sanitize_path_component_leading_trailing():
    """Test that leading/trailing underscores and spaces are removed."""
    assert sanitize_path_component("_test_") == "test"
    assert sanitize_path_component(" test ") == "test"
    assert sanitize_path_component("___test___") == "test"
    assert sanitize_path_component("/test/") == "test"


def test_sanitize_path_component_spaces():
    """Test that spaces are preserved (but trimmed from ends)."""
    assert sanitize_path_component("My Project Name") == "My Project Name"
    assert sanitize_path_component("  spaced  ") == "spaced"


def test_sanitize_path_component_empty():
    """Test that empty or invalid-only strings return 'unnamed'."""
    assert sanitize_path_component("") == "unnamed"
    assert sanitize_path_component("   ") == "unnamed"
    assert sanitize_path_component("///") == "unnamed"
    assert sanitize_path_component(":::") == "unnamed"
    assert sanitize_path_component("***") == "unnamed"


def test_sanitize_path_component_length_limit():
    """Test that very long names are truncated."""
    long_name = "a" * 250
    result = sanitize_path_component(long_name)
    assert len(result) == 200
    assert result == "a" * 200

    # Test truncation with trailing underscore
    long_name_with_special = "a" * 199 + "_" * 10
    result = sanitize_path_component(long_name_with_special)
    assert len(result) <= 200
    assert not result.endswith("_")


def test_sanitize_path_component_unicode():
    """Test handling of unicode characters."""
    # Unicode alphanumeric chars (é) pass isalnum() in Python 3
    assert sanitize_path_component("café") == "café"
    # Non-alphanumeric unicode (™) stripped, trailing underscore trimmed
    assert sanitize_path_component("test™") == "test"
    # CJK characters pass isalnum() in Python 3
    result = sanitize_path_component("日本語")
    assert result == "日本語"


def test_sanitize_path_component_special_cases():
    """Test edge cases and special scenarios."""
    # Dots are allowed
    assert sanitize_path_component("file.name") == "file.name"
    assert sanitize_path_component("v1.2.3") == "v1.2.3"

    # Mixed valid and invalid
    assert sanitize_path_component("test-file_v1.2") == "test-file_v1.2"

    # Real PBS project name examples
    assert sanitize_path_component("WPT News 2024-03-15") == "WPT News 2024-03-15"
    assert sanitize_path_component("University Place: Episode #123") == "University Place_ Episode _123"


@pytest.mark.asyncio
async def test_create_job_with_sanitized_project_name(test_db):
    """Test that create_job properly sanitizes project names when deriving paths."""
    # Create job without explicit project_path (will be derived from project_name)
    job_create = JobCreate(
        project_name="Test Project: Episode 1/Part 2",
        transcript_file="/transcripts/test.txt",
    )

    job = await create_job(job_create)

    # Verify the project_path was sanitized (spaces preserved, invalid chars → underscore)
    assert job.project_path == "OUTPUT/Test Project_ Episode 1_Part 2"
    assert "/" not in job.project_name  # project_name is derived from path


@pytest.mark.asyncio
async def test_create_job_with_explicit_path_no_sanitization(test_db):
    """Test that explicit project_path is not sanitized (user responsibility)."""
    # When user provides explicit path, we don't sanitize it
    job_create = JobCreate(
        project_name="Test Project",
        project_path="/custom/path/with/slashes",
        transcript_file="/transcripts/test.txt",
    )

    job = await create_job(job_create)

    # Explicit path is preserved as-is
    assert job.project_path == "/custom/path/with/slashes"
