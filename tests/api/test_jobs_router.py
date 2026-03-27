"""Tests for Jobs router endpoints.

Tests job detail, updates, and control operations (pause, resume, retry, cancel).
Uses the session-scoped api_client fixture from conftest.py.
"""

import pytest

# Module-level client will be set by the autouse fixture below
client = None


@pytest.fixture(autouse=True)
def _set_client(api_client):
    """Inject the session-scoped api_client as module-level client."""
    global client
    client = api_client


@pytest.fixture
async def sample_job():
    """Create a sample job for testing."""
    job_data = {
        "project_name": "test-job-router",
        "transcript_file": "test_transcript.txt",
        "priority": 1,
    }
    response = client.post("/api/queue/?force=true", json=job_data)
    assert response.status_code == 201
    return response.json()


@pytest.fixture
async def completed_job():
    """Create a completed job for testing."""
    job_data = {
        "project_name": "test-completed-job",
        "transcript_file": "completed_test.txt",
    }
    response = client.post("/api/queue/?force=true", json=job_data)
    assert response.status_code == 201
    job = response.json()

    # Update to completed status
    update_data = {"status": "completed"}
    response = client.patch(f"/api/jobs/{job['id']}", json=update_data)
    assert response.status_code == 200
    return response.json()


@pytest.fixture
async def failed_job():
    """Create a failed job for testing."""
    job_data = {
        "project_name": "test-failed-job",
        "transcript_file": "failed_test.txt",
    }
    response = client.post("/api/queue/?force=true", json=job_data)
    assert response.status_code == 201
    job = response.json()

    # Update to failed status
    update_data = {"status": "failed", "error_message": "Test failure"}
    response = client.patch(f"/api/jobs/{job['id']}", json=update_data)
    assert response.status_code == 200
    return response.json()


@pytest.fixture
async def paused_job():
    """Create a paused job for testing."""
    job_data = {
        "project_name": "test-paused-job",
        "transcript_file": "paused_test.txt",
    }
    response = client.post("/api/queue/?force=true", json=job_data)
    assert response.status_code == 201
    job = response.json()

    # Pause the job
    response = client.post(f"/api/jobs/{job['id']}/pause")
    assert response.status_code == 200
    return response.json()


class TestGetJobDetail:
    """Tests for GET /api/jobs/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_job_success(self, sample_job):
        """Test retrieving an existing job."""
        job_id = sample_job["id"]
        response = client.get(f"/api/jobs/{job_id}")

        assert response.status_code == 200
        job = response.json()
        assert job["id"] == job_id
        assert job["status"] == "pending"
        assert "transcript_file" in job
        assert "project_path" in job

    @pytest.mark.asyncio
    async def test_get_job_not_found(self):
        """Test 404 when job doesn't exist."""
        response = client.get("/api/jobs/99999")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestUpdateJobFields:
    """Tests for PATCH /api/jobs/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_update_job_status(self, sample_job):
        """Test updating job status."""
        job_id = sample_job["id"]
        update_data = {"status": "in_progress"}

        response = client.patch(f"/api/jobs/{job_id}", json=update_data)

        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_update_job_priority(self, sample_job):
        """Test updating job priority."""
        job_id = sample_job["id"]
        update_data = {"priority": 10}

        response = client.patch(f"/api/jobs/{job_id}", json=update_data)

        assert response.status_code == 200
        job = response.json()
        assert job["priority"] == 10

    @pytest.mark.asyncio
    async def test_update_job_multiple_fields(self, sample_job):
        """Test updating multiple fields at once."""
        job_id = sample_job["id"]
        update_data = {
            "status": "in_progress",
            "current_phase": "analyst",
            "priority": 5,
        }

        response = client.patch(f"/api/jobs/{job_id}", json=update_data)

        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "in_progress"
        assert job["current_phase"] == "analyst"
        assert job["priority"] == 5

    @pytest.mark.asyncio
    async def test_update_job_not_found(self):
        """Test 404 when updating non-existent job."""
        update_data = {"status": "completed"}
        response = client.patch("/api/jobs/99999", json=update_data)

        assert response.status_code == 404


class TestPauseJob:
    """Tests for POST /api/jobs/{id}/pause endpoint."""

    @pytest.mark.asyncio
    async def test_pause_pending_job(self, sample_job):
        """Test pausing a pending job."""
        job_id = sample_job["id"]

        response = client.post(f"/api/jobs/{job_id}/pause")

        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "paused"

    @pytest.mark.asyncio
    async def test_pause_in_progress_job(self, sample_job):
        """Test pausing an in-progress job."""
        job_id = sample_job["id"]

        # First set to in_progress
        client.patch(f"/api/jobs/{job_id}", json={"status": "in_progress"})

        response = client.post(f"/api/jobs/{job_id}/pause")

        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "paused"

    @pytest.mark.asyncio
    async def test_pause_completed_job_invalid(self, completed_job):
        """Test that pausing a completed job returns 400."""
        job_id = completed_job["id"]

        response = client.post(f"/api/jobs/{job_id}/pause")

        assert response.status_code == 400
        assert "cannot pause" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_pause_failed_job_invalid(self, failed_job):
        """Test that pausing a failed job returns 400."""
        job_id = failed_job["id"]

        response = client.post(f"/api/jobs/{job_id}/pause")

        assert response.status_code == 400
        assert "cannot pause" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_pause_job_not_found(self):
        """Test 404 when pausing non-existent job."""
        response = client.post("/api/jobs/99999/pause")

        assert response.status_code == 404


class TestResumeJob:
    """Tests for POST /api/jobs/{id}/resume endpoint."""

    @pytest.mark.asyncio
    async def test_resume_paused_job(self, paused_job):
        """Test resuming a paused job."""
        job_id = paused_job["id"]

        response = client.post(f"/api/jobs/{job_id}/resume")

        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "pending"

    @pytest.mark.asyncio
    async def test_resume_pending_job_invalid(self, sample_job):
        """Test that resuming a pending job returns 400."""
        job_id = sample_job["id"]

        response = client.post(f"/api/jobs/{job_id}/resume")

        assert response.status_code == 400
        assert "cannot resume" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_resume_completed_job_invalid(self, completed_job):
        """Test that resuming a completed job returns 400."""
        job_id = completed_job["id"]

        response = client.post(f"/api/jobs/{job_id}/resume")

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_resume_job_not_found(self):
        """Test 404 when resuming non-existent job."""
        response = client.post("/api/jobs/99999/resume")

        assert response.status_code == 404


class TestRetryJob:
    """Tests for POST /api/jobs/{id}/retry endpoint."""

    @pytest.mark.asyncio
    async def test_retry_failed_job(self, failed_job):
        """Test retrying a failed job."""
        job_id = failed_job["id"]

        response = client.post(f"/api/jobs/{job_id}/retry")

        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "pending"
        assert job["error_message"] == ""
        assert job["current_phase"] is None

    @pytest.mark.asyncio
    async def test_retry_pending_job_invalid(self, sample_job):
        """Test that retrying a pending job returns 400."""
        job_id = sample_job["id"]

        response = client.post(f"/api/jobs/{job_id}/retry")

        assert response.status_code == 400
        assert "cannot retry" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_retry_completed_job_invalid(self, completed_job):
        """Test that retrying a completed job returns 400."""
        job_id = completed_job["id"]

        response = client.post(f"/api/jobs/{job_id}/retry")

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_retry_job_not_found(self):
        """Test 404 when retrying non-existent job."""
        response = client.post("/api/jobs/99999/retry")

        assert response.status_code == 404


class TestCancelJob:
    """Tests for POST /api/jobs/{id}/cancel endpoint."""

    @pytest.mark.asyncio
    async def test_cancel_pending_job(self, sample_job):
        """Test cancelling a pending job."""
        job_id = sample_job["id"]

        response = client.post(f"/api/jobs/{job_id}/cancel")

        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_in_progress_job(self, sample_job):
        """Test cancelling an in-progress job."""
        job_id = sample_job["id"]

        # Set to in_progress
        client.patch(f"/api/jobs/{job_id}", json={"status": "in_progress"})

        response = client.post(f"/api/jobs/{job_id}/cancel")

        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_paused_job(self, paused_job):
        """Test cancelling a paused job."""
        job_id = paused_job["id"]

        response = client.post(f"/api/jobs/{job_id}/cancel")

        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_completed_job_invalid(self, completed_job):
        """Test that cancelling a completed job returns 400."""
        job_id = completed_job["id"]

        response = client.post(f"/api/jobs/{job_id}/cancel")

        assert response.status_code == 400
        assert "cannot cancel" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_cancel_failed_job_invalid(self, failed_job):
        """Test that cancelling a failed job returns 400."""
        job_id = failed_job["id"]

        response = client.post(f"/api/jobs/{job_id}/cancel")

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_cancel_job_not_found(self):
        """Test 404 when cancelling non-existent job."""
        response = client.post("/api/jobs/99999/cancel")

        assert response.status_code == 404


class TestJobEvents:
    """Tests for GET /api/jobs/{id}/events endpoint."""

    @pytest.mark.asyncio
    async def test_get_events_for_job(self, sample_job):
        """Test retrieving events for a job."""
        job_id = sample_job["id"]

        response = client.get(f"/api/jobs/{job_id}/events")

        assert response.status_code == 200
        events = response.json()
        assert isinstance(events, list)

    @pytest.mark.asyncio
    async def test_get_events_job_not_found(self):
        """Test 404 when getting events for non-existent job."""
        response = client.get("/api/jobs/99999/events")

        assert response.status_code == 404


class TestJobOutputs:
    """Tests for GET /api/jobs/{id}/outputs/{filename} endpoint."""

    @pytest.mark.asyncio
    async def test_get_output_job_not_found(self):
        """Test 404 when getting output for non-existent job."""
        response = client.get("/api/jobs/99999/outputs/analyst_output.md")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_output_invalid_filename(self, sample_job):
        """Test 400 when requesting invalid filename."""
        job_id = sample_job["id"]

        response = client.get(f"/api/jobs/{job_id}/outputs/malicious_file.sh")

        assert response.status_code == 400
        assert "invalid filename" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_output_allowed_filenames(self, sample_job):
        """Test that allowed filenames are validated correctly."""
        job_id = sample_job["id"]

        allowed_files = [
            "analyst_output.md",
            "formatter_output.md",
            "seo_output.md",
            "manager_output.md",
            "copy_editor_output.md",
            "recovery_analysis.md",
            "manifest.json",
        ]

        for filename in allowed_files:
            response = client.get(f"/api/jobs/{job_id}/outputs/{filename}")
            # Will get 404 because files don't exist, but not 400 (invalid filename)
            assert response.status_code in [200, 404]
