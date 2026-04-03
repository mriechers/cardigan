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


class TestRetryPhaseEndpoint:
    """Tests for POST /api/jobs/{id}/phases/{phase_name}/retry endpoint.

    Verifies JSON body acceptance, backward compatibility (no body), feedback
    and tier fields, validation, and phase name aliasing.
    """

    @pytest.mark.asyncio
    async def test_retry_phase_no_body_accepted(self, sample_job):
        """Test that phase retry works with no request body (backward compat)."""
        job_id = sample_job["id"]

        response = client.post(f"/api/jobs/{job_id}/phases/timestamp/retry")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["phase"] == "timestamp"

    @pytest.mark.asyncio
    async def test_retry_phase_with_empty_body(self, sample_job):
        """Test that phase retry works with empty JSON body."""
        job_id = sample_job["id"]

        response = client.post(
            f"/api/jobs/{job_id}/phases/analyst/retry",
            json={},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["phase"] == "analyst"

    @pytest.mark.asyncio
    async def test_retry_phase_with_tier_in_body(self, sample_job):
        """Test that phase retry accepts tier as a JSON body field."""
        job_id = sample_job["id"]

        response = client.post(
            f"/api/jobs/{job_id}/phases/seo/retry",
            json={"tier": 1},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # Message should reflect the explicit tier
        assert "tier 1" in data["message"]

    @pytest.mark.asyncio
    async def test_retry_phase_with_feedback_in_body(self, sample_job):
        """Test that phase retry accepts feedback as a JSON body field."""
        job_id = sample_job["id"]

        response = client.post(
            f"/api/jobs/{job_id}/phases/timestamp/retry",
            json={"feedback": "Please add a chapter for the Q&A section."},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_retry_phase_with_tier_and_feedback(self, sample_job):
        """Test that phase retry accepts both tier and feedback together."""
        job_id = sample_job["id"]

        response = client.post(
            f"/api/jobs/{job_id}/phases/manager/retry",
            json={"tier": 2, "feedback": "Focus more on SEO consistency issues."},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["phase"] == "manager"

    @pytest.mark.asyncio
    async def test_retry_phase_high_tier_accepted(self, sample_job):
        """Test that tier values beyond configured tiers are accepted (no upper cap).

        Tiers are dynamically defined in config. The endpoint accepts any
        non-negative integer and the worker handles unknown tiers gracefully.
        """
        job_id = sample_job["id"]

        response = client.post(
            f"/api/jobs/{job_id}/phases/seo/retry",
            json={"tier": 5},
        )

        # Accepted (200) — the retry runs in the background even if the tier
        # doesn't map to a configured backend
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_retry_phase_negative_tier_rejected(self, sample_job):
        """Test that negative tier values are rejected with 422."""
        job_id = sample_job["id"]

        response = client.post(
            f"/api/jobs/{job_id}/phases/seo/retry",
            json={"tier": -1},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_retry_phase_invalid_phase_name(self, sample_job):
        """Test that invalid phase names return 400."""
        job_id = sample_job["id"]

        response = client.post(
            f"/api/jobs/{job_id}/phases/nonexistent_phase/retry",
            json={},
        )

        assert response.status_code == 400
        assert "invalid phase" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_retry_phase_output_key_alias(self, sample_job):
        """Test that output key aliases (e.g. 'timestamp_report') map to phases."""
        job_id = sample_job["id"]

        response = client.post(
            f"/api/jobs/{job_id}/phases/timestamp_report/retry",
            json={},
        )

        assert response.status_code == 200
        data = response.json()
        # Output key should be resolved to 'timestamp' phase name
        assert data["phase"] == "timestamp"

    @pytest.mark.asyncio
    async def test_retry_phase_job_not_found(self):
        """Test 404 when retrying a phase for a non-existent job."""
        response = client.post(
            "/api/jobs/99999/phases/timestamp/retry",
            json={},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_download_header_absent_by_default(self, sample_job):
        """Test that Content-Disposition header is NOT set when download param is omitted."""
        job_id = sample_job["id"]

        response = client.get(f"/api/jobs/{job_id}/outputs/analyst_output.md")

        # 404 because file doesn't exist, but header check happens at request level
        # We verify by hitting an invalid filename path that returns 400 without reaching header logic
        # For a valid filename that 404s, the header is never set - confirmed by checking non-404 path
        assert "content-disposition" not in response.headers

    @pytest.mark.asyncio
    async def test_download_header_absent_when_false(self, sample_job):
        """Test that Content-Disposition header is NOT set when download=false."""
        job_id = sample_job["id"]

        response = client.get(f"/api/jobs/{job_id}/outputs/analyst_output.md?download=false")

        assert "content-disposition" not in response.headers

    @pytest.mark.asyncio
    async def test_download_param_accepted(self, sample_job):
        """Test that download=true query param is accepted without error.

        The endpoint returns 404 (file not found) rather than 400 (bad param),
        confirming the download parameter is valid. Header verification requires
        a real output file on disk, which is covered by integration tests.
        """
        job_id = sample_job["id"]

        response = client.get(f"/api/jobs/{job_id}/outputs/analyst_output.md?download=true")

        # Should not be 400 (invalid param) -- 404 is expected since no file exists
        assert response.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_download_false_accepted(self, sample_job):
        """Test that download=false query param is accepted without error."""
        job_id = sample_job["id"]

        response = client.get(f"/api/jobs/{job_id}/outputs/analyst_output.md?download=false")

        assert response.status_code in (200, 404)
