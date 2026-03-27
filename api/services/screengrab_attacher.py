"""
Screengrab Attacher Service

Attaches screengrab images to Airtable SST records.

POLICY EXCEPTION: This service has CONTROLLED WRITE ACCESS to Airtable,
specifically for the Screen Grab attachment field only. This exception
is explicitly authorized for the Remote Ingest Watcher feature.

SAFETY GUARANTEES:
- ADDITIVE ONLY: Never removes or replaces existing attachments
- AUDIT LOGGED: All attachment operations logged to screengrab_attachments table
- IDEMPOTENT: Re-running on same file won't duplicate attachments
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import httpx

from api.services.airtable import AirtableClient, get_secret
from api.services.database import get_session

logger = logging.getLogger(__name__)


@dataclass
class AttachResult:
    """Result of a single screengrab attachment operation."""

    success: bool
    media_id: str
    filename: str
    sst_record_id: Optional[str] = None
    attachments_before: int = 0
    attachments_after: int = 0
    error_message: Optional[str] = None
    skipped_duplicate: bool = False


@dataclass
class BatchAttachResult:
    """Result of batch attachment operation."""

    total_processed: int
    attached: int
    skipped_no_match: int
    skipped_duplicate: int
    errors: List[str]


class ScreengrabAttacher:
    """
    Attaches screengrab images to Airtable SST records.

    This service has CONTROLLED WRITE ACCESS to the Screen Grab field only.
    All other Airtable operations remain read-only.
    """

    SST_TABLE_ID = "tblTKFOwTvK7xw1H5"
    SCREEN_GRAB_FIELD = "Screen Grab"
    SCREEN_GRAB_FIELD_ID = "fldCCWjcowpE2wJhc"
    API_BASE_URL = "https://api.airtable.com/v0"
    BASE_ID = "appZ2HGwhiifQToB6"

    def __init__(self, api_key: Optional[str] = None):
        """Initialize with Airtable API key."""
        self.api_key = api_key or get_secret("AIRTABLE_API_KEY")
        if not self.api_key:
            raise ValueError("Airtable API key required. Add to Keychain or set AIRTABLE_API_KEY env var.")

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Read-only client for lookups
        self._airtable_client = AirtableClient(api_key=self.api_key)

    async def attach_screengrab(
        self,
        media_id: str,
        filename: str,
        image_url: str,
        available_file_id: Optional[int] = None,
    ) -> AttachResult:
        """
        Attach a screengrab image to its matching SST record.

        SAFETY: This method APPENDS to existing attachments, never replaces them.

        Args:
            media_id: Media ID to match against SST record
            filename: Original filename of the image
            image_url: Public URL where the image can be downloaded
            available_file_id: Optional ID from available_files table for linking

        Returns:
            AttachResult with operation outcome
        """
        result = AttachResult(
            success=False,
            media_id=media_id,
            filename=filename,
        )

        try:
            # Step 1: Find SST record by Media ID
            sst_record = await self._airtable_client.search_sst_by_media_id(media_id)
            if not sst_record:
                result.error_message = f"No SST record found for Media ID: {media_id}"
                await self._log_attachment(result, available_file_id, image_url)
                return result

            record_id = sst_record["id"]
            result.sst_record_id = record_id

            # Step 2: Get existing attachments
            existing_attachments = sst_record.get("fields", {}).get(self.SCREEN_GRAB_FIELD, []) or []
            result.attachments_before = len(existing_attachments)

            # Step 3: Check for duplicate (same filename already attached)
            if self._is_duplicate(existing_attachments, filename):
                result.skipped_duplicate = True
                result.success = True
                result.attachments_after = result.attachments_before
                logger.info(f"Skipped duplicate screengrab: {filename} for {media_id}")
                await self._log_attachment(result, available_file_id, image_url)
                return result

            # Step 4: Build new attachment list (preserve existing + add new)
            # Existing attachments need their id field preserved
            # New attachment just needs url - Airtable will download and store it
            new_attachments = []
            for att in existing_attachments:
                # Preserve existing by ID
                new_attachments.append({"id": att["id"]})

            # Add new attachment by URL
            new_attachments.append(
                {
                    "url": image_url,
                    "filename": filename,
                }
            )

            # Step 5: Update SST record (CONTROLLED WRITE)
            await self._update_screengrab_field(record_id, new_attachments)

            result.success = True
            result.attachments_after = len(new_attachments)
            logger.info(
                f"Attached screengrab {filename} to {media_id} "
                f"(record {record_id}): {result.attachments_before} -> {result.attachments_after}"
            )

        except Exception as e:
            result.error_message = str(e)
            logger.error(f"Failed to attach screengrab {filename} for {media_id}: {e}")

        # Log to audit table
        await self._log_attachment(result, available_file_id, image_url)
        return result

    async def attach_from_available_file(self, file_id: int) -> AttachResult:
        """
        Attach a screengrab using data from the available_files table.

        Args:
            file_id: ID from available_files table

        Returns:
            AttachResult with operation outcome
        """
        from sqlalchemy import text

        async with get_session() as session:
            # Fetch file record
            query = text("""
                SELECT id, remote_url, filename, media_id, status
                FROM available_files
                WHERE id = :file_id AND file_type = 'screengrab'
            """)
            result = await session.execute(query, {"file_id": file_id})
            row = result.fetchone()

            if not row:
                return AttachResult(
                    success=False,
                    media_id="unknown",
                    filename="unknown",
                    error_message=f"Available file {file_id} not found or not a screengrab",
                )

            if not row.media_id:
                return AttachResult(
                    success=False,
                    media_id="unknown",
                    filename=row.filename,
                    error_message="No Media ID extracted from filename",
                )

        # Perform attachment
        attach_result = await self.attach_screengrab(
            media_id=row.media_id,
            filename=row.filename,
            image_url=row.remote_url,
            available_file_id=file_id,
        )

        # Update available_files status
        await self._update_available_file_status(file_id, attach_result)

        return attach_result

    async def attach_all_pending(self) -> BatchAttachResult:
        """
        Attach all pending screengrabs that have matching SST records.

        Returns:
            BatchAttachResult with summary of operations
        """
        from sqlalchemy import text

        batch_result = BatchAttachResult(
            total_processed=0,
            attached=0,
            skipped_no_match=0,
            skipped_duplicate=0,
            errors=[],
        )

        async with get_session() as session:
            # Fetch all 'new' screengrabs with media IDs
            query = text("""
                SELECT id, remote_url, filename, media_id
                FROM available_files
                WHERE file_type = 'screengrab'
                  AND status = 'new'
                  AND media_id IS NOT NULL
                ORDER BY first_seen_at ASC
            """)
            result = await session.execute(query)
            rows = result.fetchall()

        for row in rows:
            batch_result.total_processed += 1

            attach_result = await self.attach_screengrab(
                media_id=row.media_id,
                filename=row.filename,
                image_url=row.remote_url,
                available_file_id=row.id,
            )

            if attach_result.success:
                if attach_result.skipped_duplicate:
                    batch_result.skipped_duplicate += 1
                else:
                    batch_result.attached += 1
            elif "No SST record found" in (attach_result.error_message or ""):
                batch_result.skipped_no_match += 1
            else:
                batch_result.errors.append(f"{row.filename}: {attach_result.error_message}")

            # Update available_files status
            await self._update_available_file_status(row.id, attach_result)

        logger.info(
            f"Batch attach complete: {batch_result.attached} attached, "
            f"{batch_result.skipped_no_match} no match, "
            f"{batch_result.skipped_duplicate} duplicates, "
            f"{len(batch_result.errors)} errors"
        )

        return batch_result

    async def _update_screengrab_field(
        self,
        record_id: str,
        attachments: List[dict],
    ) -> None:
        """
        Update the Screen Grab field on an SST record.

        CONTROLLED WRITE: Only updates the Screen Grab field, nothing else.

        Args:
            record_id: Airtable record ID
            attachments: List of attachment objects (existing by id, new by url)
        """
        url = f"{self.API_BASE_URL}/{self.BASE_ID}/{self.SST_TABLE_ID}/{record_id}"

        payload = {"fields": {self.SCREEN_GRAB_FIELD: attachments}}

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.patch(url, headers=self.headers, json=payload)
            response.raise_for_status()

    def _is_duplicate(self, existing: List[dict], filename: str) -> bool:
        """Check if filename is already attached."""
        for att in existing:
            if att.get("filename") == filename:
                return True
        return False

    async def _log_attachment(
        self,
        result: AttachResult,
        available_file_id: Optional[int],
        remote_url: str,
    ) -> None:
        """Log attachment operation to audit table."""
        from sqlalchemy import text

        async with get_session() as session:
            query = text("""
                INSERT INTO screengrab_attachments
                (available_file_id, sst_record_id, media_id, filename, remote_url,
                 attached_at, attachments_before, attachments_after, success, error_message)
                VALUES
                (:available_file_id, :sst_record_id, :media_id, :filename, :remote_url,
                 :attached_at, :attachments_before, :attachments_after, :success, :error_message)
            """)

            await session.execute(
                query,
                {
                    "available_file_id": available_file_id,
                    "sst_record_id": result.sst_record_id or "",
                    "media_id": result.media_id,
                    "filename": result.filename,
                    "remote_url": remote_url,
                    "attached_at": datetime.now(timezone.utc).isoformat(),
                    "attachments_before": result.attachments_before,
                    "attachments_after": result.attachments_after,
                    "success": result.success,
                    "error_message": result.error_message,
                },
            )

    async def _update_available_file_status(
        self,
        file_id: int,
        result: AttachResult,
    ) -> None:
        """Update available_files status after attachment attempt."""
        from sqlalchemy import text

        # Determine new status
        if result.success:
            new_status = "attached"
        elif result.error_message and "No SST record found" in result.error_message:
            new_status = "no_match"
        else:
            new_status = "new"  # Keep as new for retry on transient errors

        async with get_session() as session:
            query = text("""
                UPDATE available_files
                SET status = :status,
                    status_changed_at = :changed_at,
                    airtable_record_id = :record_id,
                    attached_at = :attached_at
                WHERE id = :file_id
            """)

            await session.execute(
                query,
                {
                    "status": new_status,
                    "changed_at": datetime.now(timezone.utc).isoformat(),
                    "record_id": result.sst_record_id,
                    "attached_at": datetime.now(timezone.utc).isoformat() if result.success else None,
                    "file_id": file_id,
                },
            )


# Factory function
def get_screengrab_attacher() -> ScreengrabAttacher:
    """Create ScreengrabAttacher instance."""
    return ScreengrabAttacher()
