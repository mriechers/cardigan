"""Ingest models for Remote Ingest Watcher (Sprint 11.1).

These models support the scheduled scanning of the PBS Wisconsin
ingest server for transcript files and screengrabs matching QC-passed content.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class FileType(str, Enum):
    """Type of file discovered on ingest server."""

    transcript = "transcript"
    screengrab = "screengrab"


class FileStatus(str, Enum):
    """Status workflow for available files.

    - new: File discovered, ready for action
    - queued: Transcript queued for processing (job created)
    - attached: Screengrab attached to SST record
    - no_match: Screengrab Media ID not found in SST
    - ignored: User explicitly dismissed this file
    """

    new = "new"
    queued = "queued"
    attached = "attached"
    no_match = "no_match"
    ignored = "ignored"


# =============================================================================
# Database Record Models
# =============================================================================


class AvailableFileBase(BaseModel):
    """Base fields for an available file from ingest server."""

    remote_url: str = Field(..., description="Full URL to the file on ingest server")
    filename: str = Field(..., description="Just the filename (e.g., '2WLI1215HD.srt')")
    directory_path: Optional[str] = Field(None, description="Parent directory on server")
    file_type: FileType = Field(..., description="Type of file (transcript or screengrab)")
    media_id: Optional[str] = Field(None, description="Extracted Media ID from filename")
    file_size_bytes: Optional[int] = Field(None, description="File size if available")
    remote_modified_at: Optional[datetime] = Field(None, description="Last modified time on server")


class AvailableFileCreate(AvailableFileBase):
    """Schema for creating a new available file record."""

    pass


class AvailableFile(AvailableFileBase):
    """Complete available file record from database."""

    id: int
    first_seen_at: datetime
    last_seen_at: datetime
    status: FileStatus = FileStatus.new
    status_changed_at: Optional[datetime] = None
    job_id: Optional[int] = Field(None, description="Linked job ID if queued")
    airtable_record_id: Optional[str] = Field(None, description="SST record ID if attached")
    attached_at: Optional[datetime] = Field(None, description="When screengrab was attached")

    class Config:
        from_attributes = True


class ScreengrabAttachmentBase(BaseModel):
    """Base fields for screengrab attachment audit record."""

    sst_record_id: str = Field(..., description="Airtable SST record ID")
    media_id: str = Field(..., description="Media ID that was matched")
    filename: str = Field(..., description="Screengrab filename")
    remote_url: str = Field(..., description="URL where screengrab was downloaded from")


class ScreengrabAttachmentCreate(ScreengrabAttachmentBase):
    """Schema for creating screengrab attachment audit record."""

    available_file_id: Optional[int] = None
    attachments_before: Optional[int] = None
    attachments_after: Optional[int] = None
    success: bool = True
    error_message: Optional[str] = None


class ScreengrabAttachment(ScreengrabAttachmentBase):
    """Complete screengrab attachment audit record."""

    id: int
    available_file_id: Optional[int] = None
    attached_at: datetime
    attachments_before: Optional[int] = Field(None, description="Count of existing attachments before")
    attachments_after: Optional[int] = Field(None, description="Count after attachment")
    success: bool = True
    error_message: Optional[str] = None

    class Config:
        from_attributes = True


# =============================================================================
# API Request/Response Models
# =============================================================================


class SSTRecordInfo(BaseModel):
    """Minimal SST record info for display with available files."""

    id: str = Field(..., description="Airtable record ID")
    title: Optional[str] = Field(None, description="Episode/content title")
    project: Optional[str] = Field(None, description="Project name")


class AvailableFileWithSST(AvailableFile):
    """Available file with linked SST record info for API responses."""

    sst_record: Optional[SSTRecordInfo] = Field(None, description="Linked SST record info (if Media ID matched)")


class AvailableFilesResponse(BaseModel):
    """Response for GET /api/ingest/available."""

    files: List[AvailableFileWithSST]
    total_new: int = Field(..., description="Count of files with status='new'")
    total_transcripts: int = Field(0, description="Count of new transcripts")
    total_screengrabs: int = Field(0, description="Count of new screengrabs")
    last_scan_at: Optional[datetime] = Field(None, description="When last scan completed")


class QueueFileResponse(BaseModel):
    """Response for POST /api/ingest/queue/{file_id}."""

    success: bool
    job_id: Optional[int] = None
    message: str


class BulkQueueRequest(BaseModel):
    """Request for POST /api/ingest/queue/bulk."""

    file_ids: List[int] = Field(..., min_length=1, description="IDs of files to queue")


class BulkQueueResponse(BaseModel):
    """Response for POST /api/ingest/queue/bulk."""

    success: bool
    queued_count: int
    failed_count: int
    job_ids: List[int] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


# =============================================================================
# Scanner Models
# =============================================================================


class RemoteFile(BaseModel):
    """Parsed file info from directory listing."""

    filename: str
    url: str
    size_bytes: Optional[int] = None
    modified_at: Optional[datetime] = None
    directory: str


class ScanResult(BaseModel):
    """Result of an ingest scan operation."""

    success: bool
    qc_passed_checked: int = Field(..., description="Number of QC-passed Media IDs checked")
    new_transcripts_found: int = Field(0, description="New transcript files discovered")
    new_screengrabs_found: int = Field(0, description="New screengrab files discovered")
    errors: List[str] = Field(default_factory=list)
    scan_started_at: datetime
    scan_completed_at: datetime
    scan_duration_ms: int = Field(..., description="Scan duration in milliseconds")


# =============================================================================
# Configuration Models
# =============================================================================


class IngestConfig(BaseModel):
    """Ingest scanner configuration (stored in config table)."""

    enabled: bool = Field(True, description="Whether scheduled scanning is active")
    scan_interval_hours: int = Field(24, ge=1, le=168, description="Hours between scans")
    scan_time: str = Field("00:00", description="Time of day to run scan (HH:MM)")
    last_scan_at: Optional[datetime] = Field(None, description="When last scan completed")
    last_scan_success: Optional[bool] = Field(None, description="Whether last scan succeeded")
    server_url: str = Field("https://mmingest.pbswi.wisc.edu/", description="Base URL of ingest server")
    directories: List[str] = Field(
        default_factory=lambda: ["/"], description="Root directories to scan (recurses into subdirectories)"
    )
    ignore_directories: List[str] = Field(default_factory=lambda: ["/promos/"], description="Directories to ignore")


class IngestConfigUpdate(BaseModel):
    """Schema for updating ingest configuration (PUT /api/ingest/config)."""

    enabled: Optional[bool] = None
    scan_interval_hours: Optional[int] = Field(None, ge=1, le=168)
    scan_time: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")


class IngestConfigResponse(IngestConfig):
    """Response for GET /api/ingest/config."""

    next_scan_at: Optional[datetime] = Field(None, description="When next scan is scheduled")


# =============================================================================
# Screengrab-Specific Models
# =============================================================================


class AttachResult(BaseModel):
    """Result of attaching a single screengrab."""

    success: bool
    file_id: int
    media_id: str
    sst_record_id: Optional[str] = None
    attachments_before: Optional[int] = None
    attachments_after: Optional[int] = None
    error: Optional[str] = None


class BatchAttachResult(BaseModel):
    """Result of batch screengrab attachment."""

    success: bool
    attached_count: int
    failed_count: int
    no_match_count: int
    results: List[AttachResult]
