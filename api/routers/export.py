"""Export API endpoints for uploading reports to external services."""

import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.services.database import get_job
from api.services.google_drive import GoogleDriveService

router = APIRouter(prefix="/export", tags=["export"])

# Same allowlist as jobs.py — keep in sync
_ALLOWED_FILES = {
    "analyst_output.md",
    "formatter_output.md",
    "seo_output.md",
    "validator_output.md",
    "timestamp_output.md",
    "copy_editor_output.md",
    "recovery_analysis.md",
}


class ExportStatusResponse(BaseModel):
    google_drive: dict


class DriveUploadResponse(BaseModel):
    drive_url: str
    file_id: str


def _make_export_filename(project_name: str, filename: str) -> str:
    """Create an export filename prefixed with sanitized project name."""
    sanitized = re.sub(r"[^a-zA-Z0-9._-]+", "-", project_name)
    sanitized = re.sub(r"-{2,}", "-", sanitized).strip("-")
    return f"{sanitized}-{filename}"


@router.get("/status", response_model=ExportStatusResponse)
async def get_export_status():
    """Check which export services are configured and available."""
    drive = GoogleDriveService()
    return ExportStatusResponse(
        google_drive={
            "configured": drive.is_configured(),
        }
    )


@router.post("/google-drive/{job_id}/{filename}", response_model=DriveUploadResponse)
async def upload_to_google_drive(
    job_id: int,
    filename: str,
    folder_id: Optional[str] = Query(default=None, description="Google Drive folder ID"),
):
    """Upload a job output file to Google Drive."""
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    is_revision = bool(re.match(r"^copy_revision_v\d+\.md$", filename))
    if filename not in _ALLOWED_FILES and not is_revision:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {filename}")

    drive = GoogleDriveService()
    if not drive.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Google Drive export is not configured. Add GOOGLE_DRIVE_CREDENTIALS secret.",
        )

    if not job.project_path:
        raise HTTPException(status_code=404, detail="Job has no output directory")

    output_dir = Path(os.getenv("OUTPUT_DIR", "OUTPUT")).resolve()
    file_path = (Path(job.project_path) / filename).resolve()

    if not file_path.is_relative_to(output_dir):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Output file '{filename}' not found")

    drive_filename = _make_export_filename(job.project_name or f"job-{job_id}", filename)
    result = drive.upload_file(file_path, filename=drive_filename, folder_id=folder_id)

    if result is None:
        raise HTTPException(status_code=500, detail="Upload to Google Drive failed")

    return DriveUploadResponse(
        drive_url=result["webViewLink"],
        file_id=result["id"],
    )
