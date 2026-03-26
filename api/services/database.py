"""Database service layer for Cardigan.

Provides async database operations using SQLAlchemy 2.0+ with aiosqlite.
Thread-safe connection pool and CRUD operations for jobs, events, and config.
"""

import glob
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    and_,
    delete,
    desc,
    func,
    select,
    update,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api.models.chat import ChatMessage, ChatSession, ChatSessionStatus
from api.models.config import ConfigItem, ConfigValueType
from api.models.events import EventCreate, EventData, EventType, SessionEvent
from api.models.job import Job, JobCreate, JobOutputs, JobPhase, JobStatus, JobUpdate, PhaseStatus


class _SafeEncoder(json.JSONEncoder):
    """JSON encoder that handles datetime and enum objects."""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        return super().default(obj)


# Global engine and session factory
_engine: Optional[AsyncEngine] = None
_async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None

# SQLAlchemy metadata and table definitions
metadata = MetaData()

# Define jobs table
jobs_table = Table(
    "jobs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("project_path", Text, nullable=False),
    Column("transcript_file", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("priority", Integer, nullable=False, server_default="0"),
    Column("queued_at", DateTime, server_default=func.current_timestamp()),
    Column("started_at", DateTime, nullable=True),
    Column("completed_at", DateTime, nullable=True),
    Column("estimated_cost", Float, server_default="0.0"),
    Column("actual_cost", Float, server_default="0.0"),
    Column("agent_phases", Text, server_default='["analyst", "formatter"]'),
    Column("current_phase", Text, nullable=True),
    Column("retry_count", Integer, server_default="0"),
    Column("max_retries", Integer, server_default="3"),
    Column("error_message", Text, nullable=True),
    Column("error_timestamp", DateTime, nullable=True),
    Column("manifest_path", Text, nullable=True),
    Column("logs_path", Text, nullable=True),
    Column("last_heartbeat", DateTime, nullable=True),
    Column("phases", Text, nullable=True),  # JSON array of JobPhase objects
    Column("airtable_record_id", Text, nullable=True),
    Column("airtable_url", Text, nullable=True),
    Column("media_id", Text, nullable=True),
    Column("duration_minutes", Float, nullable=True),
    Column("word_count", Integer, nullable=True),
)

# Define session_stats table
session_stats_table = Table(
    "session_stats",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", Integer, ForeignKey("jobs.id"), nullable=True),
    Column("timestamp", DateTime, server_default=func.current_timestamp()),
    Column("event_type", Text, nullable=False),
    Column("data", Text, nullable=True),
)

# Define config table
config_table = Table(
    "config",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
    Column("value_type", Text, server_default="string"),
    Column("description", Text, nullable=True),
    Column("updated_at", DateTime, server_default=func.current_timestamp()),
)

# Define chat_sessions table for conversation persistence
chat_sessions_table = Table(
    "chat_sessions",
    metadata,
    Column("id", Text, primary_key=True),  # UUID string
    Column("job_id", Integer, ForeignKey("jobs.id"), nullable=False),
    Column("project_name", Text, nullable=False),
    Column("created_at", DateTime, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, server_default=func.current_timestamp()),
    Column("total_tokens", Integer, server_default="0"),
    Column("total_cost", Float, server_default="0.0"),
    Column("message_count", Integer, server_default="0"),
    Column("status", Text, server_default="active"),  # active, archived, cleared
    Column("model", Text, nullable=True),  # Primary model used in session
)

# Define chat_messages table for message history
chat_messages_table = Table(
    "chat_messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", Text, ForeignKey("chat_sessions.id"), nullable=False),
    Column("role", Text, nullable=False),  # user, assistant, system
    Column("content", Text, nullable=False),
    Column("created_at", DateTime, server_default=func.current_timestamp()),
    Column("tokens", Integer, nullable=True),  # Token count (assistant messages only)
    Column("cost", Float, nullable=True),  # Cost in USD (assistant messages only)
    Column("model", Text, nullable=True),  # Model used (assistant messages only)
    Column("duration_ms", Integer, nullable=True),  # Response latency (assistant messages only)
)


# Define available_files table for tracking remote ingest server files
# Schema from migrations 006 + 007
available_files_table = Table(
    "available_files",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("remote_url", Text, nullable=False),
    Column("filename", Text, nullable=False),
    Column("directory_path", Text, nullable=True),
    Column("file_type", Text, nullable=False),
    Column("media_id", Text, nullable=True),
    Column("file_size_bytes", Integer, nullable=True),
    Column("remote_modified_at", DateTime, nullable=True),
    Column("first_seen_at", DateTime, server_default=func.current_timestamp()),
    Column("last_seen_at", DateTime, server_default=func.current_timestamp()),
    Column("status", Text, nullable=False, server_default="new"),
    Column("status_changed_at", DateTime, nullable=True),
    Column("job_id", Integer, ForeignKey("jobs.id"), nullable=True),
    Column("airtable_record_id", Text, nullable=True),
    Column("attached_at", DateTime, nullable=True),
    # Added in migration 007
    Column("local_path", Text, nullable=True),
    Column("downloaded_at", DateTime, nullable=True),
)

# Define screengrab_attachments audit log table (migration 006)
screengrab_attachments_table = Table(
    "screengrab_attachments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("available_file_id", Integer, ForeignKey("available_files.id"), nullable=True),
    Column("sst_record_id", Text, nullable=False),
    Column("media_id", Text, nullable=False),
    Column("filename", Text, nullable=False),
    Column("remote_url", Text, nullable=False),
    Column("attached_at", DateTime, server_default=func.current_timestamp()),
    Column("attachments_before", Integer, nullable=True),
    Column("attachments_after", Integer, nullable=True),
    Column("success", Boolean, server_default="1"),
    Column("error_message", Text, nullable=True),
)


def get_db_url() -> str:
    """Return SQLite database URL from environment or default."""
    db_path = os.getenv("DATABASE_PATH", "./dashboard.db")
    return f"sqlite+aiosqlite:///{db_path}"


async def init_db() -> None:
    """Initialize database connection pool.

    Creates async engine and session factory.
    Should be called once at application startup.
    """
    global _engine, _async_session_factory

    if _engine is not None:
        # Already initialized
        return

    db_url = get_db_url()

    # Create async engine with connection pooling
    _engine = create_async_engine(
        db_url,
        echo=False,  # Set to True for SQL debug logging
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,  # Verify connections before use
        connect_args={"check_same_thread": False},  # SQLite specific
    )

    # Create session factory
    _async_session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Create tables if they don't exist (fresh database)
    async with _engine.begin() as conn:
        await conn.run_sync(metadata.create_all)


async def close_db() -> None:
    """Close database connections and cleanup resources.

    Should be called at application shutdown.
    """
    global _engine, _async_session_factory

    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None


@asynccontextmanager
async def get_session():
    """Get async database session context manager.

    Usage:
        async with get_session() as session:
            result = await session.execute(...)
    """
    if _async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    async with _async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


# ============================================================================
# Helper Functions for Path Sanitization
# ============================================================================


def sanitize_path_component(name: str) -> str:
    """Sanitize a string for safe use as a filesystem path component.

    Removes or replaces characters that are invalid in file paths across
    different operating systems (/, \\, :, *, ?, ", <>, |).

    Args:
        name: The string to sanitize (e.g., project name)

    Returns:
        Sanitized string safe for use as a path component

    Examples:
        >>> sanitize_path_component("My Project: Part 1")
        'My_Project_Part_1'
        >>> sanitize_path_component("test/file\\name")
        'test_file_name'
        >>> sanitize_path_component("project<2024>")
        'project_2024_'
    """
    if not name:
        return "unnamed"

    # Replace invalid characters with underscore
    sanitized = "".join(c if c.isalnum() or c in "-_. " else "_" for c in name)

    # Replace multiple consecutive underscores with single underscore
    while "__" in sanitized:
        sanitized = sanitized.replace("__", "_")

    # Remove leading/trailing underscores and spaces
    sanitized = sanitized.strip("_ ")

    # Ensure result is not empty
    if not sanitized:
        return "unnamed"

    # Limit length to avoid filesystem issues (most systems support 255 chars)
    max_length = 200  # Conservative limit
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip("_ ")

    return sanitized


# ============================================================================
# Job CRUD Operations
# ============================================================================


async def create_job(job: JobCreate) -> Job:
    """Create a new job in the database.

    Args:
        job: Job creation schema with required fields

    Returns:
        Complete Job record with generated ID and defaults
    """
    async with get_session() as session:
        # Initialize phases - automated pipeline phases (manager is QA, copy_editor is interactive)
        default_phases = ["analyst", "formatter", "seo", "manager"]
        initial_phases = [JobPhase(name=name, status=PhaseStatus.pending).model_dump() for name in default_phases]

        # Derive project_path from project_name if not provided
        project_path = job.project_path
        if project_path is None:
            # Sanitize project name for filesystem using helper function
            safe_name = sanitize_path_component(job.project_name)
            output_dir = os.getenv("OUTPUT_DIR", "OUTPUT")
            project_path = f"{output_dir}/{safe_name}"

        # Prepare values
        # Note: agent_phases is a legacy field, phases is the new structured format
        values = {
            "project_path": project_path,
            "transcript_file": job.transcript_file,
            "priority": job.priority or 0,
            "status": JobStatus.pending.value,
            "queued_at": datetime.now(timezone.utc),
            "estimated_cost": 0.0,
            "actual_cost": 0.0,
            "agent_phases": json.dumps(default_phases),  # Legacy field
            "phases": json.dumps(initial_phases),
            "retry_count": 0,
            "max_retries": 3,
        }

        # Insert job
        stmt = jobs_table.insert().values(**values)
        result = await session.execute(stmt)
        job_id = result.inserted_primary_key[0]

        # Fetch and return complete job (within same session)
        stmt = select(jobs_table).where(jobs_table.c.id == job_id)
        result = await session.execute(stmt)
        row = result.fetchone()

        job = _row_to_job(row)

        # Broadcast job creation to WebSocket clients
        try:
            from api.routers.websocket import broadcast_job_update

            await broadcast_job_update(job, event_type="job_created")
        except Exception:
            # Don't fail job creation if broadcast fails
            pass

        return job


async def get_job(job_id: int) -> Optional[Job]:
    """Retrieve a job by ID.

    Args:
        job_id: Job ID to retrieve

    Returns:
        Job record or None if not found
    """
    async with get_session() as session:
        stmt = select(jobs_table).where(jobs_table.c.id == job_id)
        result = await session.execute(stmt)
        row = result.fetchone()

        if row is None:
            return None

        return _row_to_job(row)


async def find_jobs_by_transcript(
    transcript_file: str,
    exclude_cancelled: bool = True,
) -> List[Job]:
    """Find existing jobs for a transcript file.

    Used for duplicate detection - checks if a transcript has already been
    processed or is currently in queue.

    Args:
        transcript_file: The transcript filename to search for
        exclude_cancelled: Whether to exclude cancelled jobs (default: True)

    Returns:
        List of Job records matching the transcript file
    """
    async with get_session() as session:
        stmt = select(jobs_table).where(jobs_table.c.transcript_file == transcript_file)

        if exclude_cancelled:
            stmt = stmt.where(jobs_table.c.status != JobStatus.cancelled.value)

        # Order by newest first
        stmt = stmt.order_by(jobs_table.c.queued_at.desc())

        result = await session.execute(stmt)
        rows = result.fetchall()

        return [_row_to_job(row) for row in rows]


async def find_jobs_by_media_id(
    media_id: str,
    exclude_cancelled: bool = True,
) -> List[Job]:
    """Find existing jobs for a media ID.

    Used for duplicate detection - checks if content with this media ID
    has already been processed or is currently in queue.

    Args:
        media_id: The media ID to search for (e.g., "2WLI1209HD")
        exclude_cancelled: Whether to exclude cancelled jobs (default: True)

    Returns:
        List of Job records matching the media ID
    """
    async with get_session() as session:
        stmt = select(jobs_table).where(jobs_table.c.media_id == media_id)

        if exclude_cancelled:
            stmt = stmt.where(jobs_table.c.status != JobStatus.cancelled.value)

        # Order by newest first
        stmt = stmt.order_by(jobs_table.c.queued_at.desc())

        result = await session.execute(stmt)
        rows = result.fetchall()

        return [_row_to_job(row) for row in rows]


async def list_jobs(
    status: Optional[JobStatus] = None,
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
    sort_order: str = "newest",
) -> List[Job]:
    """List jobs with optional filtering, search, and pagination.

    Args:
        status: Filter by job status (None = all statuses)
        limit: Maximum number of jobs to return
        offset: Number of jobs to skip
        search: Filter by transcript_file or project_path (case-insensitive contains)
        sort_order: "newest" (default) or "oldest" - by queued_at timestamp

    Returns:
        List of Job records
    """
    async with get_session() as session:
        stmt = select(jobs_table)

        # Apply status filter
        if status is not None:
            stmt = stmt.where(jobs_table.c.status == status.value)

        # Apply search filter (case-insensitive)
        if search:
            search_pattern = f"%{search}%"
            stmt = stmt.where(
                (jobs_table.c.transcript_file.ilike(search_pattern)) | (jobs_table.c.project_path.ilike(search_pattern))
            )

        # Order by queued_at (newest or oldest first)
        if sort_order == "oldest":
            stmt = stmt.order_by(jobs_table.c.queued_at.asc(), jobs_table.c.id.asc())
        else:  # newest first (default)
            stmt = stmt.order_by(jobs_table.c.queued_at.desc(), jobs_table.c.id.desc())

        # Apply pagination
        stmt = stmt.limit(limit).offset(offset)

        result = await session.execute(stmt)
        rows = result.fetchall()

        return [_row_to_job(row) for row in rows]


async def count_jobs(
    status: Optional[JobStatus] = None,
    search: Optional[str] = None,
) -> int:
    """Count jobs matching filter criteria.

    Args:
        status: Filter by job status (None = all statuses)
        search: Filter by transcript_file or project_path

    Returns:
        Count of matching jobs
    """
    async with get_session() as session:
        stmt = select(func.count()).select_from(jobs_table)

        if status is not None:
            stmt = stmt.where(jobs_table.c.status == status.value)

        if search:
            search_pattern = f"%{search}%"
            stmt = stmt.where(
                (jobs_table.c.transcript_file.ilike(search_pattern)) | (jobs_table.c.project_path.ilike(search_pattern))
            )

        result = await session.execute(stmt)
        return result.scalar() or 0


async def update_job(job_id: int, job_update: JobUpdate) -> Optional[Job]:
    """Update a job with partial fields.

    Args:
        job_id: Job ID to update
        job_update: Partial update schema with optional fields

    Returns:
        Updated Job record or None if not found
    """
    async with get_session() as session:
        # Build update dict from non-None fields
        update_values = {}

        if job_update.status is not None:
            update_values["status"] = job_update.status.value

            # Auto-set timestamps based on status
            if job_update.status == JobStatus.in_progress and "started_at" not in update_values:
                update_values["started_at"] = datetime.now(timezone.utc)
            elif job_update.status in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled):
                if "completed_at" not in update_values:
                    update_values["completed_at"] = datetime.now(timezone.utc)

        if job_update.priority is not None:
            update_values["priority"] = job_update.priority

        if job_update.current_phase is not None:
            update_values["current_phase"] = job_update.current_phase

        if job_update.error_message is not None:
            update_values["error_message"] = job_update.error_message
            update_values["error_timestamp"] = datetime.now(timezone.utc)

        if job_update.estimated_cost is not None:
            update_values["estimated_cost"] = job_update.estimated_cost

        if job_update.actual_cost is not None:
            update_values["actual_cost"] = job_update.actual_cost

        if job_update.manifest_path is not None:
            update_values["manifest_path"] = job_update.manifest_path

        if job_update.logs_path is not None:
            update_values["logs_path"] = job_update.logs_path

        if job_update.last_heartbeat is not None:
            update_values["last_heartbeat"] = job_update.last_heartbeat

        if job_update.airtable_record_id is not None:
            update_values["airtable_record_id"] = job_update.airtable_record_id

        if job_update.airtable_url is not None:
            update_values["airtable_url"] = job_update.airtable_url

        if job_update.media_id is not None:
            update_values["media_id"] = job_update.media_id

        if job_update.duration_minutes is not None:
            update_values["duration_minutes"] = job_update.duration_minutes

        if job_update.word_count is not None:
            update_values["word_count"] = job_update.word_count

        # Handle phases update (replaces all phases)
        if job_update.phases is not None:
            # Use mode='json' to serialize datetime objects to ISO strings
            phases_json = json.dumps([p.model_dump(mode="json") for p in job_update.phases])
            update_values["phases"] = phases_json

        # Handle single phase update
        if job_update.phase_update is not None:
            # First fetch current phases
            stmt = select(jobs_table.c.phases).where(jobs_table.c.id == job_id)
            result = await session.execute(stmt)
            row = result.fetchone()
            if row and row.phases:
                current_phases = json.loads(row.phases)
                # Find and update the specific phase
                for i, phase in enumerate(current_phases):
                    if phase.get("name") == job_update.phase_update.name:
                        # Update only provided fields
                        if job_update.phase_update.status is not None:
                            current_phases[i]["status"] = job_update.phase_update.status.value
                        if job_update.phase_update.started_at is not None:
                            current_phases[i]["started_at"] = job_update.phase_update.started_at.isoformat()
                        if job_update.phase_update.completed_at is not None:
                            current_phases[i]["completed_at"] = job_update.phase_update.completed_at.isoformat()
                        if job_update.phase_update.cost is not None:
                            current_phases[i]["cost"] = job_update.phase_update.cost
                        if job_update.phase_update.tokens is not None:
                            current_phases[i]["tokens"] = job_update.phase_update.tokens
                        if job_update.phase_update.error_message is not None:
                            current_phases[i]["error_message"] = job_update.phase_update.error_message
                        if job_update.phase_update.output_path is not None:
                            current_phases[i]["output_path"] = job_update.phase_update.output_path
                        if job_update.phase_update.metadata is not None:
                            current_phases[i]["metadata"] = job_update.phase_update.metadata
                        break
                update_values["phases"] = json.dumps(current_phases)

        if not update_values:
            # No fields to update, just fetch and return current state
            stmt = select(jobs_table).where(jobs_table.c.id == job_id)
            result = await session.execute(stmt)
            row = result.fetchone()
            return _row_to_job(row) if row else None

        # Execute update
        stmt = update(jobs_table).where(jobs_table.c.id == job_id).values(**update_values)
        result = await session.execute(stmt)

        if result.rowcount == 0:
            return None

        # Fetch and return updated job (within same session)
        stmt = select(jobs_table).where(jobs_table.c.id == job_id)
        result = await session.execute(stmt)
        row = result.fetchone()

        job = _row_to_job(row)

        # Broadcast job update to WebSocket clients
        try:
            from api.routers.websocket import broadcast_job_update

            # Determine event type based on status
            event_type = "job_updated"
            if job.status == JobStatus.completed:
                event_type = "job_completed"
            elif job.status == JobStatus.failed:
                event_type = "job_failed"
            elif job.status == JobStatus.in_progress:
                event_type = "job_started"

            await broadcast_job_update(job, event_type=event_type)
        except Exception:
            # Don't fail job update if broadcast fails
            pass

        return job


async def delete_job(job_id: int) -> bool:
    """Delete a job from the database.

    Args:
        job_id: Job ID to delete

    Returns:
        True if deleted, False if not found
    """
    async with get_session() as session:
        stmt = delete(jobs_table).where(jobs_table.c.id == job_id)
        result = await session.execute(stmt)
        return result.rowcount > 0


async def bulk_delete_jobs_by_status(statuses: List[JobStatus]) -> int:
    """Delete all jobs with the given statuses.

    Args:
        statuses: List of job statuses to delete

    Returns:
        Number of jobs deleted
    """
    if not statuses:
        return 0

    async with get_session() as session:
        status_values = [s.value for s in statuses]
        stmt = delete(jobs_table).where(jobs_table.c.status.in_(status_values))
        result = await session.execute(stmt)
        return result.rowcount


async def update_job_status(
    job_id: int,
    status: JobStatus,
    project_path: Optional[str] = None,
    current_phase: Optional[str] = None,
    error_message: Optional[str] = None,
    actual_cost: Optional[float] = None,
) -> Optional[Job]:
    """Convenience function to update job status with common fields.

    Args:
        job_id: Job ID to update
        status: New job status
        project_path: Optional project output path
        current_phase: Optional current phase name
        error_message: Optional error message (for failed status)
        actual_cost: Optional actual cost

    Returns:
        Updated Job record or None if not found
    """
    update_data = JobUpdate(status=status)

    if current_phase is not None:
        update_data.current_phase = current_phase
    if error_message is not None:
        update_data.error_message = error_message
    if actual_cost is not None:
        update_data.actual_cost = actual_cost

    job = await update_job(job_id, update_data)

    # Handle project_path separately (not in JobUpdate model)
    if project_path is not None and job is not None:
        async with get_session() as session:
            stmt = update(jobs_table).where(jobs_table.c.id == job_id).values(project_path=project_path)
            await session.execute(stmt)

    return job


async def update_job_phase(job_id: int, phases: list) -> Optional[Job]:
    """Update the phases array for a job.

    Args:
        job_id: Job ID to update
        phases: List of phase dictionaries

    Returns:
        Updated Job or None if not found
    """
    async with get_session() as session:
        phases_json = json.dumps(phases, cls=_SafeEncoder)
        stmt = update(jobs_table).where(jobs_table.c.id == job_id).values(phases=phases_json)
        result = await session.execute(stmt)

        if result.rowcount == 0:
            return None

        # Fetch and return updated job
        stmt = select(jobs_table).where(jobs_table.c.id == job_id)
        result = await session.execute(stmt)
        row = result.fetchone()
        return _row_to_job(row) if row else None


async def get_next_pending_job() -> Optional[Job]:
    """Get the next pending job to process (non-atomic, for read-only queries).

    Returns highest priority pending job, or earliest queued if priorities equal.
    WARNING: Use claim_next_job() for worker processing to avoid race conditions.

    Returns:
        Next job to process or None if queue is empty
    """
    async with get_session() as session:
        stmt = (
            select(jobs_table)
            .where(jobs_table.c.status == JobStatus.pending.value)
            .order_by(desc(jobs_table.c.priority), jobs_table.c.queued_at)
            .limit(1)
        )
        result = await session.execute(stmt)
        row = result.fetchone()

        if row is None:
            return None

        return _row_to_job(row)


async def claim_next_job(worker_id: Optional[str] = None) -> Optional[Job]:
    """Atomically claim the next pending job for processing.

    Uses a single UPDATE statement to prevent race conditions when multiple
    workers are running. The job is marked as in_progress and started_at
    is set in one atomic operation.

    Args:
        worker_id: Optional identifier for the worker claiming the job.
                   Useful for debugging and monitoring which worker has which job.

    Returns:
        The claimed job (now in_progress) or None if no pending jobs.
    """
    from sqlalchemy import text

    async with get_session() as session:
        now = datetime.now(timezone.utc)

        # SQLite-compatible atomic claim using UPDATE with subquery
        # This finds the next job and claims it in a single statement
        claim_sql = text(
            """
            UPDATE jobs
            SET status = :new_status,
                started_at = :started_at,
                last_heartbeat = :heartbeat
            WHERE id = (
                SELECT id FROM jobs
                WHERE status = :pending_status
                ORDER BY priority DESC, queued_at ASC
                LIMIT 1
            )
            RETURNING *
        """
        )

        result = await session.execute(
            claim_sql,
            {
                "new_status": JobStatus.in_progress.value,
                "pending_status": JobStatus.pending.value,
                "started_at": now.isoformat(),
                "heartbeat": now.isoformat(),
            },
        )

        row = result.fetchone()
        if row is None:
            return None

        # Convert the raw row to a Job model
        # RETURNING * gives us columns in table definition order
        return _row_to_job(row)


async def update_heartbeat(job_id: int) -> bool:
    """Update the last_heartbeat timestamp for a job.

    Should be called periodically during long processing operations.

    Args:
        job_id: Job ID to update heartbeat for

    Returns:
        True if updated, False if job not found
    """
    async with get_session() as session:
        stmt = update(jobs_table).where(jobs_table.c.id == job_id).values(last_heartbeat=datetime.now(timezone.utc))
        result = await session.execute(stmt)
        return result.rowcount > 0


async def get_stale_jobs(threshold_minutes: int = 10) -> List[Job]:
    """Get jobs that are in_progress but haven't had a heartbeat update within threshold_minutes.

    Returns list of jobs that may be stuck.

    Args:
        threshold_minutes: Minutes since last heartbeat to consider job stale

    Returns:
        List of jobs with stale heartbeats
    """
    async with get_session() as session:
        # Calculate cutoff time
        from datetime import timedelta

        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)

        # Find in_progress jobs with stale or null heartbeat
        stmt = (
            select(jobs_table)
            .where(
                and_(
                    jobs_table.c.status == JobStatus.in_progress.value,
                    func.coalesce(jobs_table.c.last_heartbeat, datetime.min.replace(tzinfo=timezone.utc)) < cutoff_time,
                )
            )
            .order_by(jobs_table.c.id)
        )
        result = await session.execute(stmt)
        rows = result.fetchall()

        return [_row_to_job(row) for row in rows]


async def reset_stuck_jobs(threshold_minutes: int = 10) -> List[Job]:
    """Find and reset jobs that have been in_progress for longer than
    threshold_minutes without a heartbeat update.

    For each stuck job:
    - Set status back to 'pending'
    - Increment retry_count
    - Clear started_at and current_phase
    - Log the reset event to session_stats

    Returns list of jobs that were reset.
    """
    from datetime import timedelta

    async with get_session() as session:
        # Calculate threshold timestamp
        threshold_time = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)

        # Find stuck jobs - in_progress with old heartbeat or no heartbeat
        stmt = select(jobs_table).where(
            and_(
                jobs_table.c.status == JobStatus.in_progress.value,
                func.coalesce(jobs_table.c.last_heartbeat, jobs_table.c.started_at) < threshold_time,
            )
        )
        result = await session.execute(stmt)
        stuck_rows = result.fetchall()

        reset_jobs = []

        for row in stuck_rows:
            job_id = row.id
            new_retry_count = row.retry_count + 1

            # Determine new status based on retry count
            if new_retry_count >= row.max_retries:
                # Max retries exceeded - mark as failed
                update_values = {
                    "status": JobStatus.failed.value,
                    "error_message": "Max retries exceeded after stuck job reset",
                    "error_timestamp": datetime.now(timezone.utc),
                    "retry_count": new_retry_count,
                    "completed_at": datetime.now(timezone.utc),
                }

                # Log job_failed event
                event_data_dict = {
                    "job_id": job_id,
                    "reason": "stuck_job_reset_max_retries",
                    "threshold_minutes": threshold_minutes,
                    "retry_count": new_retry_count,
                    "max_retries": row.max_retries,
                }
                event_values = {
                    "job_id": job_id,
                    "timestamp": datetime.now(timezone.utc),
                    "event_type": EventType.job_failed.value,
                    "data": json.dumps(event_data_dict),
                }
                stmt_event = session_stats_table.insert().values(**event_values)
                await session.execute(stmt_event)
            else:
                # Reset to pending
                update_values = {
                    "status": JobStatus.pending.value,
                    "started_at": None,
                    "current_phase": None,
                    "retry_count": new_retry_count,
                }

                # Log system_error event
                event_data_dict = {
                    "job_id": job_id,
                    "reason": "stuck_job_reset",
                    "threshold_minutes": threshold_minutes,
                    "retry_count": new_retry_count,
                }
                event_values = {
                    "job_id": job_id,
                    "timestamp": datetime.now(timezone.utc),
                    "event_type": EventType.system_error.value,
                    "data": json.dumps(event_data_dict),
                }
                stmt_event = session_stats_table.insert().values(**event_values)
                await session.execute(stmt_event)

            # Update job
            stmt_update = update(jobs_table).where(jobs_table.c.id == job_id).values(**update_values)
            await session.execute(stmt_update)

            # Fetch updated job
            stmt_fetch = select(jobs_table).where(jobs_table.c.id == job_id)
            result_fetch = await session.execute(stmt_fetch)
            updated_row = result_fetch.fetchone()
            reset_jobs.append(_row_to_job(updated_row))

        return reset_jobs


async def run_stuck_job_cleanup(threshold_minutes: int = 10) -> dict:
    """Run the stuck job cleanup routine.

    Returns summary dict with:
    - reset_count: Number of jobs reset to pending
    - failed_count: Number of jobs that exceeded max retries
    - job_ids: List of affected job IDs
    """
    reset_jobs = await reset_stuck_jobs(threshold_minutes)

    reset_count = sum(1 for job in reset_jobs if job.status == JobStatus.pending)
    failed_count = sum(1 for job in reset_jobs if job.status == JobStatus.failed)
    job_ids = [job.id for job in reset_jobs]

    return {
        "reset_count": reset_count,
        "failed_count": failed_count,
        "job_ids": job_ids,
    }


# ============================================================================
# Event Logging Operations
# ============================================================================


async def log_event(event: EventCreate) -> SessionEvent:
    """Log a session event to the database.

    Args:
        event: Event creation schema

    Returns:
        Complete SessionEvent record with generated ID
    """
    async with get_session() as session:
        # Serialize event data to JSON
        data_json = None
        if event.data is not None:
            data_json = event.data.model_dump_json(exclude_none=True)

        values = {
            "job_id": event.job_id,
            "timestamp": datetime.now(timezone.utc),
            "event_type": event.event_type.value,
            "data": data_json,
        }

        stmt = session_stats_table.insert().values(**values)
        result = await session.execute(stmt)
        event_id = result.inserted_primary_key[0]

        # Fetch and return complete event
        stmt = select(session_stats_table).where(session_stats_table.c.id == event_id)
        result = await session.execute(stmt)
        row = result.fetchone()

        return _row_to_event(row)


async def get_events_for_job(job_id: int) -> List[SessionEvent]:
    """Retrieve all events for a specific job.

    Args:
        job_id: Job ID to get events for

    Returns:
        List of SessionEvent records ordered by timestamp
    """
    async with get_session() as session:
        stmt = (
            select(session_stats_table)
            .where(session_stats_table.c.job_id == job_id)
            .order_by(session_stats_table.c.timestamp)
        )
        result = await session.execute(stmt)
        rows = result.fetchall()

        return [_row_to_event(row) for row in rows]


# ============================================================================
# Config Operations
# ============================================================================


async def get_config(key: str) -> Optional[ConfigItem]:
    """Retrieve a configuration value by key.

    Args:
        key: Configuration key

    Returns:
        ConfigItem or None if not found
    """
    async with get_session() as session:
        stmt = select(config_table).where(config_table.c.key == key)
        result = await session.execute(stmt)
        row = result.fetchone()

        if row is None:
            return None

        return _row_to_config(row)


async def set_config(
    key: str,
    value: str,
    value_type: str = "string",
    description: Optional[str] = None,
) -> ConfigItem:
    """Set or update a configuration value.

    Args:
        key: Configuration key
        value: Configuration value (as string)
        value_type: Type of value (string, int, float, bool, json)
        description: Optional description of config item

    Returns:
        Updated or created ConfigItem
    """
    async with get_session() as session:
        # Check if key exists
        stmt = select(config_table).where(config_table.c.key == key)
        result = await session.execute(stmt)
        existing = result.fetchone()

        if existing is not None:
            # Update existing
            update_values = {
                "value": value,
                "value_type": value_type,
                "updated_at": datetime.now(timezone.utc),
            }
            if description is not None:
                update_values["description"] = description

            stmt = update(config_table).where(config_table.c.key == key).values(**update_values)
            await session.execute(stmt)
        else:
            # Insert new
            values = {
                "key": key,
                "value": value,
                "value_type": value_type,
                "description": description,
                "updated_at": datetime.now(timezone.utc),
            }
            stmt = config_table.insert().values(**values)
            await session.execute(stmt)

        # Fetch and return (within same session)
        stmt = select(config_table).where(config_table.c.key == key)
        result = await session.execute(stmt)
        row = result.fetchone()

        return _row_to_config(row)


async def list_config() -> List[ConfigItem]:
    """List all configuration items.

    Returns:
        List of all ConfigItem records
    """
    async with get_session() as session:
        stmt = select(config_table).order_by(config_table.c.key)
        result = await session.execute(stmt)
        rows = result.fetchall()

        return [_row_to_config(row) for row in rows]


# ============================================================================
# Helper Functions
# ============================================================================


def _row_to_job(row) -> Job:
    """Convert database row to Job model.

    Handles JSON deserialization for agent_phases and phases fields,
    derives project_name from project_path, and loads outputs from manifest.
    """
    # Parse agent_phases JSON
    agent_phases = json.loads(row.agent_phases)

    # Parse phases JSON (with fallback for existing rows without phases)
    phases = []
    if hasattr(row, "phases") and row.phases:
        phases_data = json.loads(row.phases)
        phases = [JobPhase(**p) for p in phases_data]
    else:
        # Initialize phases from agent_phases for backward compatibility
        phases = [JobPhase(name=name, status=PhaseStatus.pending) for name in agent_phases]

    # Derive project_name from project_path
    project_name = os.path.basename(row.project_path.rstrip("/"))

    # Load outputs from manifest.json if it exists, but only include files that actually exist
    outputs = None
    manifest_path = os.path.join(row.project_path, "manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r") as f:
                manifest = json.load(f)
                if "outputs" in manifest:
                    # Filter to only include outputs where the file actually exists
                    manifest_outputs = manifest["outputs"]
                    filtered_outputs = {}
                    for key, filename in manifest_outputs.items():
                        if filename:
                            file_path = os.path.join(row.project_path, filename)
                            if os.path.exists(file_path):
                                filtered_outputs[key] = filename

                    # Check for revision files (created by copy editor in Claude Desktop)
                    revision_files = sorted(
                        glob.glob(os.path.join(row.project_path, "copy_revision_v*.md")), reverse=True
                    )
                    if revision_files:
                        # Use latest revision as copy_edited
                        latest_revision = os.path.basename(revision_files[0])
                        filtered_outputs["copy_edited"] = latest_revision

                    # Check for timestamp report (may not be in manifest)
                    timestamp_file = os.path.join(row.project_path, "timestamp_output.md")
                    if os.path.exists(timestamp_file):
                        filtered_outputs["timestamp_report"] = "timestamp_output.md"

                    if filtered_outputs:
                        outputs = JobOutputs(**filtered_outputs)
        except (json.JSONDecodeError, IOError):
            pass  # Ignore errors reading manifest

    return Job(
        id=row.id,
        project_path=row.project_path,
        transcript_file=row.transcript_file,
        project_name=project_name,
        status=JobStatus(row.status),
        priority=row.priority,
        queued_at=row.queued_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        estimated_cost=row.estimated_cost,
        actual_cost=row.actual_cost,
        agent_phases=agent_phases,
        current_phase=row.current_phase,
        phases=phases,
        retry_count=row.retry_count,
        max_retries=row.max_retries,
        error_message=row.error_message,
        error_timestamp=row.error_timestamp,
        manifest_path=row.manifest_path,
        logs_path=row.logs_path,
        last_heartbeat=row.last_heartbeat,
        airtable_record_id=getattr(row, "airtable_record_id", None),
        airtable_url=getattr(row, "airtable_url", None),
        media_id=getattr(row, "media_id", None),
        duration_minutes=getattr(row, "duration_minutes", None),
        word_count=getattr(row, "word_count", None),
        outputs=outputs,
    )


def _row_to_event(row) -> SessionEvent:
    """Convert database row to SessionEvent model.

    Handles JSON deserialization for data field.
    """
    # Parse data JSON if present
    event_data = None
    if row.data is not None:
        event_data = EventData.model_validate_json(row.data)

    return SessionEvent(
        id=row.id,
        job_id=row.job_id,
        timestamp=row.timestamp,
        event_type=EventType(row.event_type),
        data=event_data,
    )


def _row_to_config(row) -> ConfigItem:
    """Convert database row to ConfigItem model."""
    return ConfigItem(
        key=row.key,
        value=row.value,
        value_type=ConfigValueType(row.value_type),
        description=row.description,
        updated_at=row.updated_at,
    )


# ============================================================================
# Chat Session CRUD Operations
# ============================================================================


async def create_chat_session(
    session_id: str,
    job_id: int,
    project_name: str,
) -> ChatSession:
    """Create a new chat session for a job.

    Args:
        session_id: UUID string for the session
        job_id: Associated job ID (must be completed)
        project_name: Project name for context injection

    Returns:
        Created ChatSession record
    """
    async with get_session() as session:
        values = {
            "id": session_id,
            "job_id": job_id,
            "project_name": project_name,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "total_tokens": 0,
            "total_cost": 0.0,
            "message_count": 0,
            "status": ChatSessionStatus.active.value,
        }

        stmt = chat_sessions_table.insert().values(**values)
        await session.execute(stmt)

        # Fetch and return the created session
        stmt = select(chat_sessions_table).where(chat_sessions_table.c.id == session_id)
        result = await session.execute(stmt)
        row = result.fetchone()

        return _row_to_chat_session(row)


async def get_chat_session(session_id: str) -> Optional[ChatSession]:
    """Retrieve a chat session by ID.

    Args:
        session_id: Session UUID

    Returns:
        ChatSession or None if not found
    """
    async with get_session() as session:
        stmt = select(chat_sessions_table).where(chat_sessions_table.c.id == session_id)
        result = await session.execute(stmt)
        row = result.fetchone()

        if row is None:
            return None

        return _row_to_chat_session(row)


async def list_sessions_for_job(job_id: int, include_cleared: bool = False) -> List[ChatSession]:
    """List all chat sessions for a job.

    Args:
        job_id: Job ID to list sessions for
        include_cleared: Whether to include cleared sessions

    Returns:
        List of ChatSession records ordered by created_at desc
    """
    async with get_session() as session:
        stmt = select(chat_sessions_table).where(chat_sessions_table.c.job_id == job_id)

        if not include_cleared:
            stmt = stmt.where(chat_sessions_table.c.status != ChatSessionStatus.cleared.value)

        stmt = stmt.order_by(chat_sessions_table.c.created_at.desc())

        result = await session.execute(stmt)
        rows = result.fetchall()

        return [_row_to_chat_session(row) for row in rows]


async def update_session_stats(
    session_id: str,
    tokens: int,
    cost: float,
    model: Optional[str] = None,
) -> Optional[ChatSession]:
    """Update session statistics after a message exchange.

    Increments total_tokens, total_cost, and message_count.
    Updates the model field if provided.

    Args:
        session_id: Session UUID
        tokens: Tokens to add to total
        cost: Cost to add to total
        model: Model used (updates session's primary model)

    Returns:
        Updated ChatSession or None if not found
    """
    async with get_session() as session:
        # Fetch current stats
        stmt = select(chat_sessions_table).where(chat_sessions_table.c.id == session_id)
        result = await session.execute(stmt)
        row = result.fetchone()

        if row is None:
            return None

        # Calculate new values
        update_values = {
            "total_tokens": row.total_tokens + tokens,
            "total_cost": row.total_cost + cost,
            "message_count": row.message_count + 1,
            "updated_at": datetime.now(timezone.utc),
        }

        if model:
            update_values["model"] = model

        # Update
        stmt = update(chat_sessions_table).where(chat_sessions_table.c.id == session_id).values(**update_values)
        await session.execute(stmt)

        # Fetch and return updated session
        stmt = select(chat_sessions_table).where(chat_sessions_table.c.id == session_id)
        result = await session.execute(stmt)
        row = result.fetchone()

        return _row_to_chat_session(row)


async def update_session_status(
    session_id: str,
    status: ChatSessionStatus,
) -> Optional[ChatSession]:
    """Update session status (archive, clear, etc.).

    Args:
        session_id: Session UUID
        status: New status

    Returns:
        Updated ChatSession or None if not found
    """
    async with get_session() as session:
        stmt = (
            update(chat_sessions_table)
            .where(chat_sessions_table.c.id == session_id)
            .values(status=status.value, updated_at=datetime.now(timezone.utc))
        )
        result = await session.execute(stmt)

        if result.rowcount == 0:
            return None

        # Fetch and return updated session
        stmt = select(chat_sessions_table).where(chat_sessions_table.c.id == session_id)
        result = await session.execute(stmt)
        row = result.fetchone()

        return _row_to_chat_session(row)


# ============================================================================
# Chat Message CRUD Operations
# ============================================================================


async def save_chat_message(
    session_id: str,
    role: str,
    content: str,
    tokens: Optional[int] = None,
    cost: Optional[float] = None,
    model: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> int:
    """Save a chat message to the database.

    Args:
        session_id: Session UUID this message belongs to
        role: Message role (user, assistant, system)
        content: Message content
        tokens: Token count (for assistant messages)
        cost: Cost in USD (for assistant messages)
        model: Model used (for assistant messages)
        duration_ms: Response time (for assistant messages)

    Returns:
        ID of the saved message
    """
    async with get_session() as session:
        values = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "created_at": datetime.now(timezone.utc),
            "tokens": tokens,
            "cost": cost,
            "model": model,
            "duration_ms": duration_ms,
        }

        stmt = chat_messages_table.insert().values(**values)
        result = await session.execute(stmt)

        return result.inserted_primary_key[0]


async def get_session_messages(
    session_id: str,
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[ChatMessage]:
    """Retrieve messages for a chat session.

    Args:
        session_id: Session UUID
        limit: Maximum messages to return (None = all)
        offset: Number of messages to skip

    Returns:
        List of ChatMessage records ordered by created_at asc
    """
    async with get_session() as session:
        stmt = (
            select(chat_messages_table)
            .where(chat_messages_table.c.session_id == session_id)
            .order_by(chat_messages_table.c.created_at.asc())
        )

        if limit:
            stmt = stmt.limit(limit).offset(offset)

        result = await session.execute(stmt)
        rows = result.fetchall()

        return [_row_to_chat_message(row) for row in rows]


async def clear_session_messages(session_id: str) -> int:
    """Delete all messages from a session.

    Args:
        session_id: Session UUID

    Returns:
        Number of messages deleted
    """
    async with get_session() as session:
        stmt = delete(chat_messages_table).where(chat_messages_table.c.session_id == session_id)
        result = await session.execute(stmt)

        # Also update session stats
        stmt = (
            update(chat_sessions_table)
            .where(chat_sessions_table.c.id == session_id)
            .values(
                status=ChatSessionStatus.cleared.value,
                message_count=0,
                total_tokens=0,
                total_cost=0.0,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await session.execute(stmt)

        return result.rowcount


async def get_session_message_count(session_id: str) -> int:
    """Get the count of messages in a session.

    Args:
        session_id: Session UUID

    Returns:
        Message count
    """
    async with get_session() as session:
        stmt = (
            select(func.count()).select_from(chat_messages_table).where(chat_messages_table.c.session_id == session_id)
        )
        result = await session.execute(stmt)
        return result.scalar() or 0


# ============================================================================
# Chat Helper Functions
# ============================================================================


def _row_to_chat_session(row) -> ChatSession:
    """Convert database row to ChatSession model."""
    return ChatSession(
        id=row.id,
        job_id=row.job_id,
        project_name=row.project_name,
        created_at=row.created_at,
        updated_at=row.updated_at,
        total_tokens=row.total_tokens,
        total_cost=row.total_cost,
        message_count=row.message_count,
        status=ChatSessionStatus(row.status),
        model=row.model,
    )


def _row_to_chat_message(row) -> ChatMessage:
    """Convert database row to ChatMessage model."""
    return ChatMessage(
        id=row.id,
        role=row.role,
        content=row.content,
        created_at=row.created_at,
        tokens=row.tokens,
        cost=row.cost,
        model=row.model,
        duration_ms=row.duration_ms,
    )


# ============================================================================
# Convenience Aliases for Worker
# ============================================================================

# Legacy alias - prefer claim_next_job for worker use
get_next_job = claim_next_job
update_job_heartbeat = update_heartbeat
