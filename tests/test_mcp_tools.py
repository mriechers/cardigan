"""Tests for MCP server tool handlers.

Tests the tool handler functions directly (not via MCP protocol),
using a temporary OUTPUT directory to isolate filesystem operations.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

from mcp_server.server import handle_save_revision


@pytest.fixture
def output_dir(tmp_path):
    """Provide a temporary OUTPUT directory and patch the module global."""
    with patch("mcp_server.server.OUTPUT_DIR", tmp_path):
        yield tmp_path


@pytest.fixture
def project_with_manifest(output_dir):
    """Create a project folder with a minimal manifest for testing."""
    project_name = "2WLITestProjectSM"
    project_path = output_dir / project_name
    project_path.mkdir()
    manifest = {
        "project_name": project_name,
        "phases": [
            {"name": "analyst", "status": "completed"},
            {"name": "formatter", "status": "completed"},
            {"name": "seo", "status": "completed"},
        ],
        "outputs": {
            "analysis": "analyst_output.md",
            "formatted_transcript": "formatter_output.md",
            "seo_metadata": "seo_output.md",
        },
        "revisions": [],
        "keyword_reports": [],
    }
    (project_path / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (project_path / "analyst_output.md").write_text("# Analysis\nTest analysis content.")
    (project_path / "formatter_output.md").write_text("# Formatted Transcript\nTest transcript.")
    (project_path / "seo_output.md").write_text("# SEO\nTest SEO content.")
    return project_name, project_path


@pytest.mark.asyncio
async def test_save_revision_succeeds(project_with_manifest):
    """save_revision should write a file and update manifest."""
    project_name, project_path = project_with_manifest
    result = await handle_save_revision({
        "project_name": project_name,
        "content": "# Revision\nTest revision content.",
    })
    assert len(result) == 1
    assert "✅" in result[0].text
    assert "copy_revision_v1.md" in result[0].text

    assert (project_path / "copy_revision_v1.md").exists()
    assert (project_path / "copy_revision_v1.md").read_text() == "# Revision\nTest revision content."

    manifest = json.loads((project_path / "manifest.json").read_text())
    assert len(manifest["revisions"]) == 1
    assert manifest["revisions"][0]["version"] == 1


@pytest.mark.asyncio
async def test_save_revision_auto_versions(project_with_manifest):
    """save_revision should auto-increment version numbers."""
    project_name, project_path = project_with_manifest

    await handle_save_revision({"project_name": project_name, "content": "Version 1"})
    result = await handle_save_revision({"project_name": project_name, "content": "Version 2"})
    assert "copy_revision_v2.md" in result[0].text
    assert (project_path / "copy_revision_v2.md").read_text() == "Version 2"


@pytest.mark.asyncio
async def test_save_revision_missing_args(output_dir):
    """save_revision should return error for missing arguments."""
    result = await handle_save_revision({"project_name": "test"})
    assert "Error" in result[0].text

    result = await handle_save_revision({"content": "test"})
    assert "Error" in result[0].text


@pytest.mark.asyncio
async def test_save_revision_completes_within_timeout(project_with_manifest):
    """save_revision should complete within a reasonable time, not hang."""
    project_name, _ = project_with_manifest
    result = await asyncio.wait_for(
        handle_save_revision({
            "project_name": project_name,
            "content": "Timeout test content",
        }),
        timeout=5.0,
    )
    assert "✅" in result[0].text
