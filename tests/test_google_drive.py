"""Tests for Google Drive upload service."""

from unittest.mock import MagicMock, patch


class TestGoogleDriveService:
    """Tests for GoogleDriveService."""

    def test_is_configured_returns_false_when_no_credentials(self):
        """Returns False when no service account credentials are available."""
        from api.services.google_drive import GoogleDriveService

        with patch("api.services.google_drive.get_secret", return_value=None):
            service = GoogleDriveService()
            assert service.is_configured() is False

    def test_is_configured_returns_true_with_credentials(self):
        """Returns True when service account JSON is available."""
        from api.services.google_drive import GoogleDriveService

        creds_json = '{"type": "service_account", "project_id": "test"}'
        with patch("api.services.google_drive.get_secret", return_value=creds_json):
            service = GoogleDriveService()
            assert service.is_configured() is True

    def test_upload_file_returns_file_id_and_url(self, tmp_path):
        """upload_file returns a dict with id and webViewLink."""
        from api.services.google_drive import GoogleDriveService

        test_file = tmp_path / "test_output.md"
        test_file.write_text("# Test Content\n\nHello world.")

        mock_service = MagicMock()
        mock_create = mock_service.files.return_value.create.return_value
        mock_create.execute.return_value = {
            "id": "abc123",
            "webViewLink": "https://drive.google.com/file/d/abc123/view",
        }

        creds_json = '{"type": "service_account", "project_id": "test"}'
        with (
            patch("api.services.google_drive.get_secret", return_value=creds_json),
            patch("api.services.google_drive.GoogleDriveService._build_service", return_value=mock_service),
        ):
            service = GoogleDriveService()
            result = service.upload_file(test_file, filename="test_output.md")

        assert result["id"] == "abc123"
        assert "drive.google.com" in result["webViewLink"]

    def test_upload_file_uses_folder_id_when_provided(self, tmp_path):
        """upload_file passes folder_id as parent when specified."""
        from api.services.google_drive import GoogleDriveService

        test_file = tmp_path / "test_output.md"
        test_file.write_text("# Content")

        mock_service = MagicMock()
        mock_create = mock_service.files.return_value.create.return_value
        mock_create.execute.return_value = {"id": "abc", "webViewLink": "https://drive.google.com/file/d/abc/view"}

        creds_json = '{"type": "service_account", "project_id": "test"}'
        with (
            patch("api.services.google_drive.get_secret", return_value=creds_json),
            patch("api.services.google_drive.GoogleDriveService._build_service", return_value=mock_service),
        ):
            service = GoogleDriveService()
            service.upload_file(test_file, filename="output.md", folder_id="folder123")

        # Verify the create call included the parent folder
        create_call = mock_service.files.return_value.create.call_args
        metadata = create_call.kwargs.get("body") or create_call[1].get("body")
        assert metadata["parents"] == ["folder123"]

    def test_upload_file_returns_none_when_not_configured(self, tmp_path):
        """upload_file returns None when service is not configured."""
        from api.services.google_drive import GoogleDriveService

        test_file = tmp_path / "test.md"
        test_file.write_text("content")

        with patch("api.services.google_drive.get_secret", return_value=None):
            service = GoogleDriveService()
            result = service.upload_file(test_file, filename="test.md")

        assert result is None
