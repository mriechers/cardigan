"""Google Drive upload service for Cardigan report export.

Uses a Google service account for authentication. The service account
JSON credentials are resolved through the standard secrets system
(Docker secret file → env var → macOS Keychain).

Secret key: GOOGLE_DRIVE_CREDENTIALS (JSON string of service account key)
"""

import json
import logging
from pathlib import Path
from typing import Optional

from api.services.secrets import get_secret

logger = logging.getLogger(__name__)

# Google libraries are optional — only needed when Drive export is configured
try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class GoogleDriveService:
    """Upload files to Google Drive using a service account."""

    def __init__(self) -> None:
        self._creds_json = get_secret("GOOGLE_DRIVE_CREDENTIALS")

    def is_configured(self) -> bool:
        """Check if Google Drive credentials are available."""
        return _GOOGLE_AVAILABLE and self._creds_json is not None

    def _build_service(self):
        """Build the Google Drive API service client."""
        if not self._creds_json:
            return None
        info = json.loads(self._creds_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        return build("drive", "v3", credentials=creds)

    def upload_file(
        self,
        file_path: Path,
        filename: str,
        folder_id: Optional[str] = None,
        mime_type: str = "text/markdown",
    ) -> Optional[dict]:
        """Upload a file to Google Drive.

        Args:
            file_path: Local path to the file to upload.
            filename: Name for the file in Google Drive.
            folder_id: Optional Drive folder ID to upload into.
            mime_type: MIME type of the file.

        Returns:
            Dict with 'id' and 'webViewLink' on success, None on failure.
        """
        if not self.is_configured():
            logger.warning("Google Drive not configured — skipping upload")
            return None

        try:
            service = self._build_service()
            if service is None:
                return None

            metadata: dict = {"name": filename}
            if folder_id:
                metadata["parents"] = [folder_id]

            media = MediaFileUpload(str(file_path), mimetype=mime_type)
            result = (
                service.files()
                .create(body=metadata, media_body=media, fields="id,webViewLink")
                .execute()
            )
            logger.info("Uploaded %s to Google Drive: %s", filename, result.get("id"))
            return result
        except Exception:
            logger.exception("Failed to upload %s to Google Drive", filename)
            return None
