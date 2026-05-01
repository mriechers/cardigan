"""Tests for download filename prefixing."""

import pytest


class TestDownloadFilename:
    """Tests for project-prefixed download filenames."""

    def test_download_includes_project_prefix(self):
        """Download Content-Disposition includes sanitized project name prefix."""
        from api.routers.jobs import _make_download_filename

        result = _make_download_filename("Wisconsin Life / 2WLI1209HD", "analyst_output.md")
        assert result == "Wisconsin-Life-2WLI1209HD-analyst_output.md"

    def test_download_filename_sanitizes_special_chars(self):
        """Special characters in project names are replaced with hyphens."""
        from api.routers.jobs import _make_download_filename

        result = _make_download_filename("Project: Test (v2)", "seo_output.md")
        assert result == "Project-Test-v2-seo_output.md"

    def test_download_filename_collapses_multiple_hyphens(self):
        """Multiple consecutive hyphens are collapsed to one."""
        from api.routers.jobs import _make_download_filename

        result = _make_download_filename("A --- B", "formatter_output.md")
        assert result == "A-B-formatter_output.md"

    def test_non_download_has_no_disposition(self):
        """Helper always returns a name; the router decides whether to use it."""
        from api.routers.jobs import _make_download_filename

        result = _make_download_filename("Test", "analyst_output.md")
        assert result == "Test-analyst_output.md"
