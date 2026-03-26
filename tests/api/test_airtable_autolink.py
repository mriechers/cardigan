"""Tests for Airtable auto-linking functionality on job creation.

Tests that jobs are automatically linked to SST records when created,
with proper error handling when Airtable is unavailable.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.models.job import Job, JobCreate, JobStatus
from api.routers.queue import add_to_queue


def _make_request():
    """Create a mock Request object for rate-limited endpoints."""
    from starlette.requests import Request

    scope = {"type": "http", "method": "POST", "path": "/api/queue/", "headers": []}
    return Request(scope)


@pytest.mark.asyncio
async def test_add_to_queue_with_successful_airtable_lookup():
    """Test that job creation successfully links to Airtable SST record."""
    # Mock job creation
    job_create = JobCreate(
        project_name="Test Project",
        transcript_file="2WLI1209HD_ForClaude.txt",
        priority=0,
    )

    # Mock created job
    mock_job = Job(
        id=1,
        project_path="/path/to/project",
        transcript_file="2WLI1209HD_ForClaude.txt",
        status=JobStatus.pending,
        priority=0,
        queued_at="2025-01-01T00:00:00Z",
        estimated_cost=0.0,
        actual_cost=0.0,
        retry_count=0,
        max_retries=3,
    )

    # Mock updated job with Airtable data
    mock_updated_job = Job(
        id=1,
        project_path="/path/to/project",
        transcript_file="2WLI1209HD_ForClaude.txt",
        status=JobStatus.pending,
        priority=0,
        queued_at="2025-01-01T00:00:00Z",
        estimated_cost=0.0,
        actual_cost=0.0,
        retry_count=0,
        max_retries=3,
        media_id="2WLI1209HD",
        airtable_record_id="recXXXXXXXXXXXXXX",
        airtable_url="https://airtable.com/appZ2HGwhiifQToB6/tblTKFOwTvK7xw1H5/recXXXXXXXXXXXXXX",
    )

    # Mock Airtable record response
    mock_record = {
        "id": "recXXXXXXXXXXXXXX",
        "fields": {"Media ID": "2WLI1209HD"},
        "createdTime": "2025-01-01T00:00:00.000Z",
    }

    with (
        patch("api.routers.queue.database.find_jobs_by_transcript", new_callable=AsyncMock) as mock_find,
        patch("api.routers.queue.database.find_jobs_by_media_id", new_callable=AsyncMock) as mock_find_media,
        patch("api.routers.queue.database.create_job", new_callable=AsyncMock) as mock_create,
        patch("api.routers.queue.database.update_job", new_callable=AsyncMock) as mock_update,
        patch("api.routers.queue.AirtableClient") as mock_airtable_class,
    ):

        # Setup mocks
        mock_find.return_value = []
        mock_find_media.return_value = []
        mock_create.return_value = mock_job
        mock_update.return_value = mock_updated_job

        # Mock AirtableClient instance
        mock_airtable = MagicMock()
        mock_airtable.search_sst_by_media_id = AsyncMock(return_value=mock_record)
        mock_airtable.get_sst_url.return_value = (
            "https://airtable.com/appZ2HGwhiifQToB6/tblTKFOwTvK7xw1H5/recXXXXXXXXXXXXXX"
        )
        mock_airtable_class.return_value = mock_airtable

        # Execute
        result = await add_to_queue(_make_request(), job_create, force=False)

        # Verify
        assert result.id == 1
        assert result.media_id == "2WLI1209HD"
        assert result.airtable_record_id == "recXXXXXXXXXXXXXX"
        assert result.airtable_url == "https://airtable.com/appZ2HGwhiifQToB6/tblTKFOwTvK7xw1H5/recXXXXXXXXXXXXXX"

        # Verify Airtable client was called with correct media_id
        mock_airtable.search_sst_by_media_id.assert_called_once_with("2WLI1209HD")


@pytest.mark.asyncio
async def test_add_to_queue_airtable_not_found():
    """Test that job creation continues when SST record not found."""
    job_create = JobCreate(
        project_name="Test Project",
        transcript_file="UNKNOWN_MEDIA_ID.txt",
        priority=0,
    )

    mock_job = Job(
        id=2,
        project_path="/path/to/project",
        transcript_file="UNKNOWN_MEDIA_ID.txt",
        status=JobStatus.pending,
        priority=0,
        queued_at="2025-01-01T00:00:00Z",
        estimated_cost=0.0,
        actual_cost=0.0,
        retry_count=0,
        max_retries=3,
    )

    mock_updated_job = Job(
        id=2,
        project_path="/path/to/project",
        transcript_file="UNKNOWN_MEDIA_ID.txt",
        status=JobStatus.pending,
        priority=0,
        queued_at="2025-01-01T00:00:00Z",
        estimated_cost=0.0,
        actual_cost=0.0,
        retry_count=0,
        max_retries=3,
        media_id="UNKNOWN_MEDIA_ID",
        airtable_record_id=None,
        airtable_url=None,
    )

    with (
        patch("api.routers.queue.database.find_jobs_by_transcript", new_callable=AsyncMock) as mock_find,
        patch("api.routers.queue.database.find_jobs_by_media_id", new_callable=AsyncMock) as mock_find_media,
        patch("api.routers.queue.database.create_job", new_callable=AsyncMock) as mock_create,
        patch("api.routers.queue.database.update_job", new_callable=AsyncMock) as mock_update,
        patch("api.routers.queue.AirtableClient") as mock_airtable_class,
    ):

        mock_find.return_value = []
        mock_find_media.return_value = []
        mock_create.return_value = mock_job
        mock_update.return_value = mock_updated_job

        # Mock AirtableClient - return None (not found)
        mock_airtable = MagicMock()
        mock_airtable.search_sst_by_media_id = AsyncMock(return_value=None)
        mock_airtable_class.return_value = mock_airtable

        # Execute
        result = await add_to_queue(_make_request(), job_create, force=False)

        # Verify job was created with media_id but no Airtable link
        assert result.id == 2
        assert result.media_id == "UNKNOWN_MEDIA_ID"
        assert result.airtable_record_id is None
        assert result.airtable_url is None


@pytest.mark.asyncio
async def test_add_to_queue_airtable_api_key_missing():
    """Test that job creation continues when Airtable API key not configured."""
    job_create = JobCreate(
        project_name="Test Project",
        transcript_file="2WLI1209HD.txt",
        priority=0,
    )

    mock_job = Job(
        id=3,
        project_path="/path/to/project",
        transcript_file="2WLI1209HD.txt",
        status=JobStatus.pending,
        priority=0,
        queued_at="2025-01-01T00:00:00Z",
        estimated_cost=0.0,
        actual_cost=0.0,
        retry_count=0,
        max_retries=3,
    )

    with (
        patch("api.routers.queue.database.find_jobs_by_transcript", new_callable=AsyncMock) as mock_find,
        patch("api.routers.queue.database.find_jobs_by_media_id", new_callable=AsyncMock) as mock_find_media,
        patch("api.routers.queue.database.create_job", new_callable=AsyncMock) as mock_create,
        patch("api.routers.queue.database.update_job", new_callable=AsyncMock) as mock_update,
        patch("api.routers.queue.AirtableClient") as mock_airtable_class,
    ):

        mock_find.return_value = []
        mock_find_media.return_value = []
        mock_create.return_value = mock_job
        mock_update.return_value = mock_job

        # Mock AirtableClient to raise ValueError (API key not set)
        mock_airtable_class.side_effect = ValueError("Airtable API key required")

        # Execute
        result = await add_to_queue(_make_request(), job_create, force=False)

        # Verify job was created despite Airtable error
        assert result.id == 3
        # Job creation should succeed even though Airtable failed


@pytest.mark.asyncio
async def test_add_to_queue_airtable_api_error():
    """Test that job creation continues when Airtable API fails."""
    job_create = JobCreate(
        project_name="Test Project",
        transcript_file="2WLI1209HD.txt",
        priority=0,
    )

    mock_job = Job(
        id=4,
        project_path="/path/to/project",
        transcript_file="2WLI1209HD.txt",
        status=JobStatus.pending,
        priority=0,
        queued_at="2025-01-01T00:00:00Z",
        estimated_cost=0.0,
        actual_cost=0.0,
        retry_count=0,
        max_retries=3,
    )

    with (
        patch("api.routers.queue.database.find_jobs_by_transcript", new_callable=AsyncMock) as mock_find,
        patch("api.routers.queue.database.find_jobs_by_media_id", new_callable=AsyncMock) as mock_find_media,
        patch("api.routers.queue.database.create_job", new_callable=AsyncMock) as mock_create,
        patch("api.routers.queue.database.update_job", new_callable=AsyncMock) as mock_update,
        patch("api.routers.queue.AirtableClient") as mock_airtable_class,
    ):

        mock_find.return_value = []
        mock_find_media.return_value = []
        mock_create.return_value = mock_job
        mock_update.return_value = mock_job

        # Mock AirtableClient to raise HTTP error
        mock_airtable = MagicMock()
        mock_airtable.search_sst_by_media_id = AsyncMock(side_effect=Exception("Airtable API connection failed"))
        mock_airtable_class.return_value = mock_airtable

        # Execute
        result = await add_to_queue(_make_request(), job_create, force=False)

        # Verify job was created despite Airtable error
        assert result.id == 4


@pytest.mark.asyncio
async def test_media_id_extraction():
    """Test that media IDs are correctly extracted from various filename formats."""
    test_cases = [
        ("2WLI1209HD_ForClaude.txt", "2WLI1209HD"),
        ("9UNP2005HD.srt", "9UNP2005HD"),
        ("2BUC0000HDWEB02_REV20251202.srt", "2BUC0000HDWEB02_REV20251202"),
        ("2WLI1209HD_ForClaude_REV20251202.txt", "2WLI1209HD_REV20251202"),
    ]

    for filename, expected_media_id in test_cases:
        job_create = JobCreate(
            project_name="Test Project",
            transcript_file=filename,
            priority=0,
        )

        mock_job = Job(
            id=1,
            project_path="/path/to/project",
            transcript_file=filename,
            status=JobStatus.pending,
            priority=0,
            queued_at="2025-01-01T00:00:00Z",
            estimated_cost=0.0,
            actual_cost=0.0,
            retry_count=0,
            max_retries=3,
            media_id=expected_media_id,
        )

        with (
            patch("api.routers.queue.database.find_jobs_by_transcript", new_callable=AsyncMock) as mock_find,
            patch("api.routers.queue.database.find_jobs_by_media_id", new_callable=AsyncMock) as mock_find_media,
            patch("api.routers.queue.database.create_job", new_callable=AsyncMock) as mock_create,
            patch("api.routers.queue.database.update_job", new_callable=AsyncMock) as mock_update,
            patch("api.routers.queue.AirtableClient") as mock_airtable_class,
        ):

            mock_find.return_value = []
            mock_find_media.return_value = []
            mock_create.return_value = mock_job
            mock_update.return_value = mock_job

            # Mock AirtableClient - return None (not found)
            mock_airtable = MagicMock()
            mock_airtable.search_sst_by_media_id = AsyncMock(return_value=None)
            mock_airtable_class.return_value = mock_airtable

            # Execute
            result = await add_to_queue(_make_request(), job_create, force=False)

            # Verify media_id was correctly extracted
            assert result.media_id == expected_media_id

            # Verify Airtable was called with correct media_id
            mock_airtable.search_sst_by_media_id.assert_called_once_with(expected_media_id)
