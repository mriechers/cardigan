"""Tests for utility functions in api/services/utils.py.

Tests for duplicate file sanitization, media ID extraction, and SRT parsing.
"""

from api.services.utils import (
    extract_media_id,
    sanitize_duplicate_filename,
)


class TestSanitizeDuplicateFilename:
    """Tests for OS duplicate file suffix detection and removal."""

    def test_macos_parenthesis_suffix(self):
        """Test macOS duplicate suffixes like (1), (2) are removed."""
        result, was_dup = sanitize_duplicate_filename("2WLIComicArtistSM (1)")
        assert result == "2WLIComicArtistSM"
        assert was_dup is True

        result, was_dup = sanitize_duplicate_filename("2WLI1209HD (2)")
        assert result == "2WLI1209HD"
        assert was_dup is True

        result, was_dup = sanitize_duplicate_filename("SomeFile (15)")
        assert result == "SomeFile"
        assert was_dup is True

    def test_windows_copy_suffix(self):
        """Test Windows '- Copy' suffixes are removed."""
        result, was_dup = sanitize_duplicate_filename("2WLI1209HD - Copy")
        assert result == "2WLI1209HD"
        assert was_dup is True

        result, was_dup = sanitize_duplicate_filename("2WLI1209HD - Copy (2)")
        assert result == "2WLI1209HD"
        assert was_dup is True

    def test_generic_copy_suffix(self):
        """Test generic 'copy' and 'copy N' suffixes are removed."""
        result, was_dup = sanitize_duplicate_filename("2WLI1209HD copy")
        assert result == "2WLI1209HD"
        assert was_dup is True

        result, was_dup = sanitize_duplicate_filename("2WLI1209HD copy 2")
        assert result == "2WLI1209HD"
        assert was_dup is True

    def test_preserves_normal_filenames(self):
        """Test that normal filenames without duplicates are unchanged."""
        result, was_dup = sanitize_duplicate_filename("2WLI1209HD")
        assert result == "2WLI1209HD"
        assert was_dup is False

        result, was_dup = sanitize_duplicate_filename("9UNP2005HD")
        assert result == "9UNP2005HD"
        assert was_dup is False

    def test_preserves_revision_dates(self):
        """Test that _REV[date] patterns are NOT stripped."""
        result, was_dup = sanitize_duplicate_filename("2BUC0000HDWEB02_REV20251202")
        assert result == "2BUC0000HDWEB02_REV20251202"
        assert was_dup is False

        result, was_dup = sanitize_duplicate_filename("2WLI1209HD_REV20260115")
        assert result == "2WLI1209HD_REV20260115"
        assert was_dup is False

    def test_preserves_segment_markers(self):
        """Test that segment markers like _SM, HD, WEB02 are NOT stripped."""
        result, was_dup = sanitize_duplicate_filename("2WLIComicArtistSM")
        assert result == "2WLIComicArtistSM"
        assert was_dup is False

        result, was_dup = sanitize_duplicate_filename("2WLI1209HD")
        assert result == "2WLI1209HD"
        assert was_dup is False

        result, was_dup = sanitize_duplicate_filename("2BUC0000HDWEB02")
        assert result == "2BUC0000HDWEB02"
        assert was_dup is False

    def test_preserves_position_markers(self):
        """Test that position markers like _midshow are NOT stripped."""
        result, was_dup = sanitize_duplicate_filename("2WLI1209HD_midshow")
        assert result == "2WLI1209HD_midshow"
        assert was_dup is False

        result, was_dup = sanitize_duplicate_filename("2WLI1209HD_excerpt")
        assert result == "2WLI1209HD_excerpt"
        assert was_dup is False

    def test_case_insensitive_copy(self):
        """Test that 'Copy' detection is case insensitive."""
        result, was_dup = sanitize_duplicate_filename("2WLI1209HD COPY")
        assert result == "2WLI1209HD"
        assert was_dup is True

        result, was_dup = sanitize_duplicate_filename("2WLI1209HD - COPY")
        assert result == "2WLI1209HD"
        assert was_dup is True


class TestExtractMediaId:
    """Tests for Media ID extraction from filenames."""

    def test_basic_extraction(self):
        """Test basic Media ID extraction from filenames."""
        assert extract_media_id("2WLI1209HD.srt") == "2WLI1209HD"
        assert extract_media_id("9UNP2005HD.txt") == "9UNP2005HD"

    def test_strips_for_claude_suffix(self):
        """Test that _ForClaude suffix is stripped."""
        assert extract_media_id("2WLI1209HD_ForClaude.txt") == "2WLI1209HD"
        assert extract_media_id("9UNP2005HD_ForClaude.srt") == "9UNP2005HD"

    def test_preserves_revision_dates(self):
        """Test that _REV[date] is preserved (it's a distinct Media ID)."""
        assert extract_media_id("2BUC0000HDWEB02_REV20251202.srt") == "2BUC0000HDWEB02_REV20251202"
        assert extract_media_id("2WLI1209HD_REV20260115.txt") == "2WLI1209HD_REV20260115"

    def test_strips_macos_duplicate_suffix(self):
        """Test that macOS duplicate suffixes are stripped."""
        assert extract_media_id("2WLIComicArtistSM (1).srt") == "2WLIComicArtistSM"
        assert extract_media_id("2WLI1209HD (2).txt") == "2WLI1209HD"

    def test_strips_copy_suffix(self):
        """Test that copy suffixes are stripped."""
        assert extract_media_id("2WLI1209HD - Copy.srt") == "2WLI1209HD"
        assert extract_media_id("2WLI1209HD copy.txt") == "2WLI1209HD"

    def test_combined_for_claude_and_duplicate(self):
        """Test handling of both _ForClaude and duplicate suffixes."""
        # _ForClaude is stripped first, then duplicate pattern
        assert extract_media_id("2WLI1209HD_ForClaude (1).txt") == "2WLI1209HD"

    def test_handles_paths(self):
        """Test extraction from full file paths."""
        assert extract_media_id("/path/to/2WLI1209HD.srt") == "2WLI1209HD"
        assert extract_media_id("transcripts/archive/2WLI1209HD (1).srt") == "2WLI1209HD"

    def test_project_style_names(self):
        """Test extraction from project-style names without standard Media ID format."""
        # These don't match the 4+4 pattern but should still work
        assert extract_media_id("Some_Project_Name.txt") == "Some_Project_Name"
        assert extract_media_id("WC_S01_trailer.srt") == "WC_S01_trailer"

    def test_sm_style_filenames_no_rev_false_positive(self):
        """Test that SM-style filenames don't false-match REV dates as media IDs."""
        # SM-style: show code + title slug, no standard 4+4 episode number.
        # The _REV suffix is a revision date, NOT a show code.
        assert extract_media_id("2WLIAliceGoodSM_REV20251106.srt") == "2WLIAliceGoodSM_REV20251106"
        assert extract_media_id("2WLIExchangeStudentSM.srt") == "2WLIExchangeStudentSM"
        assert extract_media_id("6WLIBigfootConvention_ForClaude.txt") == "6WLIBigfootConvention"

    def test_rev_suffix_not_extracted_as_media_id(self):
        """Test that _REV date suffixes are never mistaken for show codes."""
        # Previously, REV20251 would match [A-Z0-9]{4}\\d{4} as a false positive
        result = extract_media_id("2WLIAliceGoodSM_REV20251106.srt")
        assert result != "REV20251", "REV date suffix should not be extracted as media ID"
