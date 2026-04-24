#!/usr/bin/env python3
"""
Cardigan — The Metadata Neighborhood's Friendly Editor

"Hello, neighbor. I'm so glad you're here."

Cardigan is the copy editor agent for PBS Wisconsin's editorial workflow.
Think of them as a warm, patient neighbor who's genuinely delighted to help
you polish your metadata and make your content shine.

The Metadata Neighborhood is the automated transcript processing pipeline
that prepares projects for Cardigan's gentle editorial touch.

Tools provided:
- Discover processed projects ready for editing
- Load project context (transcript, brainstorming, revisions, SST metadata)
- Save revisions and keyword reports with auto-versioning

Connects to the FastAPI backend on localhost:8100 for job metadata,
and reads/writes directly to the OUTPUT folder for content.

NOTE: Airtable writes are restricted to allowlisted fields via the
propose/review/commit workflow. See WRITABLE_FIELDS for the allowlist.
"""

import asyncio
import contextlib
import importlib.util
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Prompt, PromptArgument, PromptMessage, TextContent, Tool

# Load .env file FIRST — it contains the current, correct credentials.
# The MCP server runs as a separate process, so it needs its own load_dotenv().
load_dotenv(Path(__file__).parent.parent / ".env")

# Then backfill from Keychain for any keys still missing.
# keychain_secrets isn't on sys.path, so use spec_from_file_location.
_keychain_path = Path.home() / "Developer/the-lodge/scripts/keychain_secrets.py"
if _keychain_path.exists():
    try:
        spec = importlib.util.spec_from_file_location("keychain_secrets", _keychain_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _get_secret = getattr(mod, "get_secret", None)
            if _get_secret:
                for key in ["AIRTABLE_API_KEY"]:
                    if key not in os.environ:
                        value = _get_secret(key)
                        if value:
                            os.environ[key] = value
    except Exception:
        pass  # Keychain module not available (e.g., CI/Docker)

# Configuration
API_BASE_URL = os.getenv("EDITORIAL_API_URL", "http://localhost:8100")
OUTPUT_DIR = Path(os.getenv("EDITORIAL_OUTPUT_DIR", Path(__file__).parent.parent / "OUTPUT"))
TRANSCRIPTS_DIR = Path(os.getenv("EDITORIAL_TRANSCRIPTS_DIR", Path(__file__).parent.parent / "transcripts"))

# Artifact display labels - maps technical filenames to user-friendly names
ARTIFACT_LABELS = {
    "analyst_output.md": "Analysis",
    "formatter_output.md": "Formatted Transcript",
    "seo_output.md": "SEO Metadata",
    "manager_output.md": "QA Review",
    "timestamp_output.md": "Timestamps",
    "copy_editor_output.md": "Copy Edited",
    "recovery_analysis.md": "Recovery Analysis",
    "investigation_report.md": "Failure Investigation",
    "manifest.json": "Job Manifest",
}


def get_artifact_label(filename: str) -> str:
    """Get a friendly label for a filename, or return the filename if unknown."""
    return ARTIFACT_LABELS.get(filename, filename)


# Airtable configuration (writes restricted to WRITABLE_FIELDS via propose/review/commit)
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = "appZ2HGwhiifQToB6"
AIRTABLE_TABLE_ID = "tblTKFOwTvK7xw1H5"
AIRTABLE_API_BASE = "https://api.airtable.com/v0"

# Writable field allowlist — ONLY these fields can be written to Airtable.
# Everything else is read-only. Format: key -> (airtable_column, field_id, char_limit or None)
WRITABLE_FIELDS: dict[str, tuple[str, str, int | None]] = {
    "title": ("Release Title", "fldXqxjjxR4z5IJv6", 80),
    "short_description": ("Short Description", "fldDwTtKlOCdgKHpW", 90),
    "long_description": ("Long Description", "fld6HsWiKL77bFqo1", 350),
    "keywords": ("General Keywords/Tags", "fldjdPEXZyvx3rc6Y", None),
    "social_description": ("Social Media Description", "fldntHlzk6PfIT5k2", None),
    "social_tags": ("Social Media Tags", "fldcenwfu4nEWjPbt", None),
    "facebook_description": ("Facebook Description", "fldnprt2bJEsndv96", None),
    "hashtags": ("Hashtags", "fldYSGo5EBidQYL7W", None),
}

# Initialize MCP server
server = Server("cardigan")


# =============================================================================
# Helper Functions
# =============================================================================


def get_project_path(project_name: str) -> Path:
    """Get the OUTPUT path for a project."""
    candidate = (OUTPUT_DIR / project_name).resolve()
    if not str(candidate).startswith(str(OUTPUT_DIR.resolve())):
        raise ValueError(f"Invalid project name: {project_name!r}")
    return OUTPUT_DIR / project_name


def load_manifest(project_name: str) -> dict | None:
    """Load the manifest.json for a project."""
    manifest_path = get_project_path(project_name) / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            return json.load(f)
    return None


def save_manifest(project_name: str, manifest: dict) -> None:
    """Save the manifest.json for a project."""
    manifest_path = get_project_path(project_name) / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)


def ensure_project_folder(project_name: str) -> tuple[Path, bool]:
    """Ensure project folder exists, creating with minimal manifest if needed.

    When the editor works on a project that hasn't been through the transcript
    pipeline (e.g., editing from Airtable data only), the OUTPUT folder won't
    exist yet. This creates it with a provenance manifest so the pipeline can
    later add its outputs alongside the editor's work.

    Returns:
        Tuple of (project_path, was_created).
    """
    project_path = get_project_path(project_name)
    if project_path.exists():
        return project_path, False

    project_path.mkdir(parents=True, exist_ok=True)
    manifest = {
        "project_name": project_name,
        "origin": "editor",
        "created_at": datetime.now().isoformat(),
        "phases": [],
        "outputs": {},
    }
    save_manifest(project_name, manifest)
    return project_path, True


def get_next_version(project_path: Path, prefix: str) -> int:
    """Find the next version number for a file type."""
    pattern = re.compile(rf"{prefix}_v(\d+)\.md")
    max_version = 0
    for file in project_path.glob(f"{prefix}_v*.md"):
        match = pattern.match(file.name)
        if match:
            max_version = max(max_version, int(match.group(1)))
    return max_version + 1


def infer_content_type(manifest: dict, project_name: str) -> tuple[str, str]:
    """Infer the content type (segment vs digital_short) from available data.

    Returns:
        Tuple of (content_type, confidence) where confidence is 'explicit', 'inferred', or 'unknown'
    """
    # Check if explicitly set in manifest
    if manifest.get("content_type"):
        return manifest["content_type"], "explicit"

    # Check duration if available
    duration_minutes = manifest.get("duration_minutes")
    if duration_minutes is not None:
        # PBS Wisconsin convention: < 3 minutes = digital short, >= 3 minutes = segment
        if duration_minutes < 3.0:
            return "digital_short", "inferred"
        return "segment", "inferred"

    # Infer from project name patterns
    project_lower = project_name.lower()
    if "sm" in project_lower or "short" in project_lower or "ds" in project_lower:
        return "digital_short", "inferred"

    # Default to segment for Wisconsin Life (2WLI prefix) and other standard content
    if project_name.startswith("2WLI") or project_name.startswith("9UNP"):
        return "segment", "inferred"

    return "segment", "unknown"  # Default assumption


def determine_project_status(manifest: dict, project_path: Path) -> str:
    """Determine the editing status of a project."""
    phases = manifest.get("phases", [])

    # Check if still processing
    for phase in phases:
        if phase.get("status") == "in_progress":
            return "processing"

    # Check if any phases failed
    for phase in phases:
        if phase.get("status") == "failed":
            return "failed"

    # Check if we have revisions
    if list(project_path.glob("copy_revision_v*.md")):
        return "revision_in_progress"

    # Check if core phases are complete
    core_phases = ["analyst", "formatter", "seo"]
    completed = [p["name"] for p in phases if p.get("status") == "completed"]
    if all(p in completed for p in core_phases):
        return "ready_for_editing"

    return "incomplete"


def get_available_deliverables(project_path: Path, manifest: dict) -> list[str]:
    """List available deliverables for a project using friendly labels."""
    deliverables = []
    outputs = manifest.get("outputs", {})

    if (project_path / outputs.get("analysis", "")).exists():
        deliverables.append("Analysis")
    if (project_path / outputs.get("formatted_transcript", "")).exists():
        deliverables.append("Formatted Transcript")
    if (project_path / outputs.get("seo_metadata", "")).exists():
        deliverables.append("SEO Metadata")
    if (project_path / outputs.get("qa_review", "")).exists():
        deliverables.append("QA Review")

    # Check for revisions
    revisions = list(project_path.glob("copy_revision_v*.md"))
    if revisions:
        deliverables.append(f"Revisions ({len(revisions)})")

    # Check for keyword reports
    keyword_reports = list(project_path.glob("keyword_report_v*.md"))
    if keyword_reports:
        deliverables.append(f"Keyword Reports ({len(keyword_reports)})")

    return deliverables


async def fetch_job_from_api(project_name: str) -> dict | None:
    """Fetch job details from the FastAPI backend."""
    try:
        async with httpx.AsyncClient() as client:
            # Try to find job by project name
            response = await client.get(f"{API_BASE_URL}/api/queue", params={"limit": 100})
            if response.status_code == 200:
                data = response.json()
                jobs = data.get("jobs", [])
                for job in jobs:
                    if job.get("project_name") == project_name:
                        return job
    except Exception as e:
        logger.debug(f"Could not fetch job for {project_name}: {e}")
    return None


async def fetch_sst_context(airtable_record_id: str) -> Optional[dict]:
    """
    Fetch SST (Single Source of Truth) metadata from Airtable by record ID.

    This function is read-only. Writes go through patch_sst_record().

    Args:
        airtable_record_id: Airtable record ID (e.g., "recXXXXXXXXXXXXXX")

    Returns:
        Dict with SST fields if found and API key configured, None otherwise.
    """
    if not AIRTABLE_API_KEY:
        return None

    if not airtable_record_id:
        return None

    url = f"{AIRTABLE_API_BASE}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}/{airtable_record_id}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                record = response.json()
                return _extract_sst_fields(record)
            return None
    except Exception as e:
        logger.debug(f"Could not fetch SST context for {airtable_record_id}: {e}")
        return None


async def search_sst_by_media_id(media_id: str) -> Optional[dict]:
    """
    Search SST (Single Source of Truth) by Media ID.

    This function is read-only. Writes go through patch_sst_record().

    Args:
        media_id: The Media ID / project name (e.g., "2WLIEuchreWorldChampSM")

    Returns:
        Dict with SST fields if found, None otherwise.
    """
    if not AIRTABLE_API_KEY:
        return None

    if not media_id:
        return None

    # Use Airtable's filterByFormula to search by Media ID
    import urllib.parse

    # Escape single quotes to prevent formula injection
    safe_media_id = media_id.replace("'", "\\'")
    formula = f"{{Media ID}}='{safe_media_id}'"
    encoded_formula = urllib.parse.quote(formula)
    url = f"{AIRTABLE_API_BASE}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}?filterByFormula={encoded_formula}"

    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                records = data.get("records", [])
                if records:
                    return _extract_sst_fields(records[0])
            return None
    except Exception as e:
        logger.debug(f"Could not search SST for media ID {media_id}: {e}")
        return None


def _extract_sst_fields(record: dict) -> dict:
    """Extract relevant SST fields from an Airtable record."""
    fields = record.get("fields", {})
    record_id = record.get("id", "")

    # Map Airtable field names to our normalized names
    # Using actual Airtable field names from the SST table
    sst_context = {
        "record_id": record_id,
        "media_id": fields.get("Media ID"),
        "title": fields.get("Release Title"),
        "short_description": fields.get("Short Description"),
        "long_description": fields.get("Long Description"),
        "keywords": fields.get("General Keywords/Tags"),
        "program": fields.get("Batch-Episode"),  # This seems to be episode/segment name
        "host": fields.get("Host"),
        "presenter": fields.get("Presenter"),
        "sd_status": fields.get("SD Character Count"),
        "ld_status": fields.get("LD Character Count"),
        "special_thanks": fields.get("Episode Special Thanks"),
        "social_description": fields.get("Social Media Description"),
        "social_tags": fields.get("Social Media Tags"),
        "facebook_description": fields.get("Facebook Description"),
        "hashtags": fields.get("Hashtags"),
    }

    # Remove None values
    return {k: v for k, v in sst_context.items() if v is not None}


async def patch_sst_record(record_id: str, fields: dict[str, str]) -> tuple[bool, str]:
    """Write fields to an Airtable SST record via PATCH.

    Args:
        record_id: Airtable record ID (e.g., "recXXXXXXXX")
        fields: Dict of {Airtable column name: value} to write

    Returns:
        Tuple of (success: bool, message_or_error: str)
    """
    if not AIRTABLE_API_KEY:
        return False, "AIRTABLE_API_KEY not configured"

    url = f"{AIRTABLE_API_BASE}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}/{record_id}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"fields": fields}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(url, headers=headers, json=payload)
            if response.status_code == 200:
                return True, response.json()
            return False, f"Airtable returned {response.status_code}: {response.text}"
    except Exception as e:
        return False, f"Error writing to Airtable: {e}"


async def post_sst_comment(record_id: str, text: str) -> bool:
    """Post a comment on an Airtable SST record for audit trail.

    Args:
        record_id: Airtable record ID
        text: Comment text

    Returns:
        True if comment was posted successfully, False otherwise.
    """
    if not AIRTABLE_API_KEY:
        return False

    url = f"{AIRTABLE_API_BASE}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}/{record_id}/comments"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"text": text}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            return response.status_code == 200
    except Exception:
        return False


# =============================================================================
# MCP Tool Definitions
# =============================================================================


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools for the copy editor."""
    return [
        Tool(
            name="list_processed_projects",
            description="Discover what transcripts have been processed and are ready for editing. Returns project ID, status, and available deliverables.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status_filter": {
                        "type": "string",
                        "enum": ["all", "ready_for_editing", "revision_in_progress", "processing"],
                        "description": "Filter by project status. Default: all",
                    }
                },
            },
        ),
        Tool(
            name="load_project_for_editing",
            description="Load full context for an editing session: transcript, brainstorming (titles, descriptions, keywords), existing revisions, and metadata.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "The project ID (e.g., '2WLI1209HD')"}
                },
                "required": ["project_name"],
            },
        ),
        Tool(
            name="get_formatted_transcript",
            description="Load the AP Style formatted transcript for fact-checking. Use this to verify quotes, speaker names, and facts.",
            inputSchema={
                "type": "object",
                "properties": {"project_name": {"type": "string", "description": "The project ID"}},
                "required": ["project_name"],
            },
        ),
        Tool(
            name="save_revision",
            description="Save a copy revision document with auto-versioning. Returns the file path and version number.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "The project ID"},
                    "content": {"type": "string", "description": "The revision document content (Markdown)"},
                },
                "required": ["project_name", "content"],
            },
        ),
        Tool(
            name="save_keyword_report",
            description="Save a keyword/SEO analysis report with auto-versioning.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "The project ID"},
                    "content": {"type": "string", "description": "The keyword report content (Markdown)"},
                },
                "required": ["project_name", "content"],
            },
        ),
        Tool(
            name="get_project_summary",
            description="Quick status check for a specific project without loading full context.",
            inputSchema={
                "type": "object",
                "properties": {"project_name": {"type": "string", "description": "The project ID"}},
                "required": ["project_name"],
            },
        ),
        Tool(
            name="read_project_file",
            description="Read a specific file from a project folder.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "The project ID"},
                    "filename": {"type": "string", "description": "The filename to read (e.g., 'analyst_output.md')"},
                },
                "required": ["project_name", "filename"],
            },
        ),
        Tool(
            name="search_projects",
            description="Search projects by name, date range, or status. Supports text search and filtering.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for in project names (case-insensitive partial match)",
                    },
                    "status": {
                        "type": "string",
                        "enum": [
                            "all",
                            "ready_for_editing",
                            "revision_in_progress",
                            "processing",
                            "failed",
                            "incomplete",
                        ],
                        "description": "Filter by project status. Default: all",
                    },
                    "completed_after": {
                        "type": "string",
                        "description": "Filter projects completed after this date (YYYY-MM-DD format)",
                    },
                    "completed_before": {
                        "type": "string",
                        "description": "Filter projects completed before this date (YYYY-MM-DD format)",
                    },
                    "limit": {"type": "integer", "description": "Maximum number of results to return. Default: 20"},
                },
            },
        ),
        Tool(
            name="validate_copy",
            description="Validate metadata field lengths against PBS character limits. Returns counts and pass/fail for each field. Use before finalizing any revision.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Release title to validate (limit: 80 chars)"},
                    "short_description": {
                        "type": "string",
                        "description": "Short description to validate (limit: 90 chars)",
                    },
                    "long_description": {
                        "type": "string",
                        "description": "Long description to validate (limit: 350 chars)",
                    },
                    "keywords": {
                        "type": "string",
                        "description": "Keywords string to validate (optional, returns count)",
                    },
                },
            },
        ),
        Tool(
            name="get_sst_metadata",
            description="Fetch current metadata from Airtable SST (Single Source of Truth) by Media ID. Returns title, descriptions, keywords, and character count status. Use this to get the LIVE Airtable data for a project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "media_id": {
                        "type": "string",
                        "description": "The Media ID / project name (e.g., '2WLIEuchreWorldChampSM')",
                    }
                },
                "required": ["media_id"],
            },
        ),
        Tool(
            name="submit_processing_job",
            description=(
                "Queue a new video transcript for processing by Media ID. "
                "Looks for the transcript file locally or on the ingest server, "
                "validates the Media ID against the Airtable SST, and creates a processing job. "
                "Returns the job ID and queue position."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "media_id": {
                        "type": "string",
                        "description": "The SST Media ID (e.g., '2WLI1209HD', '2YAC2026Andre')",
                    },
                },
                "required": ["media_id"],
            },
        ),
        Tool(
            name="list_project_files",
            description="List all files in a project folder. Shows deliverables, revisions, keyword reports, and any uploaded files (e.g., SEMRush CSVs).",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "The project ID (e.g., '2WLI1209HD')"},
                },
                "required": ["project_name"],
            },
        ),
        Tool(
            name="list_revisions",
            description="Show the version history of copy revisions and keyword reports for a project. Includes version numbers, filenames, and timestamps.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "The project ID (e.g., '2WLI1209HD')"},
                },
                "required": ["project_name"],
            },
        ),
        Tool(
            name="propose_sst_edit",
            description="Stage a proposed edit to an Airtable SST field. Does NOT write to Airtable yet — just stages the change locally for review. Call review_proposed_edits to see all staged changes, then commit_sst_edits to write them.",
            inputSchema={
                "type": "object",
                "properties": {
                    "media_id": {"type": "string", "description": "The Media ID / project name"},
                    "field": {
                        "type": "string",
                        "enum": [
                            "title",
                            "short_description",
                            "long_description",
                            "keywords",
                            "social_description",
                            "social_tags",
                            "facebook_description",
                            "hashtags",
                        ],
                        "description": "Which metadata field to edit",
                    },
                    "proposed_value": {"type": "string", "description": "The new value for this field"},
                    "reason": {"type": "string", "description": "Why this change is being made (for audit trail)"},
                },
                "required": ["media_id", "field", "proposed_value", "reason"],
            },
        ),
        Tool(
            name="review_proposed_edits",
            description="Show all staged Airtable edits for a project in a diff format. Use this to preview changes before committing with commit_sst_edits.",
            inputSchema={
                "type": "object",
                "properties": {
                    "media_id": {"type": "string", "description": "The Media ID / project name"},
                },
                "required": ["media_id"],
            },
        ),
        Tool(
            name="commit_sst_edits",
            description="Write all staged edits to Airtable. Checks that no fields were changed since proposal (optimistic concurrency). Posts an audit comment on the record. ALWAYS show the user the output of review_proposed_edits before calling this.",
            inputSchema={
                "type": "object",
                "properties": {
                    "media_id": {"type": "string", "description": "The Media ID / project name"},
                },
                "required": ["media_id"],
            },
        ),
    ]


# =============================================================================
# MCP Prompt Definitions
# =============================================================================


@server.list_prompts()
async def list_prompts() -> list[Prompt]:
    """List available prompts for copy editing workflows."""
    return [
        Prompt(
            name="hello_neighbor",
            description="Meet Cardigan, your friendly editorial neighbor. A warm introduction to what's available.",
            arguments=[],
        ),
        Prompt(
            name="start_edit_session",
            description="Start an editing session for a project. Loads context and guides you through the copy editing workflow.",
            arguments=[
                PromptArgument(
                    name="project_name", description="The project ID to edit (e.g., '2WLI1209HD')", required=True
                )
            ],
        ),
        Prompt(
            name="review_brainstorming",
            description="Review the AI-generated brainstorming (titles, descriptions, keywords) for a project and refine the copy.",
            arguments=[PromptArgument(name="project_name", description="The project ID", required=True)],
        ),
        Prompt(
            name="analyze_seo",
            description="Analyze SEO metadata and suggest improvements for search visibility.",
            arguments=[PromptArgument(name="project_name", description="The project ID", required=True)],
        ),
        Prompt(
            name="fact_check",
            description="Verify facts, quotes, and speaker names against the formatted transcript.",
            arguments=[PromptArgument(name="project_name", description="The project ID", required=True)],
        ),
        Prompt(
            name="save_my_work",
            description="Save the current revision document to the project folder. Use this when you've finalized copy edits.",
            arguments=[PromptArgument(name="project_name", description="The project ID", required=True)],
        ),
    ]


@server.get_prompt()
async def get_prompt(name: str, arguments: dict[str, str] | None) -> list[PromptMessage]:
    """Get a prompt with arguments filled in."""
    args = arguments or {}
    project_name = args.get("project_name", "")

    if name == "hello_neighbor":
        return [
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text="""Hello, Cardigan! I'd like to do some editing today.

Please:
1. Introduce yourself warmly (you speak like Mister Rogers — gentle, patient, genuinely delighted to help)
2. Use `list_processed_projects("ready_for_editing")` to see what's available
3. Give me a friendly summary of what projects are ready for my attention
4. Ask which one I'd like to work on, or if I have something else in mind

Remember: You're Cardigan, the friendly editorial neighbor from The Metadata Neighborhood.
You help PBS Wisconsin polish their content with care and kindness.

IMPORTANT TOOL USAGE GUIDELINES:
- When you need to save a revision, you MUST actually call the `save_revision` tool. Do not just describe or announce saving.
- Always verify tool calls completed successfully by checking the response before telling the user it's done.
- If you want to show the user a document, present it directly in the chat - don't rely on external artifacts.
- Never claim to have saved, created, or modified a file unless the tool call returned a success confirmation.""",
                ),
            )
        ]

    elif name == "start_edit_session":
        return [
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"""I'd like to start an editing session for project **{project_name}**.

Please:
1. Load the project context using `load_project_for_editing("{project_name}")`
2. Review the SST metadata (if available) to understand the canonical title and descriptions
3. Summarize the AI-generated brainstorming (key themes, suggested titles, keywords)
4. Let me know what's available and ask what aspect I'd like to work on first:
   - Title refinement
   - Description editing
   - Keyword optimization
   - Full copy review""",
                ),
            )
        ]

    elif name == "review_brainstorming":
        return [
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"""I want to review the AI-generated brainstorming for project **{project_name}**.

Please:
1. Load the project using `load_project_for_editing("{project_name}")`
2. Present the brainstorming section with these for each suggested title/description:
   - The suggestion
   - Why it works (strengths)
   - What could be improved
3. Compare against the SST metadata (if available)
4. Recommend which suggestions to use, modify, or discard""",
                ),
            )
        ]

    elif name == "analyze_seo":
        return [
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"""I need an SEO analysis for project **{project_name}**.

Please:
1. Load the project using `load_project_for_editing("{project_name}")`
2. Read the SEO metadata file using `read_project_file("{project_name}", "seo_output.md")`
3. Evaluate the current metadata for:
   - Title effectiveness (length, keywords, engagement)
   - Description optimization (character limits, call-to-action)
   - Keyword coverage and density
   - Tag relevance
4. Suggest specific improvements with before/after examples
5. Save your analysis using `save_keyword_report` if needed""",
                ),
            )
        ]

    elif name == "fact_check":
        return [
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"""I need to fact-check content for project **{project_name}**.

Please:
1. Load the formatted transcript using `get_formatted_transcript("{project_name}")`
2. Load any existing revisions using `load_project_for_editing("{project_name}")`
3. Verify:
   - Speaker names are spelled correctly and consistently
   - Quoted text matches the transcript exactly
   - Proper nouns (organizations, places, titles) are accurate
   - Any facts or statistics mentioned
4. Flag any discrepancies or items that need verification""",
                ),
            )
        ]

    elif name == "save_my_work":
        return [
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"""Please save our work on project **{project_name}**.

CRITICAL: You must ACTUALLY call the save_revision tool. Do not just describe saving.

Steps:
1. Compile all finalized copy (titles, descriptions, keywords) into a single revision document
2. Format it as a clean markdown document with clear sections
3. Call `save_revision` with:
   - project_name: "{project_name}"
   - content: [the full markdown document]
4. Wait for the tool response confirming success
5. Only AFTER receiving "✅ Saved revision as copy_revision_vX.md" should you tell me it's saved
6. Show me the confirmation message from the tool

If the tool call fails or returns an error, tell me immediately - do not claim success.""",
                ),
            )
        ]

    else:
        return [PromptMessage(role="user", content=TextContent(type="text", text=f"Unknown prompt: {name}"))]


# =============================================================================
# MCP Tool Implementations
# =============================================================================


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls from the copy editor agent."""

    if name == "list_processed_projects":
        return await handle_list_processed_projects(arguments)
    elif name == "load_project_for_editing":
        return await handle_load_project_for_editing(arguments)
    elif name == "get_formatted_transcript":
        return await handle_get_formatted_transcript(arguments)
    elif name == "save_revision":
        return await handle_save_revision(arguments)
    elif name == "save_keyword_report":
        return await handle_save_keyword_report(arguments)
    elif name == "get_project_summary":
        return await handle_get_project_summary(arguments)
    elif name == "read_project_file":
        return await handle_read_project_file(arguments)
    elif name == "search_projects":
        return await handle_search_projects(arguments)
    elif name == "validate_copy":
        return await handle_validate_copy(arguments)
    elif name == "get_sst_metadata":
        return await handle_get_sst_metadata(arguments)
    elif name == "submit_processing_job":
        return await handle_submit_processing_job(arguments)
    elif name == "list_project_files":
        return await handle_list_project_files(arguments)
    elif name == "list_revisions":
        return await handle_list_revisions(arguments)
    elif name == "propose_sst_edit":
        return await handle_propose_sst_edit(arguments)
    elif name == "review_proposed_edits":
        return await handle_review_proposed_edits(arguments)
    elif name == "commit_sst_edits":
        return await handle_commit_sst_edits(arguments)
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def handle_list_processed_projects(arguments: dict) -> list[TextContent]:
    """List all processed projects with their status."""
    status_filter = arguments.get("status_filter", "all")

    projects = []

    if not OUTPUT_DIR.exists():
        return [TextContent(type="text", text="No OUTPUT directory found.")]

    for project_path in sorted(OUTPUT_DIR.iterdir()):
        if not project_path.is_dir():
            continue

        manifest = load_manifest(project_path.name)
        if not manifest:
            continue

        status = determine_project_status(manifest, project_path)

        # Apply filter
        if status_filter != "all" and status != status_filter:
            continue

        deliverables = get_available_deliverables(project_path, manifest)

        # Extract metadata from manifest
        completed_at = manifest.get("completed_at", "")
        if completed_at:
            try:
                dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
                completed_at = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass

        projects.append(
            {
                "project_name": project_path.name,
                "status": status,
                "completed_at": completed_at,
                "deliverables": deliverables,
                "job_id": manifest.get("job_id"),
            }
        )

    if not projects:
        return [TextContent(type="text", text="No processed projects found.")]

    # Format output
    lines = [f"Found {len(projects)} processed project(s):\n"]
    for p in projects:
        status_emoji = {
            "ready_for_editing": "✅",
            "revision_in_progress": "📝",
            "processing": "⏳",
            "failed": "❌",
            "incomplete": "⚠️",
        }.get(p["status"], "❓")

        lines.append(f"{status_emoji} **{p['project_name']}**")
        lines.append(f"   Status: {p['status']}")
        if p["completed_at"]:
            lines.append(f"   Completed: {p['completed_at']}")
        lines.append(f"   Has: {', '.join(p['deliverables'])}")
        lines.append("")

    return [TextContent(type="text", text="\n".join(lines))]


async def handle_load_project_for_editing(arguments: dict) -> list[TextContent]:
    """Load full project context for editing, including SST metadata if available."""
    project_name = arguments.get("project_name")
    if not project_name:
        return [TextContent(type="text", text="Error: project_name is required")]

    project_path = get_project_path(project_name)
    if not project_path.exists():
        return [
            TextContent(
                type="text",
                text="\n".join(
                    [
                        f"# Project: {project_name}",
                        "",
                        f"No OUTPUT folder exists for '{project_name}'. This project has not been processed through the transcript pipeline.",
                        "",
                        "## How to proceed",
                        "",
                        f"1. **Fetch Airtable context** — Use `get_sst_metadata` with media_id `{project_name}`",
                        "2. **Save your work** — `save_revision` and `save_keyword_report` will create the project folder automatically",
                        "3. **Or submit for processing** — Add a transcript to the ingest queue via the web dashboard",
                    ]
                ),
            )
        ]

    manifest = load_manifest(project_name)
    if not manifest:
        # Folder exists but no manifest — list what's there so the editor isn't stuck
        existing_files = sorted(f.name for f in project_path.iterdir() if f.is_file())
        file_list = [f"- {name}" for name in existing_files] if existing_files else ["- _(empty folder)_"]
        return [
            TextContent(
                type="text",
                text="\n".join(
                    [
                        f"# Project: {project_name}",
                        "",
                        "Project folder exists but has no manifest.json. This may be a partially created project.",
                        "",
                        "## Files found",
                        *file_list,
                        "",
                        "Use `save_revision` or `save_keyword_report` to create a manifest and save your work.",
                    ]
                ),
            )
        ]

    outputs = manifest.get("outputs", {})
    result_parts = []

    # Header
    result_parts.append(f"# Project: {project_name}\n")
    result_parts.append(f"**Status**: {determine_project_status(manifest, project_path)}")
    result_parts.append(f"**Job ID**: {manifest.get('job_id', 'N/A')}")
    if manifest.get("completed_at"):
        result_parts.append(f"**Completed**: {manifest['completed_at']}")

    # Content type (segment vs digital short) - critical for applying correct editorial standards
    content_type, confidence = infer_content_type(manifest, project_name)
    content_type_display = "Full Segment" if content_type == "segment" else "Digital Short"
    if confidence == "inferred":
        result_parts.append(f"**Content Type**: {content_type_display} _(inferred)_")
    elif confidence == "unknown":
        result_parts.append(f"**Content Type**: {content_type_display} _(assumed - please verify)_")
    else:
        result_parts.append(f"**Content Type**: {content_type_display}")

    # Duration if available
    if manifest.get("duration_minutes"):
        mins = manifest["duration_minutes"]
        result_parts.append(f"**Duration**: {int(mins)}:{int((mins % 1) * 60):02d}")

    # Include Airtable SST link if available
    airtable_url = manifest.get("airtable_url")
    if airtable_url:
        result_parts.append(f"**SST Record**: [{project_name}]({airtable_url})")
    result_parts.append("")

    # Fetch and include SST context if linked (Sprint 10.2.1)
    airtable_record_id = manifest.get("airtable_record_id")
    if airtable_record_id:
        sst_context = await fetch_sst_context(airtable_record_id)
        if sst_context:
            result_parts.append("---\n## Single Source of Truth (SST) Metadata\n")
            result_parts.append("*Canonical metadata from PBS Wisconsin Airtable. Use this for alignment.*\n")

            if sst_context.get("title"):
                result_parts.append(f"**Title:** {sst_context['title']}")
            if sst_context.get("program"):
                result_parts.append(f"**Program:** {sst_context['program']}")
            if sst_context.get("short_description"):
                result_parts.append(f"**Short Description:** {sst_context['short_description']}")
            if sst_context.get("long_description"):
                result_parts.append(f"\n**Long Description:**\n{sst_context['long_description']}")
            if sst_context.get("host"):
                result_parts.append(f"**Host:** {sst_context['host']}")
            if sst_context.get("presenter"):
                result_parts.append(f"**Presenter:** {sst_context['presenter']}")
            if sst_context.get("keywords"):
                result_parts.append(f"**Keywords:** {sst_context['keywords']}")
            if sst_context.get("tags"):
                result_parts.append(f"**Tags:** {sst_context['tags']}")
            result_parts.append("")
        elif not AIRTABLE_API_KEY:
            result_parts.append("---\n## SST Metadata\n")
            result_parts.append(
                "*SST linked but AIRTABLE_API_KEY not configured. Set env var to enable SST context.*\n"
            )
            result_parts.append("")
        else:
            result_parts.append("---\n## SST Metadata\n")
            result_parts.append(
                f"*⚠️ SST record linked ({airtable_record_id}) but fetch returned no data. Record may not exist or have no relevant fields.*\n"
            )
            result_parts.append("")
    else:
        # No Airtable link in manifest - prompt agent to search directly
        result_parts.append("---\n## SST Metadata\n")
        result_parts.append("*⚠️ NO AIRTABLE LINK IN MANIFEST — Try searching Airtable by Media ID:*\n")
        result_parts.append("```")
        result_parts.append("mcp__airtable__search_records(")
        result_parts.append('  baseId="appZ2HGwhiifQToB6",')
        result_parts.append('  tableId="tblTKFOwTvK7xw1H5",')
        result_parts.append(f'  searchTerm="{project_name}"')
        result_parts.append(")")
        result_parts.append("```")
        result_parts.append(
            "*If search finds a record, use that SST data. If no results, work from transcript only.*\n"
        )
        result_parts.append("")

    # Load brainstorming (analyst output)
    analyst_file = project_path / outputs.get("analysis", "analyst_output.md")
    if analyst_file.exists():
        result_parts.append("---\n## Brainstorming (AI-Generated)\n")
        result_parts.append(analyst_file.read_text())
        result_parts.append("")

    # Load latest revision if exists
    revisions = sorted(project_path.glob("copy_revision_v*.md"), reverse=True)
    if revisions:
        latest = revisions[0]
        version = re.search(r"v(\d+)", latest.name)
        version_num = version.group(1) if version else "?"
        result_parts.append(f"---\n## Latest Revision (v{version_num})\n")
        result_parts.append(latest.read_text())
        result_parts.append("")

    # Note about transcript
    result_parts.append("---\n## Transcript Access\n")
    result_parts.append("Use `get_formatted_transcript()` to load the AP Style formatted transcript for fact-checking.")

    return [TextContent(type="text", text="\n".join(result_parts))]


async def handle_get_formatted_transcript(arguments: dict) -> list[TextContent]:
    """Load the formatted transcript for fact-checking."""
    project_name = arguments.get("project_name")
    if not project_name:
        return [TextContent(type="text", text="Error: project_name is required")]

    project_path = get_project_path(project_name)
    manifest = load_manifest(project_name)

    if not manifest:
        return [TextContent(type="text", text=f"Error: Project '{project_name}' not found")]

    outputs = manifest.get("outputs", {})

    # Try formatted transcript first
    formatter_file = project_path / outputs.get("formatted_transcript", "formatter_output.md")
    if formatter_file.exists():
        content = formatter_file.read_text()
        return [TextContent(type="text", text=f"# Formatted Transcript: {project_name}\n\n{content}")]

    # Fall back to raw transcript
    transcript_name = manifest.get("transcript_file", "")
    if transcript_name:
        for search_dir in [TRANSCRIPTS_DIR, TRANSCRIPTS_DIR / "archive"]:
            transcript_path = search_dir / transcript_name
            if transcript_path.exists():
                content = transcript_path.read_text()
                return [
                    TextContent(
                        type="text",
                        text=f"# Raw Transcript: {project_name}\n\n**Note**: Formatted transcript not available. Using raw transcript.\n\n{content}",
                    )
                ]

    return [TextContent(type="text", text=f"Error: No transcript found for '{project_name}'")]


async def handle_save_revision(arguments: dict) -> list[TextContent]:
    """Save a copy revision with auto-versioning."""
    project_name = arguments.get("project_name")
    content = arguments.get("content")

    if not project_name or not content:
        return [TextContent(type="text", text="Error: project_name and content are required")]

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_save_revision_sync, project_name, content),
            timeout=15.0,
        )
        return [TextContent(type="text", text=result)]
    except asyncio.TimeoutError:
        return [
            TextContent(
                type="text",
                text=f"Error: Save timed out after 15 seconds for project '{project_name}'. "
                "The file system may be slow or unresponsive. Please retry.",
            )
        ]
    except Exception as e:
        return [TextContent(type="text", text=f"Error saving revision: {e}")]


def _save_revision_sync(project_name: str, content: str) -> str:
    """Synchronous file write for save_revision, run in a thread."""
    project_path, was_created = ensure_project_folder(project_name)

    version = get_next_version(project_path, "copy_revision")
    filename = f"copy_revision_v{version}.md"
    filepath = project_path / filename

    filepath.write_text(content)

    manifest = load_manifest(project_name)
    if manifest:
        if "revisions" not in manifest:
            manifest["revisions"] = []
        manifest["revisions"].append(
            {
                "version": version,
                "filename": filename,
                "saved_at": datetime.now().isoformat(),
            }
        )
        save_manifest(project_name, manifest)

    created_note = f"\n📁 New project folder created for '{project_name}'" if was_created else ""
    return f"✅ Saved revision as `{filename}` in OUTPUT/{project_name}/\n\nVersion: v{version}\nPath: {filepath}{created_note}"


async def handle_save_keyword_report(arguments: dict) -> list[TextContent]:
    """Save a keyword report with auto-versioning."""
    project_name = arguments.get("project_name")
    content = arguments.get("content")

    if not project_name or not content:
        return [TextContent(type="text", text="Error: project_name and content are required")]

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_save_keyword_report_sync, project_name, content),
            timeout=15.0,
        )
        return [TextContent(type="text", text=result)]
    except asyncio.TimeoutError:
        return [
            TextContent(
                type="text",
                text=f"Error: Save timed out after 15 seconds for project '{project_name}'. "
                "The file system may be slow or unresponsive. Please retry.",
            )
        ]
    except Exception as e:
        return [TextContent(type="text", text=f"Error saving keyword report: {e}")]


def _save_keyword_report_sync(project_name: str, content: str) -> str:
    """Synchronous file write for save_keyword_report, run in a thread."""
    project_path, was_created = ensure_project_folder(project_name)

    version = get_next_version(project_path, "keyword_report")
    filename = f"keyword_report_v{version}.md"
    filepath = project_path / filename

    filepath.write_text(content)

    manifest = load_manifest(project_name)
    if manifest:
        if "keyword_reports" not in manifest:
            manifest["keyword_reports"] = []
        manifest["keyword_reports"].append(
            {
                "version": version,
                "filename": filename,
                "saved_at": datetime.now().isoformat(),
            }
        )
        save_manifest(project_name, manifest)

    created_note = f"\n📁 New project folder created for '{project_name}'" if was_created else ""
    return f"✅ Saved keyword report as `{filename}` in OUTPUT/{project_name}/\n\nVersion: v{version}\nPath: {filepath}{created_note}"


async def handle_get_project_summary(arguments: dict) -> list[TextContent]:
    """Get a quick summary of a project's status."""
    project_name = arguments.get("project_name")
    if not project_name:
        return [TextContent(type="text", text="Error: project_name is required")]

    project_path = get_project_path(project_name)
    if not project_path.exists():
        return [TextContent(type="text", text=f"Error: Project '{project_name}' not found")]

    manifest = load_manifest(project_name)
    if not manifest:
        return [TextContent(type="text", text=f"Error: No manifest found for '{project_name}'")]

    status = determine_project_status(manifest, project_path)
    deliverables = get_available_deliverables(project_path, manifest)

    # Count revisions
    revisions = list(project_path.glob("copy_revision_v*.md"))
    keyword_reports = list(project_path.glob("keyword_report_v*.md"))

    lines = [
        f"# Project Summary: {project_name}\n",
        f"**Status**: {status}",
        f"**Job ID**: {manifest.get('job_id', 'N/A')}",
        f"**Completed**: {manifest.get('completed_at', 'N/A')}",
        "",
        "## Available Deliverables",
        *[f"- {d}" for d in deliverables],
        "",
        "## Revision History",
        f"- Copy Revisions: {len(revisions)}",
        f"- Keyword Reports: {len(keyword_reports)}",
    ]

    if revisions:
        latest = sorted(revisions, reverse=True)[0]
        lines.append(f"- Latest Revision: {latest.name}")

    return [TextContent(type="text", text="\n".join(lines))]


async def handle_read_project_file(arguments: dict) -> list[TextContent]:
    """Read a specific file from a project folder."""
    project_name = arguments.get("project_name")
    filename = arguments.get("filename")

    if not project_name or not filename:
        return [TextContent(type="text", text="Error: project_name and filename are required")]

    project_path = get_project_path(project_name)
    filepath = project_path / filename

    # Security check: ensure file is within project folder
    try:
        filepath.resolve().relative_to(project_path.resolve())
    except ValueError:
        return [TextContent(type="text", text="Error: Invalid file path")]

    if not filepath.exists():
        return [TextContent(type="text", text=f"Error: File '{filename}' not found in {project_name}")]

    content = filepath.read_text()
    label = get_artifact_label(filename)
    # Show friendly label with filename in parentheses for context
    header = f"# {label}" if label == filename else f"# {label} ({filename})"
    return [TextContent(type="text", text=f"{header}\n\n{content}")]


async def handle_search_projects(arguments: dict) -> list[TextContent]:
    """Search projects by name, date, and status."""
    query = arguments.get("query", "").lower()
    status_filter = arguments.get("status", "all")
    completed_after = arguments.get("completed_after")
    completed_before = arguments.get("completed_before")
    limit = arguments.get("limit", 20)

    # Parse date filters
    after_date = None
    before_date = None
    if completed_after:
        try:
            after_date = datetime.fromisoformat(completed_after)
        except ValueError:
            return [
                TextContent(
                    type="text",
                    text=f"Error: Invalid date format for completed_after: {completed_after}. Use YYYY-MM-DD.",
                )
            ]
    if completed_before:
        try:
            before_date = datetime.fromisoformat(completed_before)
        except ValueError:
            return [
                TextContent(
                    type="text",
                    text=f"Error: Invalid date format for completed_before: {completed_before}. Use YYYY-MM-DD.",
                )
            ]

    results = []

    if not OUTPUT_DIR.exists():
        return [TextContent(type="text", text="No OUTPUT directory found.")]

    for project_path in sorted(OUTPUT_DIR.iterdir(), reverse=True):  # Most recent first
        if not project_path.is_dir():
            continue

        project_name = project_path.name

        # Text search filter
        if query and query not in project_name.lower():
            continue

        manifest = load_manifest(project_name)
        if not manifest:
            continue

        # Status filter
        status = determine_project_status(manifest, project_path)
        if status_filter != "all" and status != status_filter:
            continue

        # Date range filter
        completed_at_str = manifest.get("completed_at", "")
        completed_at = None
        if completed_at_str:
            try:
                completed_at = datetime.fromisoformat(completed_at_str.replace("Z", "+00:00"))
            except Exception:
                pass

        if after_date and (not completed_at or completed_at.date() < after_date.date()):
            continue
        if before_date and (not completed_at or completed_at.date() > before_date.date()):
            continue

        # Collect result
        deliverables = get_available_deliverables(project_path, manifest)
        results.append(
            {
                "project_name": project_name,
                "status": status,
                "completed_at": completed_at.strftime("%Y-%m-%d %H:%M") if completed_at else "N/A",
                "deliverables": deliverables,
                "job_id": manifest.get("job_id"),
            }
        )

        if len(results) >= limit:
            break

    if not results:
        filters_desc = []
        if query:
            filters_desc.append(f"query='{query}'")
        if status_filter != "all":
            filters_desc.append(f"status='{status_filter}'")
        if completed_after:
            filters_desc.append(f"after={completed_after}")
        if completed_before:
            filters_desc.append(f"before={completed_before}")
        filters_str = ", ".join(filters_desc) if filters_desc else "none"
        return [TextContent(type="text", text=f"No projects found matching filters: {filters_str}")]

    # Format output
    lines = [f"Found {len(results)} project(s):\n"]
    for p in results:
        status_emoji = {
            "ready_for_editing": "✅",
            "revision_in_progress": "📝",
            "processing": "⏳",
            "failed": "❌",
            "incomplete": "⚠️",
        }.get(p["status"], "❓")

        lines.append(f"{status_emoji} **{p['project_name']}**")
        lines.append(f"   Status: {p['status']}")
        lines.append(f"   Completed: {p['completed_at']}")
        lines.append(f"   Has: {', '.join(p['deliverables'])}")
        lines.append("")

    return [TextContent(type="text", text="\n".join(lines))]


async def handle_get_sst_metadata(arguments: dict) -> list[TextContent]:
    """Fetch current SST metadata from Airtable by Media ID."""
    media_id = arguments.get("media_id")
    if not media_id:
        return [TextContent(type="text", text="Error: media_id is required")]

    if not AIRTABLE_API_KEY:
        return [TextContent(type="text", text="Error: AIRTABLE_API_KEY not configured. Cannot fetch SST metadata.")]

    sst_data = await search_sst_by_media_id(media_id)

    if not sst_data:
        return [
            TextContent(
                type="text",
                text=f"No SST record found for Media ID: {media_id}\n\nThis project may not have an Airtable entry yet.",
            )
        ]

    # Format the response
    lines = [f"# SST Metadata for {media_id}\n"]
    lines.append(f"**Airtable Record ID:** {sst_data.get('record_id', 'N/A')}")
    lines.append("")

    if sst_data.get("title"):
        title = sst_data["title"]
        title_len = len(title)
        title_status = "✅" if title_len <= 80 else "❌ OVER LIMIT"
        lines.append(f"## Title ({title_len} chars) {title_status}")
        lines.append(f"```\n{title}\n```")
        lines.append("")

    if sst_data.get("short_description"):
        sd = sst_data["short_description"]
        sd_len = len(sd)
        sd_status = sst_data.get("sd_status", "✅" if sd_len <= 100 else "❌ OVER LIMIT")
        lines.append(f"## Short Description ({sd_len} chars) {sd_status}")
        lines.append(f"```\n{sd}\n```")
        lines.append("")

    if sst_data.get("long_description"):
        ld = sst_data["long_description"]
        ld_len = len(ld)
        ld_status = sst_data.get("ld_status", "✅" if ld_len <= 350 else "❌ OVER LIMIT")
        lines.append(f"## Long Description ({ld_len} chars) {ld_status}")
        lines.append(f"```\n{ld}\n```")
        lines.append("")

    if sst_data.get("keywords"):
        lines.append("## Keywords/Tags")
        # Just show the first part if it's very long (often has analysis notes)
        keywords = sst_data["keywords"]
        if len(keywords) > 500:
            # Find the first double newline which often separates keywords from analysis
            split_point = keywords.find("\n\n")
            if split_point > 0:
                keywords = keywords[:split_point] + "\n\n[Additional analysis truncated]"
        lines.append(f"```\n{keywords}\n```")
        lines.append("")

    if sst_data.get("special_thanks"):
        lines.append("## Special Thanks")
        lines.append(sst_data["special_thanks"])
        lines.append("")

    if sst_data.get("program"):
        lines.append(f"**Episode/Segment:** {sst_data['program']}")

    return [TextContent(type="text", text="\n".join(lines))]


async def handle_validate_copy(arguments: dict) -> list[TextContent]:
    """Validate metadata field lengths against PBS character limits."""
    LIMITS = {
        "title": 80,
        "short_description": 100,
        "long_description": 350,
    }

    title = arguments.get("title")
    short_description = arguments.get("short_description")
    long_description = arguments.get("long_description")
    keywords = arguments.get("keywords")

    if not any([title, short_description, long_description, keywords]):
        return [
            TextContent(
                type="text",
                text="Error: At least one field (title, short_description, long_description, or keywords) is required.",
            )
        ]

    lines = ["# Copy Validation Results\n"]
    all_valid = True

    for field_name, limit in LIMITS.items():
        value = arguments.get(field_name)
        if value is not None:
            length = len(value)
            valid = length <= limit
            status = "✅" if valid else f"❌ OVER LIMIT by {length - limit}"
            if not valid:
                all_valid = False
            lines.append(f"**{field_name.replace('_', ' ').title()}:** {length}/{limit} chars {status}")
        else:
            lines.append(f"**{field_name.replace('_', ' ').title()}:** (not provided)")

    if keywords is not None:
        keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
        lines.append(f"\n**Keywords:** {len(keyword_list)} keywords provided")

    lines.append(f"\n**All valid:** {'✅ Yes' if all_valid else '❌ No — fix fields marked OVER LIMIT'}")

    return [TextContent(type="text", text="\n".join(lines))]


async def handle_submit_processing_job(arguments: dict) -> list[TextContent]:
    """Queue a new processing job by Media ID.

    Searches for the transcript file locally or on the ingest server,
    validates the Media ID, and creates a job.
    """
    media_id = arguments.get("media_id", "").strip()
    if not media_id:
        return [TextContent(type="text", text="Error: media_id is required")]

    # Step 1: Check if this media ID already has a job
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{API_BASE_URL}/api/queue/",
                params={"search": media_id, "page_size": 5},
            )
            if resp.status_code == 200:
                data = resp.json()
                existing = [j for j in data.get("jobs", []) if j.get("media_id") == media_id]
                if existing:
                    job = existing[0]
                    return [
                        TextContent(
                            type="text",
                            text=(
                                f"A job already exists for **{media_id}**:\n\n"
                                f"- **Job #{job['id']}** — Status: {job['status']}\n"
                                f"- Project: {job.get('project_name', 'N/A')}\n\n"
                                f"No new job was created."
                            ),
                        )
                    ]
    except Exception as e:
        logger.warning(f"Could not check for existing jobs for {media_id}: {e}")

    # Step 2: Look for transcript file locally
    transcript_file = None
    if TRANSCRIPTS_DIR.exists():
        for f in TRANSCRIPTS_DIR.iterdir():
            if f.is_file() and media_id.upper() in f.name.upper():
                transcript_file = f.name
                break

    # Step 3: If not local, check ingest server available_files
    ingest_file_id = None
    if not transcript_file:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{API_BASE_URL}/api/ingest/available",
                    params={"search": media_id, "file_type": "transcript", "status": "new"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    files = data.get("files", [])
                    if files:
                        ingest_file_id = files[0]["id"]
                        transcript_file = files[0]["filename"]
        except Exception as e:
            logger.warning(f"Could not search ingest server for {media_id}: {e}")

    if not transcript_file:
        return [
            TextContent(
                type="text",
                text=(
                    f"No transcript file found for **{media_id}**.\n\n"
                    f"Checked:\n"
                    f"- Local `transcripts/` directory\n"
                    f"- Ingest server available files\n\n"
                    f"Upload or scan for the transcript first, then try again."
                ),
            )
        ]

    # Step 4: Queue the job
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if ingest_file_id:
                # Queue via ingest endpoint (downloads from server)
                resp = await client.post(
                    f"{API_BASE_URL}/api/ingest/transcripts/{ingest_file_id}/queue",
                )
            else:
                # Queue via standard endpoint (local file)
                resp = await client.post(
                    f"{API_BASE_URL}/api/queue/",
                    json={
                        "project_name": media_id,
                        "transcript_file": transcript_file,
                    },
                )

            if resp.status_code in (200, 201):
                data = resp.json()
                job_id = data.get("job_id") or data.get("id")
                return [
                    TextContent(
                        type="text",
                        text=(
                            f"Job submitted for **{media_id}**!\n\n"
                            f"- **Job #{job_id}**\n"
                            f"- Transcript: `{transcript_file}`\n"
                            f"- Source: {'ingest server' if ingest_file_id else 'local file'}\n\n"
                            f"The pipeline will process this automatically."
                        ),
                    )
                ]
            else:
                error = resp.text
                try:
                    error = resp.json().get("detail", error)
                except Exception:
                    pass
                return [
                    TextContent(
                        type="text",
                        text=f"Failed to queue job for **{media_id}**: {error}",
                    )
                ]

    except Exception as e:
        return [TextContent(type="text", text=f"Error submitting job: {e}")]


async def handle_list_project_files(arguments: dict) -> list[TextContent]:
    """List all files in a project folder with friendly grouping."""
    project_name = arguments.get("project_name")
    if not project_name:
        return [TextContent(type="text", text="Error: project_name is required")]

    project_path = get_project_path(project_name)
    if not project_path.exists():
        return [TextContent(type="text", text=f"Error: Project '{project_name}' not found in OUTPUT/")]

    # Collect all files recursively
    all_files = []
    for file in sorted(project_path.rglob("*")):
        if file.is_file():
            rel_path = file.relative_to(project_path)
            size = file.stat().st_size
            all_files.append((str(rel_path), size))

    if not all_files:
        return [TextContent(type="text", text=f"# Files in {project_name}\n\n(empty folder)")]

    # Group files
    deliverables = []
    revisions = []
    keyword_reports = []
    other = []

    for rel_path, size in all_files:
        filename = Path(rel_path).name
        size_str = f"{size:,} bytes" if size < 1024 else f"{size / 1024:.1f} KB"
        entry = f"- `{rel_path}` ({size_str})"
        label = get_artifact_label(filename)
        if label != filename:
            entry = f"- `{rel_path}` — {label} ({size_str})"

        if "copy_revision_v" in filename:
            revisions.append(entry)
        elif "keyword_report_v" in filename:
            keyword_reports.append(entry)
        elif filename in ARTIFACT_LABELS:
            deliverables.append(entry)
        else:
            other.append(entry)

    lines = [f"# Files in {project_name}\n"]

    if deliverables:
        lines.append("## Pipeline Deliverables")
        lines.extend(deliverables)
        lines.append("")

    if revisions:
        lines.append(f"## Copy Revisions ({len(revisions)})")
        lines.extend(revisions)
        lines.append("")

    if keyword_reports:
        lines.append(f"## Keyword Reports ({len(keyword_reports)})")
        lines.extend(keyword_reports)
        lines.append("")

    if other:
        lines.append("## Other Files")
        lines.extend(other)
        lines.append("")

    lines.append(f"**Total:** {len(all_files)} files")

    return [TextContent(type="text", text="\n".join(lines))]


async def handle_list_revisions(arguments: dict) -> list[TextContent]:
    """Show version history for a project's revisions and keyword reports."""
    project_name = arguments.get("project_name")
    if not project_name:
        return [TextContent(type="text", text="Error: project_name is required")]

    project_path = get_project_path(project_name)
    if not project_path.exists():
        return [TextContent(type="text", text=f"Error: Project '{project_name}' not found in OUTPUT/")]

    manifest = load_manifest(project_name)
    revisions = manifest.get("revisions", []) if manifest else []
    keyword_reports = manifest.get("keyword_reports", []) if manifest else []

    if not revisions and not keyword_reports:
        return [
            TextContent(
                type="text", text=f"# Revision History for {project_name}\n\nNo revisions or keyword reports saved yet."
            )
        ]

    lines = [f"# Revision History for {project_name}\n"]

    if revisions:
        lines.append(f"## Copy Revisions ({len(revisions)})")
        for rev in reversed(revisions):  # Most recent first
            version = rev.get("version", "?")
            filename = rev.get("filename", "unknown")
            saved_at = rev.get("saved_at", "unknown")
            filepath = project_path / filename
            size_note = ""
            if filepath.exists():
                size = filepath.stat().st_size
                size_note = f" — {size / 1024:.1f} KB" if size >= 1024 else f" — {size:,} bytes"
            else:
                size_note = " — file missing"
            lines.append(f"- **v{version}**: `{filename}` (saved {saved_at}){size_note}")
        lines.append("")

    if keyword_reports:
        lines.append(f"## Keyword Reports ({len(keyword_reports)})")
        for report in reversed(keyword_reports):
            version = report.get("version", "?")
            filename = report.get("filename", "unknown")
            saved_at = report.get("saved_at", "unknown")
            filepath = project_path / filename
            size_note = ""
            if filepath.exists():
                size = filepath.stat().st_size
                size_note = f" — {size / 1024:.1f} KB" if size >= 1024 else f" — {size:,} bytes"
            else:
                size_note = " — file missing"
            lines.append(f"- **v{version}**: `{filename}` (saved {saved_at}){size_note}")
        lines.append("")

    lines.append("Use `read_project_file(project_name, filename)` to read any revision.")

    return [TextContent(type="text", text="\n".join(lines))]


async def handle_propose_sst_edit(arguments: dict) -> list[TextContent]:
    """Stage a proposed edit to an Airtable SST field."""
    media_id = arguments.get("media_id")
    field = arguments.get("field")
    proposed_value = arguments.get("proposed_value")
    reason = arguments.get("reason")

    if not all([media_id, field, proposed_value, reason]):
        return [TextContent(type="text", text="Error: media_id, field, proposed_value, and reason are all required.")]

    # Enforce allowlist
    if field not in WRITABLE_FIELDS:
        allowed = ", ".join(sorted(WRITABLE_FIELDS.keys()))
        return [TextContent(type="text", text=f"Error: Field '{field}' is not writable. Allowed fields: {allowed}")]

    airtable_column, field_id, char_limit = WRITABLE_FIELDS[field]

    # Fetch current value from Airtable
    sst_data = await search_sst_by_media_id(media_id)
    if not sst_data:
        return [TextContent(type="text", text=f"Error: No SST record found for Media ID '{media_id}'")]

    record_id = sst_data.get("record_id")
    current_value = sst_data.get(field, "")

    # Validate character limit
    length = len(proposed_value)
    limit_status = ""
    if char_limit:
        if length <= char_limit:
            limit_status = f" ({length}/{char_limit} chars ✅)"
        else:
            limit_status = f" ({length}/{char_limit} chars ❌ OVER LIMIT by {length - char_limit})"
    else:
        limit_status = f" ({length} chars)"

    # Stage in manifest
    project_path, was_created = ensure_project_folder(media_id)
    manifest = load_manifest(media_id) or {
        "project_name": media_id,
        "origin": "editor",
        "created_at": datetime.now().isoformat(),
        "phases": [],
        "outputs": {},
    }

    if "proposed_edits" not in manifest:
        manifest["proposed_edits"] = {}

    manifest["proposed_edits"][field] = {
        "airtable_column": airtable_column,
        "current_value": current_value or "",
        "proposed_value": proposed_value,
        "reason": reason,
        "record_id": record_id,
        "staged_at": datetime.now().isoformat(),
    }
    save_manifest(media_id, manifest)

    # Build response
    current_display = current_value or "(empty)"
    staged_count = len(manifest["proposed_edits"])

    lines = [
        f"# Proposed Edit: {airtable_column}\n",
        f"**Current:** {current_display}",
        f"**Proposed:** {proposed_value}{limit_status}",
        f"**Reason:** {reason}",
        f"\n✅ Staged in manifest ({staged_count} edit{'s' if staged_count != 1 else ''} pending)",
        f'\nUse `review_proposed_edits("{media_id}")` to see all pending changes.',
        f'Use `commit_sst_edits("{media_id}")` when ready to write to Airtable.',
    ]

    return [TextContent(type="text", text="\n".join(lines))]


async def handle_review_proposed_edits(arguments: dict) -> list[TextContent]:
    """Show all staged Airtable edits for a project."""
    media_id = arguments.get("media_id")
    if not media_id:
        return [TextContent(type="text", text="Error: media_id is required")]

    manifest = load_manifest(media_id)
    proposed = manifest.get("proposed_edits", {}) if manifest else {}

    if not proposed:
        return [
            TextContent(
                type="text",
                text=f"# Proposed Edits for {media_id}\n\nNo pending edits. Use `propose_sst_edit` to stage changes.",
            )
        ]

    lines = [f"# Proposed Edits for {media_id}\n"]
    lines.append(f"**{len(proposed)} edit{'s' if len(proposed) != 1 else ''} staged**\n")

    for field_key, edit in proposed.items():
        airtable_column = edit.get("airtable_column", field_key)
        current = edit.get("current_value", "(empty)") or "(empty)"
        proposed_val = edit.get("proposed_value", "")
        reason = edit.get("reason", "")

        limit_info = ""
        if field_key in WRITABLE_FIELDS:
            _, _, char_limit = WRITABLE_FIELDS[field_key]
            if char_limit:
                length = len(proposed_val)
                status = "✅" if length <= char_limit else f"❌ OVER by {length - char_limit}"
                limit_info = f" ({length}/{char_limit} {status})"

        lines.append(f"## {airtable_column}")
        lines.append(f"**Current:** {current}")
        lines.append(f"**Proposed:** {proposed_val}{limit_info}")
        lines.append(f"**Reason:** {reason}")
        lines.append("")

    lines.append("---")
    lines.append(f'Use `commit_sst_edits("{media_id}")` to write these changes to Airtable.')
    lines.append("Use `propose_sst_edit` to modify or add more changes.")

    return [TextContent(type="text", text="\n".join(lines))]


async def handle_commit_sst_edits(arguments: dict) -> list[TextContent]:
    """Write all staged edits to Airtable with concurrency checking and audit."""
    media_id = arguments.get("media_id")
    if not media_id:
        return [TextContent(type="text", text="Error: media_id is required")]

    manifest = load_manifest(media_id)
    proposed = manifest.get("proposed_edits", {}) if manifest else {}

    if not proposed:
        return [
            TextContent(
                type="text", text=f"No pending edits for {media_id}. Use `propose_sst_edit` to stage changes first."
            )
        ]

    # All proposals should reference the same record
    record_id = None
    for edit in proposed.values():
        record_id = edit.get("record_id")
        if record_id:
            break

    if not record_id:
        return [TextContent(type="text", text="Error: No Airtable record ID found in staged edits.")]

    # Optimistic concurrency check: re-fetch current values
    current_data = await fetch_sst_context(record_id)
    if not current_data:
        return [
            TextContent(
                type="text",
                text="Error: Could not re-fetch Airtable record for concurrency check. Record may have been deleted.",
            )
        ]

    # Check for conflicts
    conflicts = []
    for field_key, edit in proposed.items():
        expected_current = edit.get("current_value", "")
        actual_current = current_data.get(field_key, "") or ""
        if expected_current != actual_current:
            airtable_column = edit.get("airtable_column", field_key)
            conflicts.append(
                f"**{airtable_column}:**\n"
                f"  Expected: {expected_current or '(empty)'}\n"
                f"  Actual now: {actual_current or '(empty)'}"
            )

    if conflicts:
        lines = [
            f"# ⚠️ Concurrency Conflict for {media_id}\n",
            "The following fields were changed in Airtable since you staged your edits:\n",
            *conflicts,
            "\n**Your staged edits were NOT applied.**",
            "Please review the current values, then use `propose_sst_edit` to re-stage with updated context.",
        ]
        return [TextContent(type="text", text="\n".join(lines))]

    # Enforce character limits before writing
    over_limit = []
    for field_key, edit in proposed.items():
        if field_key in WRITABLE_FIELDS:
            _, _, char_limit = WRITABLE_FIELDS[field_key]
            value = edit.get("proposed_value", "")
            if char_limit and isinstance(value, str) and len(value) > char_limit:
                col_name = edit.get("airtable_column", field_key)
                over_limit.append(f"  {col_name}: {len(value)}/{char_limit} chars")
    if over_limit:
        return [
            TextContent(
                type="text",
                text="**Commit blocked** — fields exceed character limits:\n"
                + "\n".join(over_limit)
                + "\n\nFix them with `propose_sst_edit` before committing.",
            )
        ]

    # Build the PATCH payload using Airtable column names
    patch_fields = {}
    for field_key, edit in proposed.items():
        airtable_column = edit.get("airtable_column")
        if airtable_column:
            patch_fields[airtable_column] = edit["proposed_value"]

    # Write to Airtable
    success, result = await patch_sst_record(record_id, patch_fields)
    if not success:
        return [
            TextContent(
                type="text",
                text=f'Error writing to Airtable: {result}\n\nYour staged edits are still saved. You can retry with `commit_sst_edits("{media_id}")`.',
            )
        ]

    # Post audit comment
    comment_lines = ["📝 Agent edit (Cardigan copy editor)\n\nFields updated:"]
    for field_key, edit in proposed.items():
        airtable_column = edit.get("airtable_column", field_key)
        old = edit.get("current_value", "(empty)") or "(empty)"
        new = edit.get("proposed_value", "")
        reason = edit.get("reason", "")
        comment_lines.append(f'- {airtable_column}: "{old}" → "{new}" ({reason})')
    comment_lines.append(f"\nSession: {datetime.now().isoformat()}")

    comment_ok = await post_sst_comment(record_id, "\n".join(comment_lines))

    # Clear proposed edits from manifest
    manifest["proposed_edits"] = {}
    save_manifest(media_id, manifest)

    # Build confirmation response
    lines = [f"# ✅ Airtable Updated for {media_id}\n"]
    for field_key, edit in proposed.items():
        airtable_column = edit.get("airtable_column", field_key)
        old = edit.get("current_value", "(empty)") or "(empty)"
        new = edit.get("proposed_value", "")
        lines.append(f"**{airtable_column}:** {old} → {new}")

    if comment_ok:
        lines.append(f"\n📝 Audit comment posted on record `{record_id}`")
    else:
        lines.append(f"\n⚠️ Fields updated but audit comment failed to post on `{record_id}`. Check Airtable manually.")
        logger.warning(f"Failed to post audit comment on {record_id} for {media_id}")
    lines.append(f"✅ {len(proposed)} field{'s' if len(proposed) != 1 else ''} written successfully")

    return [TextContent(type="text", text="\n".join(lines))]


# =============================================================================
# Main Entry Point
# =============================================================================


async def main():
    """Run the MCP server.

    Supports two transport modes:
    - stdio (default): For local Claude Desktop subprocess spawning
    - http: For Docker/HTTP environments with Streamable HTTP transport

    Set MCP_TRANSPORT=http to use Streamable HTTP transport on port 8080.
    Legacy value MCP_TRANSPORT=sse is accepted for backward compatibility.
    """
    transport = os.environ.get("MCP_TRANSPORT", "stdio")

    if transport in ("http", "sse"):  # Accept "sse" for backward compat
        import uvicorn
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from starlette.applications import Starlette
        from starlette.routing import Mount

        session_manager = StreamableHTTPSessionManager(
            app=server,
            json_response=False,
            stateless=False,
            session_idle_timeout=1800,  # 30 min idle cleanup
        )

        @contextlib.asynccontextmanager
        async def lifespan(app):
            async with session_manager.run():
                yield

        app = Starlette(
            debug=False,
            lifespan=lifespan,
            routes=[
                Mount("/mcp", app=session_manager.handle_request),
            ],
        )

        config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="info")
        srv = uvicorn.Server(config)
        await srv.serve()
    else:
        # Default stdio transport for local Claude Desktop
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
