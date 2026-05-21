"""Tests for export API endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch


class TestExportStatus:
    """Tests for GET /api/export/status."""

    def test_status_returns_configured_false_when_no_creds(self, api_client):
        """Returns google_drive.configured=false when no credentials."""
        with patch("api.routers.export.GoogleDriveService") as MockService:
            MockService.return_value.is_configured.return_value = False
            response = api_client.get("/api/export/status")

        assert response.status_code == 200
        data = response.json()
        assert data["google_drive"]["configured"] is False

    def test_status_returns_configured_true_with_creds(self, api_client):
        """Returns google_drive.configured=true when credentials exist."""
        with patch("api.routers.export.GoogleDriveService") as MockService:
            MockService.return_value.is_configured.return_value = True
            response = api_client.get("/api/export/status")

        assert response.status_code == 200
        data = response.json()
        assert data["google_drive"]["configured"] is True


class TestExportUpload:
    """Tests for POST /api/export/google-drive/{job_id}/{filename}."""

    def test_upload_returns_404_for_missing_job(self, api_client):
        """Returns 404 when job doesn't exist."""
        with patch("api.routers.export.get_job", new_callable=AsyncMock, return_value=None):
            response = api_client.post("/api/export/google-drive/999/analyst_output.md")
        assert response.status_code == 404

    def test_upload_returns_503_when_not_configured(self, api_client):
        """Returns 503 when Google Drive is not configured."""
        mock_job = MagicMock()
        mock_job.project_name = "Test Project"
        mock_job.project_path = "/data/output/test"

        with (
            patch("api.routers.export.get_job", new_callable=AsyncMock, return_value=mock_job),
            patch("api.routers.export.GoogleDriveService") as MockService,
        ):
            MockService.return_value.is_configured.return_value = False
            response = api_client.post("/api/export/google-drive/1/analyst_output.md")

        assert response.status_code == 503

    def test_upload_returns_400_for_invalid_filename(self, api_client):
        """Returns 400 for filenames not in the allowlist."""
        mock_job = MagicMock()
        mock_job.project_name = "Test Project"
        mock_job.project_path = "/data/output/test"

        with patch("api.routers.export.get_job", new_callable=AsyncMock, return_value=mock_job):
            response = api_client.post("/api/export/google-drive/1/malicious_script.sh")

        assert response.status_code == 400

    def test_upload_accepts_copy_revision_filename(self, api_client):
        """copy_revision_vN.md filenames pass the allowlist check."""
        mock_job = MagicMock()
        mock_job.project_name = "Test Project"
        mock_job.project_path = "/data/output/test"

        with (
            patch("api.routers.export.get_job", new_callable=AsyncMock, return_value=mock_job),
            patch("api.routers.export.GoogleDriveService") as MockService,
        ):
            MockService.return_value.is_configured.return_value = False
            response = api_client.post("/api/export/google-drive/1/copy_revision_v3.md")

        # 503 means we passed the filename check (Drive not configured)
        assert response.status_code == 503
