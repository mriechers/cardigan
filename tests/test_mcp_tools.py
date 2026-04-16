"""Tests for MCP server tool handlers.

Tests the tool handler functions directly (not via MCP protocol),
using a temporary OUTPUT directory to isolate filesystem operations.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

from mcp_server.server import handle_save_keyword_report, handle_save_revision


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


@pytest.mark.asyncio
async def test_save_keyword_report_succeeds(project_with_manifest):
    """save_keyword_report should write a file and update manifest."""
    project_name, project_path = project_with_manifest
    result = await handle_save_keyword_report({
        "project_name": project_name,
        "content": "# Keywords\nTest keyword report.",
    })
    assert "✅" in result[0].text
    assert "keyword_report_v1.md" in result[0].text

    assert (project_path / "keyword_report_v1.md").exists()
    manifest = json.loads((project_path / "manifest.json").read_text())
    assert len(manifest["keyword_reports"]) == 1


@pytest.mark.asyncio
async def test_save_keyword_report_auto_versions(project_with_manifest):
    """save_keyword_report should auto-increment version numbers."""
    project_name, project_path = project_with_manifest

    await handle_save_keyword_report({"project_name": project_name, "content": "Report 1"})
    result = await handle_save_keyword_report({"project_name": project_name, "content": "Report 2"})
    assert "keyword_report_v2.md" in result[0].text


@pytest.mark.asyncio
async def test_save_keyword_report_completes_within_timeout(project_with_manifest):
    """save_keyword_report should complete within a reasonable time."""
    project_name, _ = project_with_manifest
    result = await asyncio.wait_for(
        handle_save_keyword_report({"project_name": project_name, "content": "Timeout test"}),
        timeout=5.0,
    )
    assert "✅" in result[0].text


@pytest.mark.asyncio
async def test_validate_copy_all_valid(output_dir):
    """validate_copy should report all fields valid when under limits."""
    from mcp_server.server import handle_validate_copy

    result = await handle_validate_copy({
        "title": "Wisconsin Life | Alice Good Café in Verona",
        "short_description": "In Verona, Alice Good brews Colombian coffee and community.",
        "long_description": "Alice Good Café in Verona is more than a coffee shop.",
    })
    text = result[0].text
    assert "✅" in text
    assert "Yes" in text  # "All valid: ✅ Yes"


@pytest.mark.asyncio
async def test_validate_copy_over_limit(output_dir):
    """validate_copy should flag fields that exceed character limits."""
    from mcp_server.server import handle_validate_copy

    result = await handle_validate_copy({
        "title": "X" * 85,
        "short_description": "X" * 105,
        "long_description": "OK",
    })
    text = result[0].text
    assert "❌" in text
    assert "OVER LIMIT" in text
    assert "85" in text


@pytest.mark.asyncio
async def test_validate_copy_partial_fields(output_dir):
    """validate_copy should work with only some fields provided."""
    from mcp_server.server import handle_validate_copy

    result = await handle_validate_copy({
        "title": "Just a title",
    })
    text = result[0].text
    assert "12" in text
    assert "Error" not in text


@pytest.mark.asyncio
async def test_validate_copy_with_keywords(output_dir):
    """validate_copy should count keywords when provided."""
    from mcp_server.server import handle_validate_copy

    result = await handle_validate_copy({
        "title": "Test Title",
        "keywords": "coffee, Verona, Wisconsin, fair trade, community",
    })
    text = result[0].text
    assert "5" in text  # 5 keywords


@pytest.mark.asyncio
async def test_validate_copy_empty(output_dir):
    """validate_copy should handle no fields gracefully."""
    from mcp_server.server import handle_validate_copy

    result = await handle_validate_copy({})
    text = result[0].text
    assert "Error" in text or "at least one" in text.lower()


@pytest.mark.asyncio
async def test_list_project_files_shows_all_files(project_with_manifest):
    """list_project_files should return all files in the project folder."""
    from mcp_server.server import handle_list_project_files

    project_name, project_path = project_with_manifest

    # Add some extra files to simulate a real project
    (project_path / "copy_revision_v1.md").write_text("Revision 1")
    (project_path / "keyword_report_v1.md").write_text("Keywords")
    semrush_dir = project_path / "semrush"
    semrush_dir.mkdir()
    (semrush_dir / "export_2026-04-16.csv").write_text("keyword,volume\ncoffee,1000")

    result = await handle_list_project_files({"project_name": project_name})
    text = result[0].text

    assert "analyst_output.md" in text
    assert "copy_revision_v1.md" in text
    assert "keyword_report_v1.md" in text
    assert "export_2026-04-16.csv" in text
    assert "manifest.json" in text


@pytest.mark.asyncio
async def test_list_project_files_missing_project(output_dir):
    """list_project_files should return error for nonexistent project."""
    from mcp_server.server import handle_list_project_files

    result = await handle_list_project_files({"project_name": "nonexistent"})
    text = result[0].text
    assert "not found" in text.lower() or "Error" in text


@pytest.mark.asyncio
async def test_list_project_files_empty_project(output_dir):
    """list_project_files should handle a project with only a manifest."""
    from mcp_server.server import handle_list_project_files

    project_name = "2WLIEmptyProject"
    project_path = output_dir / project_name
    project_path.mkdir()
    (project_path / "manifest.json").write_text('{"project_name": "2WLIEmptyProject"}')

    result = await handle_list_project_files({"project_name": project_name})
    text = result[0].text
    assert "manifest.json" in text
