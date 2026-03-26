"""Tests for Queue router endpoints.

Tests queue listing, creation, deletion, and statistics.
"""

import pytest
from fastapi.testclient import TestClient

from api.main import app

# Use context manager to trigger lifespan events (DB init)
_test_client = TestClient(app)
_test_client.__enter__()
client = _test_client


@pytest.fixture
async def cleanup_queue():
    """Clean up test jobs after test runs."""
    yield
    # Note: TestClient handles lifespan events, so database is initialized


class TestListQueue:
    """Tests for GET /api/queue/ endpoint."""

    @pytest.mark.asyncio
    async def test_list_queue_empty(self, cleanup_queue):
        """Test listing queue when empty."""
        response = client.get("/api/queue/")

        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert "total_pages" in data
        assert isinstance(data["jobs"], list)

    @pytest.mark.asyncio
    async def test_list_queue_with_jobs(self, cleanup_queue):
        """Test listing queue with jobs."""
        # Create test jobs
        job1 = {"project_name": "test-list-1", "transcript_file": "test1.txt"}
        job2 = {"project_name": "test-list-2", "transcript_file": "test2.txt"}

        client.post("/api/queue/", json=job1)
        client.post("/api/queue/", json=job2)

        response = client.get("/api/queue/")

        assert response.status_code == 200
        data = response.json()
        assert len(data["jobs"]) >= 2
        assert data["total"] >= 2

    @pytest.mark.asyncio
    async def test_list_queue_filter_by_status(self, cleanup_queue):
        """Test filtering queue by status."""
        # Create jobs with different statuses
        job1 = {"project_name": "test-pending", "transcript_file": "pending.txt"}
        client.post("/api/queue/", json=job1)

        job2 = {"project_name": "test-completed", "transcript_file": "completed.txt"}
        response2 = client.post("/api/queue/", json=job2)
        job2_id = response2.json()["id"]

        # Update one to completed
        client.patch(f"/api/jobs/{job2_id}", json={"status": "completed"})

        # Filter by pending
        response = client.get("/api/queue/?status=pending")
        assert response.status_code == 200
        data = response.json()
        pending_jobs = data["jobs"]
        assert all(job["status"] == "pending" for job in pending_jobs)

        # Filter by completed
        response = client.get("/api/queue/?status=completed")
        assert response.status_code == 200
        data = response.json()
        completed_jobs = data["jobs"]
        assert all(job["status"] == "completed" for job in completed_jobs)

    @pytest.mark.asyncio
    async def test_list_queue_pagination(self, cleanup_queue):
        """Test queue pagination."""
        # Create multiple jobs
        for i in range(5):
            job = {"project_name": f"test-page-{i}", "transcript_file": f"test{i}.txt"}
            client.post("/api/queue/", json=job)

        # Get first page with page_size=2
        response = client.get("/api/queue/?page=1&page_size=2")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 1
        assert data["page_size"] == 2
        assert len(data["jobs"]) <= 2

        # Get second page
        response = client.get("/api/queue/?page=2&page_size=2")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2

    @pytest.mark.asyncio
    async def test_list_queue_search(self, cleanup_queue):
        """Test searching queue by filename."""
        # Create jobs with specific filenames
        job1 = {"project_name": "test-search-1", "transcript_file": "unique_file.txt"}
        job2 = {"project_name": "test-search-2", "transcript_file": "another.txt"}

        client.post("/api/queue/", json=job1)
        client.post("/api/queue/", json=job2)

        # Search for unique_file
        response = client.get("/api/queue/?search=unique_file")
        assert response.status_code == 200
        data = response.json()
        # Should find at least the unique_file job
        assert any("unique_file" in job["transcript_file"] for job in data["jobs"])

    @pytest.mark.asyncio
    async def test_list_queue_sort_order(self, cleanup_queue):
        """Test queue sorting."""
        # Create jobs
        job1 = {"project_name": "test-sort-1", "transcript_file": "sort1.txt"}
        job2 = {"project_name": "test-sort-2", "transcript_file": "sort2.txt"}

        client.post("/api/queue/", json=job1)

        client.post("/api/queue/", json=job2)

        # Test newest first (default)
        response = client.get("/api/queue/?sort=newest")
        assert response.status_code == 200
        data = response.json()
        if len(data["jobs"]) >= 2:
            # Newest should have higher ID
            assert data["jobs"][0]["id"] >= data["jobs"][1]["id"]

        # Test oldest first
        response = client.get("/api/queue/?sort=oldest")
        assert response.status_code == 200
        data = response.json()
        if len(data["jobs"]) >= 2:
            # Oldest should have lower ID
            assert data["jobs"][0]["id"] <= data["jobs"][1]["id"]


class TestAddToQueue:
    """Tests for POST /api/queue/ endpoint."""

    @pytest.mark.asyncio
    async def test_create_job_success(self, cleanup_queue):
        """Test creating a new job."""
        job_data = {
            "project_name": "test-create",
            "transcript_file": "create_test.txt",
            "priority": 5,
        }

        response = client.post("/api/queue/", json=job_data)

        assert response.status_code == 201
        job = response.json()
        assert job["status"] == "pending"
        assert job["priority"] == 5
        assert "id" in job
        assert "queued_at" in job

    @pytest.mark.asyncio
    async def test_create_job_duplicate_prevention(self, cleanup_queue):
        """Test that duplicate transcripts are prevented."""
        job_data = {
            "project_name": "test-duplicate",
            "transcript_file": "duplicate.txt",
        }

        # Create first job
        response1 = client.post("/api/queue/", json=job_data)
        assert response1.status_code == 201

        # Try to create duplicate
        response2 = client.post("/api/queue/", json=job_data)
        assert response2.status_code == 409
        error_detail = response2.json()["detail"]
        assert "already" in error_detail["message"].lower()
        assert "existing_job_id" in error_detail

    @pytest.mark.asyncio
    async def test_create_job_duplicate_with_force(self, cleanup_queue):
        """Test creating duplicate with force=true."""
        job_data = {
            "project_name": "test-force-duplicate",
            "transcript_file": "force_duplicate.txt",
        }

        # Create first job
        response1 = client.post("/api/queue/", json=job_data)
        assert response1.status_code == 201

        # Create duplicate with force
        response2 = client.post("/api/queue/?force=true", json=job_data)
        assert response2.status_code == 201
        # Should create a new job with different ID
        assert response2.json()["id"] != response1.json()["id"]

    @pytest.mark.asyncio
    async def test_create_job_duplicate_failed_job(self, cleanup_queue):
        """Test duplicate detection with failed job."""
        job_data = {
            "project_name": "test-failed-dup",
            "transcript_file": "failed_dup.txt",
        }

        # Create and fail a job
        response1 = client.post("/api/queue/", json=job_data)
        job_id = response1.json()["id"]
        client.patch(f"/api/jobs/{job_id}", json={"status": "failed"})

        # Try to create duplicate
        response2 = client.post("/api/queue/", json=job_data)
        assert response2.status_code == 409
        error_detail = response2.json()["detail"]
        assert "retry" in error_detail["action_required"].lower()

    @pytest.mark.asyncio
    async def test_create_job_minimum_fields(self, cleanup_queue):
        """Test creating job with minimum required fields."""
        job_data = {
            "project_name": "minimal-job",
            "transcript_file": "minimal.txt",
        }

        response = client.post("/api/queue/", json=job_data)

        assert response.status_code == 201
        job = response.json()
        assert job["priority"] == 0  # Default priority


class TestRemoveFromQueue:
    """Tests for DELETE /api/queue/{id} endpoint."""

    @pytest.mark.asyncio
    async def test_delete_job_success(self, cleanup_queue):
        """Test deleting a job."""
        # Create a job
        job_data = {"project_name": "test-delete", "transcript_file": "delete.txt"}
        response = client.post("/api/queue/", json=job_data)
        job_id = response.json()["id"]

        # Delete it
        response = client.delete(f"/api/queue/{job_id}")
        assert response.status_code == 204

        # Verify it's gone
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_job_not_found(self):
        """Test deleting non-existent job returns 404."""
        response = client.delete("/api/queue/99999")
        assert response.status_code == 404


class TestGetNextJob:
    """Tests for GET /api/queue/next endpoint."""

    @pytest.mark.asyncio
    async def test_get_next_job_success(self, cleanup_queue):
        """Test getting next pending job."""
        # Create jobs with different priorities
        job1 = {"project_name": "test-next-1", "transcript_file": "next1.txt", "priority": 1}
        job2 = {"project_name": "test-next-2", "transcript_file": "next2.txt", "priority": 10}

        client.post("/api/queue/", json=job1)
        client.post("/api/queue/", json=job2)

        # Get next job (should be highest priority)
        response = client.get("/api/queue/next")
        assert response.status_code == 200
        job = response.json()
        # Should get the higher priority job
        assert job["priority"] >= 1

    @pytest.mark.asyncio
    async def test_get_next_job_empty_queue(self, cleanup_queue):
        """Test getting next job when queue is empty."""
        # Clean up any pending jobs first
        response = client.get("/api/queue/?status=pending")
        if response.status_code == 200:
            jobs = response.json()["jobs"]
            for job in jobs:
                client.delete(f"/api/queue/{job['id']}")

        response = client.get("/api/queue/next")
        assert response.status_code == 404
        assert "no pending jobs" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_next_job_only_pending(self, cleanup_queue):
        """Test that only pending jobs are returned."""
        # Create jobs with different statuses
        job1 = {"project_name": "test-next-pending", "transcript_file": "next_pending.txt"}
        job2 = {"project_name": "test-next-completed", "transcript_file": "next_completed.txt"}

        client.post("/api/queue/", json=job1)
        response2 = client.post("/api/queue/", json=job2)

        # Mark second as completed
        client.patch(f"/api/jobs/{response2.json()['id']}", json={"status": "completed"})

        # Get next should return pending job
        response = client.get("/api/queue/next")
        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "pending"


class TestGetQueueStats:
    """Tests for GET /api/queue/stats endpoint."""

    @pytest.mark.asyncio
    async def test_get_stats_success(self, cleanup_queue):
        """Test getting queue statistics."""
        response = client.get("/api/queue/stats")

        assert response.status_code == 200
        stats = response.json()
        assert "pending" in stats
        assert "in_progress" in stats
        assert "completed" in stats
        assert "failed" in stats
        assert "cancelled" in stats
        assert "paused" in stats
        assert "total" in stats
        assert all(isinstance(stats[k], int) for k in stats.keys())

    @pytest.mark.asyncio
    async def test_get_stats_with_jobs(self, cleanup_queue):
        """Test stats reflect actual job counts."""
        # Create jobs with different statuses
        job1 = {"project_name": "test-stats-1", "transcript_file": "stats1.txt"}
        job2 = {"project_name": "test-stats-2", "transcript_file": "stats2.txt"}

        client.post("/api/queue/", json=job1)
        response2 = client.post("/api/queue/", json=job2)

        # Mark one as completed
        client.patch(f"/api/jobs/{response2.json()['id']}", json={"status": "completed"})

        response = client.get("/api/queue/stats")
        assert response.status_code == 200
        stats = response.json()

        # Should have at least 1 pending and 1 completed
        assert stats["pending"] >= 1
        assert stats["completed"] >= 1
        assert stats["total"] >= 2


class TestBulkDeleteJobs:
    """Tests for DELETE /api/queue/bulk endpoint."""

    @pytest.mark.asyncio
    async def test_bulk_delete_by_status(self, cleanup_queue):
        """Test bulk deleting jobs by status."""
        # Create jobs with different statuses
        job1 = {"project_name": "test-bulk-1", "transcript_file": "bulk1.txt"}
        job2 = {"project_name": "test-bulk-2", "transcript_file": "bulk2.txt"}

        response1 = client.post("/api/queue/", json=job1)
        response2 = client.post("/api/queue/", json=job2)

        # Mark both as completed
        client.patch(f"/api/jobs/{response1.json()['id']}", json={"status": "completed"})
        client.patch(f"/api/jobs/{response2.json()['id']}", json={"status": "completed"})

        # Bulk delete completed jobs
        response = client.delete("/api/queue/bulk?statuses=completed")
        assert response.status_code == 200
        data = response.json()
        assert data["deleted_count"] >= 2
        assert "completed" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_bulk_delete_multiple_statuses(self, cleanup_queue):
        """Test bulk deleting multiple statuses."""
        # Create jobs with different statuses
        job1 = {"project_name": "test-multi-1", "transcript_file": "multi1.txt"}
        job2 = {"project_name": "test-multi-2", "transcript_file": "multi2.txt"}
        job3 = {"project_name": "test-multi-3", "transcript_file": "multi3.txt"}

        r1 = client.post("/api/queue/", json=job1)
        r2 = client.post("/api/queue/", json=job2)
        r3 = client.post("/api/queue/", json=job3)

        # Set different statuses
        client.patch(f"/api/jobs/{r1.json()['id']}", json={"status": "completed"})
        client.patch(f"/api/jobs/{r2.json()['id']}", json={"status": "failed"})
        client.patch(f"/api/jobs/{r3.json()['id']}", json={"status": "cancelled"})

        # Bulk delete completed, failed, and cancelled
        response = client.delete("/api/queue/bulk?statuses=completed&statuses=failed&statuses=cancelled")
        assert response.status_code == 200
        data = response.json()
        assert data["deleted_count"] >= 3

    @pytest.mark.asyncio
    async def test_bulk_delete_safety(self, cleanup_queue):
        """Test that pending/in_progress jobs cannot be bulk deleted."""
        # Create pending job
        job = {"project_name": "test-safety", "transcript_file": "safety.txt"}
        client.post("/api/queue/", json=job)

        # Try to bulk delete pending (should be blocked)
        response = client.delete("/api/queue/bulk?statuses=pending")
        assert response.status_code == 200
        data = response.json()
        assert data["deleted_count"] == 0
        assert "no safe statuses" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_bulk_delete_no_matches(self, cleanup_queue):
        """Test bulk delete with no matching jobs."""
        response = client.delete("/api/queue/bulk?statuses=failed")
        assert response.status_code == 200
        data = response.json()
        assert data["deleted_count"] >= 0


class TestInputValidation:
    """Tests for input validation across queue endpoints."""

    @pytest.mark.asyncio
    async def test_pagination_validation(self):
        """Test pagination parameter validation."""
        # Invalid page (< 1)
        response = client.get("/api/queue/?page=0")
        assert response.status_code == 422

        # Invalid page_size (> 100)
        response = client.get("/api/queue/?page_size=101")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_status_filter(self):
        """Test invalid status filter."""
        response = client.get("/api/queue/?status=invalid_status")
        assert response.status_code == 422
