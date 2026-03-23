"""Integration tests for Ingest API endpoints.

Tests all ingest router endpoints including configuration, scanning,
transcript queueing, and screengrab attachment.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


class TestIngestConfigEndpoints:
    """Tests for configuration endpoints."""

    def test_get_config_returns_settings(self):
        """Test GET /api/ingest/config returns current configuration."""
        with (
            patch("api.routers.ingest.get_ingest_config", new_callable=AsyncMock) as mock_get,
            patch("api.routers.ingest.get_next_scan_time", new_callable=AsyncMock) as mock_next,
        ):

            mock_config = MagicMock()
            mock_config.enabled = True
            mock_config.scan_interval_hours = 6
            mock_config.scan_time = "09:00"
            mock_config.last_scan_at = None
            mock_config.last_scan_success = None
            mock_config.server_url = "https://example.com"
            mock_config.directories = ["/content"]
            mock_config.ignore_directories = ["/ignore"]

            mock_get.return_value = mock_config
            mock_next.return_value = None

            response = client.get("/api/ingest/config")

            assert response.status_code == 200
            data = response.json()
            assert data["enabled"] is True
            assert data["scan_interval_hours"] == 6
            assert data["scan_time"] == "09:00"
            assert data["server_url"] == "https://example.com"

    def test_put_config_updates_settings(self):
        """Test PUT /api/ingest/config updates configuration."""
        with (
            patch("api.routers.ingest.update_ingest_config", new_callable=AsyncMock) as mock_update,
            patch("api.routers.ingest.get_next_scan_time", new_callable=AsyncMock) as mock_next,
            patch("api.routers.ingest.configure_scheduler", new_callable=AsyncMock),
        ):

            mock_config = MagicMock()
            mock_config.enabled = False
            mock_config.scan_interval_hours = 12
            mock_config.scan_time = "15:30"
            mock_config.last_scan_at = None
            mock_config.last_scan_success = None
            mock_config.server_url = "https://example.com"
            mock_config.directories = ["/content"]
            mock_config.ignore_directories = ["/ignore"]

            mock_update.return_value = mock_config
            mock_next.return_value = None

            updates = {
                "enabled": False,
                "scan_interval_hours": 12,
                "scan_time": "15:30",
            }

            response = client.put("/api/ingest/config", json=updates)

            assert response.status_code == 200
            data = response.json()
            assert data["enabled"] is False
            assert data["scan_interval_hours"] == 12
            assert data["scan_time"] == "15:30"

    def test_put_config_validates_scan_time(self):
        """Test PUT /api/ingest/config rejects invalid time formats."""
        invalid_times = [
            "9:00",  # Single digit hour
            "09:0",  # Single digit minute
            "25:00",  # Hour out of range
            "09:60",  # Minute out of range
            "09-00",  # Wrong separator
            "invalid",  # Not a time
        ]

        for invalid_time in invalid_times:
            updates = {"scan_time": invalid_time}
            response = client.put("/api/ingest/config", json=updates)
            # FastAPI returns 422 for validation errors, 400 for custom validation
            assert response.status_code in [400, 422], f"Should reject {invalid_time}"

    def test_put_config_validates_interval_range(self):
        """Test PUT /api/ingest/config validates interval range."""
        # Note: The actual validation is in the Pydantic model
        # This test documents expected behavior
        with (
            patch("api.routers.ingest.update_ingest_config", new_callable=AsyncMock) as mock_update,
            patch("api.routers.ingest.get_next_scan_time", new_callable=AsyncMock) as mock_next,
            patch("api.routers.ingest.configure_scheduler", new_callable=AsyncMock),
        ):

            mock_config = MagicMock()
            mock_config.enabled = True
            mock_config.scan_interval_hours = 24
            mock_config.scan_time = "09:00"
            mock_config.last_scan_at = None
            mock_config.last_scan_success = None
            mock_config.server_url = "https://example.com"
            mock_config.directories = ["/content"]
            mock_config.ignore_directories = ["/ignore"]

            mock_update.return_value = mock_config
            mock_next.return_value = None

            # Valid interval
            updates = {"scan_interval_hours": 24}
            response = client.put("/api/ingest/config", json=updates)
            assert response.status_code == 200


class TestScanEndpoint:
    """Tests for the scan endpoint."""

    def test_scan_returns_results(self):
        """Test POST /api/ingest/scan returns scan results."""
        with (
            patch("api.routers.ingest.get_ingest_config", new_callable=AsyncMock) as mock_config,
            patch("api.routers.ingest.IngestScanner") as mock_scanner_class,
            patch("api.routers.ingest.record_scan_result", new_callable=AsyncMock),
        ):

            mock_config.return_value = MagicMock(
                server_url="https://example.com",
                directories=["/content"],
            )

            mock_result = MagicMock(
                success=True,
                qc_passed_checked=10,
                new_files_found=5,
                total_files_on_server=20,
                scan_duration_ms=1500,
                new_transcripts=3,
                new_screengrabs=2,
                error_message=None,
            )

            mock_scanner = MagicMock()
            mock_scanner.scan = AsyncMock(return_value=mock_result)
            mock_scanner_class.return_value = mock_scanner

            response = client.post("/api/ingest/scan")

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["qc_passed_checked"] == 10
            assert data["new_files_found"] == 5
            assert data["new_transcripts"] == 3
            assert data["new_screengrabs"] == 2

    def test_scan_handles_errors(self):
        """Test POST /api/ingest/scan returns error on failure."""
        with (
            patch("api.routers.ingest.get_ingest_config", new_callable=AsyncMock) as mock_config,
            patch("api.routers.ingest.IngestScanner") as mock_scanner_class,
            patch("api.routers.ingest.record_scan_result", new_callable=AsyncMock),
        ):

            mock_config.return_value = MagicMock(
                server_url="https://example.com",
                directories=["/content"],
            )

            mock_scanner = MagicMock()
            mock_scanner.scan = AsyncMock(side_effect=Exception("Connection failed"))
            mock_scanner_class.return_value = mock_scanner

            response = client.post("/api/ingest/scan")

            assert response.status_code == 500


class TestAvailableFilesEndpoint:
    """Tests for the available files listing endpoint."""

    def test_list_available_returns_files(self):
        """Test GET /api/ingest/available returns file list."""
        with (
            patch("api.routers.ingest.get_session") as mock_session,
            patch("api.services.airtable.AirtableClient") as mock_airtable_class,
            patch("api.routers.ingest.get_ingest_config", new_callable=AsyncMock) as mock_config,
        ):

            # Mock database session
            mock_db = MagicMock()
            mock_result = MagicMock()
            mock_row = MagicMock()
            mock_row.id = 1
            mock_row.filename = "2WLI1209HD.srt"
            mock_row.media_id = "2WLI1209HD"
            mock_row.file_type = "transcript"
            mock_row.remote_url = "https://example.com/file.srt"
            mock_row.first_seen_at = datetime(2025, 1, 1)
            mock_row.status = "new"

            mock_result.fetchall.return_value = [mock_row]
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_session.return_value.__aenter__.return_value = mock_db

            # Mock count query result
            mock_count_result = MagicMock()
            mock_count_row = MagicMock()
            mock_count_row.count = 1
            mock_count_result.fetchone.return_value = mock_count_row

            # Make execute return different results for different queries
            async def execute_side_effect(query, params):
                if "COUNT" in str(query):
                    return mock_count_result
                return mock_result

            mock_db.execute.side_effect = execute_side_effect

            # Mock Airtable
            mock_airtable = MagicMock()
            mock_airtable.search_sst_by_media_id = AsyncMock(
                return_value={
                    "id": "recXXX",
                    "fields": {"Title": "Test Title", "Project": "Test Project"},
                }
            )
            mock_airtable_class.return_value = mock_airtable

            # Mock config
            mock_config.return_value = MagicMock(last_scan_at=None)

            response = client.get("/api/ingest/available")

            assert response.status_code == 200
            data = response.json()
            assert "files" in data
            assert data["total_new"] == 1

    def test_list_available_filters_by_status(self):
        """Test GET /api/ingest/available respects status filter."""
        with (
            patch("api.routers.ingest.get_session") as mock_session,
            patch("api.services.airtable.AirtableClient") as mock_airtable_class,
            patch("api.routers.ingest.get_ingest_config", new_callable=AsyncMock) as mock_config,
        ):

            # Mock database with no results
            mock_db = MagicMock()
            mock_result = MagicMock()
            mock_result.fetchall.return_value = []
            mock_count_result = MagicMock()
            mock_count_row = MagicMock()
            mock_count_row.count = 0
            mock_count_result.fetchone.return_value = mock_count_row

            async def execute_side_effect(query, params):
                # Count query has different parameters
                if "COUNT" in str(query):
                    # Count query only has file_type
                    assert "file_type" in params
                    return mock_count_result
                # Main query should have status parameter
                assert params.get("status") == "queued"
                return mock_result

            mock_db.execute = AsyncMock(side_effect=execute_side_effect)
            mock_session.return_value.__aenter__.return_value = mock_db

            # Mock Airtable
            mock_airtable_class.return_value = MagicMock()

            # Mock config
            mock_config.return_value = MagicMock(last_scan_at=None)

            response = client.get("/api/ingest/available?status=queued")

            assert response.status_code == 200


class TestTranscriptEndpoints:
    """Tests for transcript action endpoints."""

    def test_queue_transcript_success(self):
        """Test POST /api/ingest/transcripts/{id}/queue successfully queues transcript."""
        with (
            patch("api.routers.ingest.get_ingest_config", new_callable=AsyncMock) as mock_config,
            patch("api.routers.ingest.IngestScanner") as mock_scanner_class,
            patch("api.services.database.create_job", new_callable=AsyncMock) as mock_create_job,
            patch("api.routers.ingest.get_session") as mock_session,
        ):

            # Mock config
            mock_config.return_value = MagicMock(
                server_url="https://example.com",
                directories=["/content"],
            )

            # Mock scanner download
            mock_scanner = MagicMock()
            mock_scanner.download_file = AsyncMock(
                return_value={
                    "success": True,
                    "media_id": "2WLI1209HD",
                    "filename": "2WLI1209HD.srt",
                    "local_path": "transcripts/2WLI1209HD.srt",
                }
            )
            mock_scanner_class.return_value = mock_scanner

            # Mock job creation
            mock_job = MagicMock()
            mock_job.id = 1
            mock_create_job.return_value = mock_job

            # Mock database update
            mock_db = MagicMock()
            mock_db.execute = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db

            response = client.post("/api/ingest/transcripts/1/queue")

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["file_id"] == 1
            assert data["job_id"] == 1

    def test_ignore_transcript(self):
        """Test POST /api/ingest/transcripts/{id}/ignore marks transcript as ignored."""
        with patch("api.routers.ingest.get_session") as mock_session:

            # Mock database update
            mock_db = MagicMock()
            mock_result = MagicMock()
            mock_result.rowcount = 1
            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_session.return_value.__aenter__.return_value = mock_db

            response = client.post("/api/ingest/transcripts/1/ignore")

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True


class TestScreengrabEndpoints:
    """Tests for screengrab endpoints."""

    def test_list_screengrabs(self):
        """Test GET /api/ingest/screengrabs returns screengrab list."""
        with patch("api.routers.ingest.get_session") as mock_session:

            # Mock database session
            mock_db = MagicMock()

            # Mock file list result
            mock_result = MagicMock()
            mock_row = MagicMock()
            mock_row.id = 1
            mock_row.filename = "2WLI1209HD.jpg"
            mock_row.remote_url = "https://example.com/image.jpg"
            mock_row.media_id = "2WLI1209HD"
            mock_row.status = "new"
            mock_row.first_seen_at = datetime(2025, 1, 1)
            mock_row.airtable_record_id = None
            mock_row.attached_at = None

            mock_result.fetchall.return_value = [mock_row]

            # Mock totals result
            mock_totals_result = MagicMock()
            mock_totals_row = MagicMock()
            mock_totals_row.status = "new"
            mock_totals_row.count = 1
            mock_totals_result.fetchall.return_value = [mock_totals_row]

            # Make execute return different results for different queries
            async def execute_side_effect(query, params=None):
                if "GROUP BY" in str(query):
                    return mock_totals_result
                return mock_result

            mock_db.execute = AsyncMock(side_effect=execute_side_effect)
            mock_session.return_value.__aenter__.return_value = mock_db

            response = client.get("/api/ingest/screengrabs")

            assert response.status_code == 200
            data = response.json()
            assert "screengrabs" in data
            assert len(data["screengrabs"]) == 1
            assert data["total_new"] == 1

    def test_attach_screengrab(self):
        """Test POST /api/ingest/screengrabs/{id}/attach attaches screengrab."""
        with patch("api.routers.ingest.get_screengrab_attacher") as mock_get_attacher:

            # Mock attacher
            mock_attacher = MagicMock()
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.media_id = "2WLI1209HD"
            mock_result.filename = "2WLI1209HD.jpg"
            mock_result.sst_record_id = "recXXX"
            mock_result.attachments_before = 0
            mock_result.attachments_after = 1
            mock_result.error_message = None
            mock_result.skipped_duplicate = False

            mock_attacher.attach_from_available_file = AsyncMock(return_value=mock_result)
            mock_get_attacher.return_value = mock_attacher

            response = client.post("/api/ingest/screengrabs/1/attach")

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["media_id"] == "2WLI1209HD"
            assert data["attachments_before"] == 0
            assert data["attachments_after"] == 1
