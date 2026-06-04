"""Tests for api/services/mmingest/parsers.py.

Parser tests are driven off two sources:
  1. The real server fixture (tests/services/mmingest/fixtures/autoindex_snapshot.html)
     captured 2026-06-04 from https://mmingest.pbswi.wisc.edu/IWP/
  2. Synthetic test cases for variant rules, edge cases, and prefix resolution.

Coverage:
  * AutoindexParser: fixture-driven, counts, metadata, subdirectory extraction
  * parse_filename: REV supersession grouping, PLEDGE/DS coexistence, unknown-tag
    preservation, 6POL/2WLI/6WLI resolution, unparseable graceful degradation
  * select_primary: winner selection, superseded list, unknown-tag passthrough
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.services.mmingest.parsers import (
    KNOWN_VARIANT_VOCAB,
    AutoindexParser,
    DirEntry,
    GroupSelectionResult,
    ParsedFilename,
    ParseError,
    parse_filename,
    select_primary,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "autoindex_snapshot.html"


# ---------------------------------------------------------------------------
# AutoindexParser — fixture-driven tests
# ---------------------------------------------------------------------------


class TestAutoindexParserFixture:
    """Tests driven off the real server snapshot captured 2026-06-04."""

    @pytest.fixture
    def fixture_html(self) -> str:
        return FIXTURE_PATH.read_text(encoding="utf-8")

    @pytest.fixture
    def entries(self, fixture_html: str) -> list[DirEntry]:
        parser = AutoindexParser(base_url="https://mmingest.pbswi.wisc.edu/IWP/")
        return parser.parse(fixture_html)

    def test_fixture_file_exists(self):
        assert FIXTURE_PATH.exists(), f"Fixture not found at {FIXTURE_PATH}"

    def test_parses_non_empty(self, entries: list[DirEntry]):
        assert len(entries) > 0, "Should parse at least one entry from fixture"

    def test_no_directories_in_iwp(self, entries: list[DirEntry]):
        """The IWP listing is flat — no subdirectories expected."""
        dirs = [e for e in entries if e.is_dir]
        assert dirs == [], f"Expected no subdirs in IWP but found: {[d.name for d in dirs]}"

    def test_all_entries_are_files(self, entries: list[DirEntry]):
        assert all(not e.is_dir for e in entries)

    def test_known_files_present(self, entries: list[DirEntry]):
        """Spot-check well-known files from the fixture."""
        names = {e.name for e in entries}
        assert "6POL0101CLEAN.srt" in names
        assert "6POL0101_REV20260319.srt" in names
        assert "6POL0101_REV20260319.mp4" in names

    def test_urls_are_absolute(self, entries: list[DirEntry]):
        for e in entries:
            assert e.url.startswith("https://"), f"URL not absolute: {e.url}"

    def test_urls_contain_filename(self, entries: list[DirEntry]):
        for e in entries:
            assert e.name in e.url, f"Filename {e.name!r} not in URL {e.url!r}"

    def test_modified_dates_parsed(self, entries: list[DirEntry]):
        """At least some entries should have modification dates."""
        with_dates = [e for e in entries if e.modified is not None]
        assert len(with_dates) > 0, "Should parse modification dates from fixture"

    def test_modified_date_is_utc(self, entries: list[DirEntry]):

        for e in entries:
            if e.modified is not None:
                assert e.modified.tzinfo is not None, "Datetime must be timezone-aware"

    def test_size_bytes_parsed(self, entries: list[DirEntry]):
        """At least some entries should have file sizes."""
        with_sizes = [e for e in entries if e.size_bytes is not None]
        assert len(with_sizes) > 0, "Should parse file sizes from fixture"

    def test_specific_file_size(self, entries: list[DirEntry]):
        """6POL0101CLEAN.scc should be ~96K = 98304 bytes."""
        target = next((e for e in entries if e.name == "6POL0101CLEAN.scc"), None)
        assert target is not None, "6POL0101CLEAN.scc not found in fixture"
        assert target.size_bytes is not None
        # 96K = 96 * 1024 = 98304
        assert target.size_bytes == 96 * 1024

    def test_no_parent_directory_entry(self, entries: list[DirEntry]):
        """Parent directory link must not appear in results."""
        names = {e.name for e in entries}
        # Parent dir link text is "Parent Directory"; href is "/"
        assert "/" not in names
        assert "Parent Directory" not in names

    def test_no_sort_column_links(self, entries: list[DirEntry]):
        """Sort column links (?C=N;O=D etc.) must not appear."""
        for e in entries:
            assert not e.name.startswith("?"), f"Sort link leaked into entries: {e.name}"

    def test_file_count_reasonable(self, entries: list[DirEntry]):
        """IWP listing has many files — expect at least 40 entries."""
        assert len(entries) >= 40, f"Expected >= 40 entries, got {len(entries)}"


class TestAutoindexParserSynthetic:
    """Unit tests for parser logic independent of live fixture."""

    def test_table_format_entries(self):
        """Parse Apache table-format listing (matches real server output)."""
        html = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><head><title>Index of /TEST</title></head><body>
<h1>Index of /TEST</h1>
<table>
 <tr><th><a href="?C=N;O=D">Name</a></th><th><a href="?C=M;O=A">Last modified</a></th><th><a href="?C=S;O=A">Size</a></th></tr>
 <tr><th colspan="5"><hr></th></tr>
<tr><td><a href="/">Parent Directory</a></td><td></td><td>-</td></tr>
<tr><td><a href="subdir/">subdir/</a></td><td align="right">2026-01-01 10:00  </td><td align="right">  - </td></tr>
<tr><td><a href="2WLI0501HD.srt">2WLI0501HD.srt</a></td><td align="right">2026-01-15 14:30  </td><td align="right"> 45K</td></tr>
<tr><td><a href="9UNP2005.mp4">9UNP2005.mp4</a></td><td align="right">2026-01-16 09:00  </td><td align="right">1.8G</td></tr>
</table></body></html>"""
        parser = AutoindexParser(base_url="https://test.example.com/TEST/")
        entries = parser.parse(html)

        files = [e for e in entries if not e.is_dir]
        dirs = [e for e in entries if e.is_dir]

        assert len(files) == 2
        assert len(dirs) == 1
        assert dirs[0].name == "subdir"

        srt = next(e for e in files if e.name == "2WLI0501HD.srt")
        assert srt.size_bytes == 45 * 1024
        assert srt.modified is not None
        assert srt.modified.year == 2026

        mp4 = next(e for e in files if e.name == "9UNP2005.mp4")
        assert mp4.size_bytes == int(1.8 * 1024 * 1024 * 1024)

    def test_relative_urls_resolved(self):
        html = '<html><body><a href="file.srt">file.srt</a></body></html>'
        parser = AutoindexParser(base_url="https://example.com/dir/")
        entries = parser.parse(html)
        assert len(entries) == 1
        assert entries[0].url == "https://example.com/dir/file.srt"

    def test_parent_dir_link_href_slash_skipped(self):
        html = """<html><body>
        <a href="/">Parent Directory</a>
        <a href="good.srt">good.srt</a>
        </body></html>"""
        parser = AutoindexParser(base_url="https://example.com/subdir/")
        entries = parser.parse(html)
        assert len(entries) == 1
        assert entries[0].name == "good.srt"

    def test_sort_links_skipped(self):
        html = """<html><body>
        <a href="?C=N;O=D">Name</a>
        <a href="?C=M;O=A">Last modified</a>
        <a href="file.srt">file.srt</a>
        </body></html>"""
        parser = AutoindexParser(base_url="https://example.com/")
        entries = parser.parse(html)
        assert len(entries) == 1
        assert entries[0].name == "file.srt"

    def test_empty_listing_returns_empty(self):
        html = """<!DOCTYPE HTML><html><head><title>Index of /empty</title></head>
        <body><h1>Index of /empty</h1></body></html>"""
        parser = AutoindexParser(base_url="https://example.com/empty/")
        entries = parser.parse(html)
        assert entries == []

    def test_size_gigabytes(self):
        html = """<html><body>
        <table>
        <tr><td><a href="big.mp4">big.mp4</a></td>
        <td align="right">2026-01-01 12:00  </td>
        <td align="right">1.8G</td></tr>
        </table>
        </body></html>"""
        parser = AutoindexParser(base_url="https://example.com/")
        entries = parser.parse(html)
        assert len(entries) == 1
        assert entries[0].size_bytes == int(1.8 * 1024**3)

    def test_zero_size_parsed(self):
        """Zero-byte files (like 6POL0102CLEAN.scc in fixture) parse as 0 bytes."""
        html = """<html><body>
        <table>
        <tr><td><a href="empty.scc">empty.scc</a></td>
        <td align="right">2026-01-01 12:00  </td>
        <td align="right">  0 </td></tr>
        </table>
        </body></html>"""
        parser = AutoindexParser(base_url="https://example.com/")
        entries = parser.parse(html)
        assert len(entries) == 1
        assert entries[0].size_bytes == 0


# ---------------------------------------------------------------------------
# parse_filename — grammar and variant rules
# ---------------------------------------------------------------------------


class TestParseFilenameGrammar:
    """Core grammar parsing tests."""

    def test_minimal_media_id(self):
        """Plain PREFIX+SSEE, no HD, no suffix."""
        result = parse_filename("6POL0101.srt")
        assert isinstance(result, ParsedFilename)
        assert result.prefix == "6POL"
        assert result.season == 1
        assert result.episode == 1
        assert result.hd is False
        assert result.media_id == "6POL0101"
        assert result.revision_date is None
        assert result.variant_tag is None
        assert result.unknown_tag is None

    def test_with_hd_flag(self):
        result = parse_filename("2WLI0501HD.mp4")
        assert isinstance(result, ParsedFilename)
        assert result.prefix == "2WLI"
        assert result.season == 5
        assert result.episode == 1
        assert result.hd is True
        assert result.media_id == "2WLI0501"

    def test_extension_stripped(self):
        for ext in [".mp4", ".srt", ".scc"]:
            result = parse_filename(f"6POL0101{ext}")
            assert isinstance(result, ParsedFilename), f"Failed for {ext}"

    def test_file_type_extracted(self):
        result = parse_filename("6POL0101.srt")
        assert isinstance(result, ParsedFilename)
        assert result.file_type == ".srt"

    def test_uppercase_normalisation(self):
        """Lowercase input is normalised to uppercase before matching."""
        result = parse_filename("6pol0101hd.srt")
        assert isinstance(result, ParsedFilename)
        assert result.prefix == "6POL"
        assert result.hd is True


class TestParseFilenameRevision:
    """Tests for _REV<YYYYMMDD> revision date extraction."""

    def test_rev_date_extracted(self):
        result = parse_filename("6POL0101_REV20260319.srt")
        assert isinstance(result, ParsedFilename)
        assert result.revision_date == "2026-03-19"
        assert result.variant_tag is None
        assert result.unknown_tag is None

    def test_rev_date_with_hd(self):
        result = parse_filename("2WLI0501HD_REV20251201.srt")
        assert isinstance(result, ParsedFilename)
        assert result.hd is True
        assert result.revision_date == "2025-12-01"

    def test_rev_date_is_iso_string(self):
        result = parse_filename("6POL0106_REV20260423.srt")
        assert isinstance(result, ParsedFilename)
        assert result.revision_date == "2026-04-23"

    def test_invalid_rev_date_returns_parse_error(self):
        """_REV99999999 should produce a ParseError, not ValueError."""
        result = parse_filename("6POL0101_REV99991399.srt")
        assert isinstance(result, ParseError)
        assert "Invalid revision date" in result.reason


class TestParseFilenameVariants:
    """Tests for the known-variant vocab and unknown-tag handling."""

    def test_pledge_is_known_variant(self):
        """PLEDGE is in KNOWN_VARIANT_VOCAB — sets variant_tag, not unknown_tag."""
        result = parse_filename("2WLI0501HD_PLEDGE.mp4")
        assert isinstance(result, ParsedFilename)
        assert result.variant_tag == "PLEDGE"
        assert result.unknown_tag is None

    def test_ds_is_known_variant(self):
        """DS is in KNOWN_VARIANT_VOCAB — sets variant_tag."""
        result = parse_filename("9UNP2005_DS.srt")
        assert isinstance(result, ParsedFilename)
        assert result.variant_tag == "DS"
        assert result.unknown_tag is None

    def test_unknown_tag_preserved(self):
        """Unknown tags like CLEAN or NoBugTest must NOT be silently dropped."""
        result = parse_filename("6POL0103_NoBugTest.srt")
        assert isinstance(result, ParsedFilename)
        assert result.variant_tag is None
        assert result.unknown_tag is not None
        assert result.unknown_tag.upper() == "NOBUGTEST"

    def test_unknown_tag_clean(self):
        """CLEAN without underscore separator is a grammar mismatch (ParseError).

        Files like '6POL0101CLEAN.scc' appear in the real fixture.  The Media
        ID grammar requires a '_' before any trailing tag, so 'CLEAN' attached
        directly to the episode digits does NOT produce a trailing tag — the
        regex can't consume it and the whole match fails.  These files are real
        deliverables but use a freeform naming convention outside the grammar;
        they surface as ParseError and the crawler emits them with
        media_id=None (prefix_category='unknown') for S2 to handle.

        A file with a properly underscore-separated unknown tag
        (e.g. '6POL0103_NoBugTest.srt') DOES produce unknown_tag — see
        test_unknown_tag_preserved.
        """
        result = parse_filename("6POL0101CLEAN.srt")
        # CLEAN has no '_' separator: grammar mismatch -> ParseError
        assert isinstance(result, ParseError)
        assert result.stem == "6POL0101CLEAN"
        assert ".srt" in result.file_type

    def test_known_variant_vocab_contents(self):
        """Smoke-check that the vocabulary constants are present."""
        assert "PLEDGE" in KNOWN_VARIANT_VOCAB
        assert "DS" in KNOWN_VARIANT_VOCAB

    def test_lowercase_tag_still_classified(self):
        """Tags parsed from mixed-case server filenames are uppercased for comparison."""
        result = parse_filename("2WLI0501HD_pledge.mp4")
        assert isinstance(result, ParsedFilename)
        # "pledge" uppercases to "PLEDGE" which is in the vocab
        assert result.variant_tag == "PLEDGE"


class TestParseFilenameUnparseable:
    """Tests for filenames that don't match the grammar."""

    def test_freeform_name_returns_parse_error(self):
        """'INSIDE_WI_INTRO_20260409.srt' doesn't match — ParseError expected."""
        result = parse_filename("INSIDE_WI_INTRO_20260409.srt")
        assert isinstance(result, ParseError)

    def test_short_prefix_returns_parse_error(self):
        result = parse_filename("POL0101.srt")  # 3-char prefix
        assert isinstance(result, ParseError)

    def test_six_char_prefix_returns_parse_error(self):
        """'6POLS0101NIL' — prefix would be '6POL', then 'S' is not a digit."""
        result = parse_filename("6POLS0101NIL.srt")
        # 6POLS: 5 chars before the SSEE — the RE requires exactly 4-char prefix
        # then 2+2 digits.  "6POL" is 4 chars, then "S" is not digit -> no match.
        assert isinstance(result, ParseError)

    def test_parse_error_has_reason(self):
        result = parse_filename("RANDOM_TEXT.srt")
        assert isinstance(result, ParseError)
        assert result.reason  # non-empty reason

    def test_parse_error_preserves_file_type(self):
        result = parse_filename("RANDOM_TEXT.srt")
        assert isinstance(result, ParseError)
        assert result.file_type == ".srt"


class TestPrefixResolution:
    """Tests for prefix -> show_name and prefix_category lookup."""

    def test_6pol_resolves_correctly(self):
        """6POL -> Inside Wisconsin Politics (non-broadcast)."""
        result = parse_filename("6POL0101.srt")
        assert isinstance(result, ParsedFilename)
        assert result.show_name == "Inside Wisconsin Politics"
        assert result.prefix_category == "non-broadcast"

    def test_2wli_resolves_correctly(self):
        """2WLI -> Wisconsin Life (broadcast)."""
        result = parse_filename("2WLI0501HD.srt")
        assert isinstance(result, ParsedFilename)
        assert result.show_name == "Wisconsin Life"
        assert result.prefix_category == "broadcast"

    def test_6wli_resolves_correctly(self):
        """6WLI -> Wisconsin Life Digital Shorts (non-broadcast).

        This is the key 2WLI vs 6WLI distinction test:
        both are 'Wisconsin Life' but different shows/categories.
        """
        result = parse_filename("6WLI0101.srt")
        assert isinstance(result, ParsedFilename)
        assert result.show_name == "Wisconsin Life Digital Shorts"
        assert result.prefix_category == "non-broadcast"
        # Confirm it's different from 2WLI
        assert result.show_name != "Wisconsin Life"

    def test_9unp_resolves_correctly(self):
        """9UNP -> University Place (broadcast)."""
        result = parse_filename("9UNP2005.srt")
        assert isinstance(result, ParsedFilename)
        assert result.show_name == "University Place"
        assert result.prefix_category == "broadcast"

    def test_unknown_prefix_degrades_gracefully(self):
        """Unrecognised prefix returns show_name=None, category='unknown'."""
        result = parse_filename("ZZZZ0101.srt")
        assert isinstance(result, ParsedFilename)
        assert result.show_name is None
        assert result.prefix_category == "unknown"

    def test_broadcast_leading_digit_2(self):
        """Leading digit 2 => broadcast category for several known prefixes."""
        result = parse_filename("2HNW0101.srt")
        assert isinstance(result, ParsedFilename)
        assert result.prefix_category == "broadcast"

    def test_broadcast_leading_digit_9(self):
        result = parse_filename("9DCU0101.srt")
        assert isinstance(result, ParsedFilename)
        assert result.prefix_category == "broadcast"

    def test_non_broadcast_leading_digit_6(self):
        result = parse_filename("6AKA0101.srt")
        assert isinstance(result, ParsedFilename)
        assert result.prefix_category == "non-broadcast"


# ---------------------------------------------------------------------------
# select_primary — variant-group winner selection
# ---------------------------------------------------------------------------


class TestSelectPrimary:
    """Tests for select_primary() over (media_id, variant_tag) groups."""

    def _make_parsed(
        self,
        filename: str,
    ) -> ParsedFilename:
        """Parse a filename and assert it succeeds."""
        result = parse_filename(filename)
        assert isinstance(result, ParsedFilename), f"Expected ParsedFilename for {filename}, got: {result}"
        return result

    def test_single_entry_is_primary(self):
        entry = self._make_parsed("6POL0101.srt")
        results = select_primary([entry])
        assert len(results) == 1
        r = results[0]
        assert r.primary == entry
        assert r.variants == []
        assert r.superseded == []

    def test_empty_list_returns_empty(self):
        results = select_primary([])
        assert results == []

    def test_rev_latest_wins(self):
        """Within a single (media_id, variant_tag) group, latest REV date wins.

        Both entries must share the same media_id so select_primary sees them
        in the same group.  We use two REV filenames for 6POL0101.
        """
        old = self._make_parsed("6POL0101_REV20260423.srt")
        new = self._make_parsed("6POL0101_REV20260430.srt")
        results = select_primary([old, new])
        assert len(results) == 1
        r = results[0]
        assert r.primary is not None
        assert r.primary.revision_date == "2026-04-30"
        assert old in r.superseded
        assert len(r.superseded) == 1
        assert r.variants == []

    def test_rev_supersession_correct_order(self):
        """Older REV ends up in superseded, newer is primary — same media_id."""
        older = self._make_parsed("6POL0101_REV20260423.srt")
        newer = self._make_parsed("6POL0101_REV20260430.srt")
        results = select_primary([older, newer])
        assert len(results) == 1
        r = results[0]
        assert r.primary == newer
        assert older in r.superseded

    def test_different_media_ids_produce_separate_groups(self):
        """Entries with different media_ids are placed in separate groups."""
        entry_0106 = self._make_parsed("6POL0106_REV20260423.srt")
        entry_0107 = self._make_parsed("6POL0107_REV20260430.srt")
        results = select_primary([entry_0106, entry_0107])
        # Two distinct media_ids → two groups, each with one primary
        assert len(results) == 2
        primaries = [r.primary for r in results]
        assert entry_0106 in primaries
        assert entry_0107 in primaries
        for r in results:
            assert r.superseded == []

    def test_pledge_group_rev_independent_of_no_variant_group(self):
        """PLEDGE variant's REV race is independent of the no-variant primary group.

        Scenario: one show (6POL0101) has two REV deliveries of the no-variant
        primary AND two REV deliveries of the PLEDGE cut.  select_primary must
        pick the winning REV within each (media_id, variant_tag) group without
        cross-contamination.
        """
        # No-variant group: two REVs
        primary_old = self._make_parsed("6POL0101_REV20260410.mp4")
        primary_new = self._make_parsed("6POL0101_REV20260430.mp4")
        # PLEDGE group: two REVs
        pledge_old = self._make_parsed("6POL0101_REV20260410_PLEDGE.mp4")
        pledge_new = self._make_parsed("6POL0101_REV20260430_PLEDGE.mp4")

        results = select_primary([primary_old, primary_new, pledge_old, pledge_new])

        # Expect exactly two groups
        assert len(results) == 2
        by_key = {r.group_key: r for r in results}

        # No-variant group (variant_tag=None)
        no_variant = by_key[("6POL0101", None)]
        assert no_variant.primary == primary_new
        assert primary_old in no_variant.superseded

        # PLEDGE group (variant_tag="PLEDGE")
        pledge_group = by_key[("6POL0101", "PLEDGE")]
        assert pledge_group.primary == pledge_new
        assert pledge_old in pledge_group.superseded

    def test_known_variant_passes_through(self):
        """PLEDGE variant is routed to its own group, not mixed with no-variant.

        This test verifies that passing a mix of no-variant and PLEDGE entries
        for the same media_id produces two separate groups, not a collision.
        """
        primary_entry = self._make_parsed("2WLI0501HD.srt")
        pledge_entry = self._make_parsed("2WLI0501HD_PLEDGE.mp4")
        assert pledge_entry.variant_tag == "PLEDGE"

        results = select_primary([primary_entry, pledge_entry])
        assert len(results) == 2
        by_key = {r.group_key: r for r in results}
        assert by_key[("2WLI0501", None)].primary == primary_entry
        assert by_key[("2WLI0501", "PLEDGE")].primary == pledge_entry

    def test_unknown_tag_excluded_from_rev_race(self):
        """Entries with unknown_tag are returned as variants, not superseded.

        Both entries have the same media_id, so they land in one group.
        The unknown-tag entry is a bystander and does not participate in the
        REV race.
        """
        known = self._make_parsed("6POL0101.srt")
        unknown_tagged = self._make_parsed("6POL0101_NoBugTest.srt")
        assert unknown_tagged.unknown_tag is not None

        results = select_primary([known, unknown_tagged])
        assert len(results) == 1
        r = results[0]
        assert r.primary == known
        assert unknown_tagged in r.variants
        assert r.superseded == []

    def test_no_rev_multiple_entries(self):
        """Without revision dates, first entry wins, rest go to superseded."""
        a = self._make_parsed("6POL0101.srt")
        b = self._make_parsed("6POL0101.mp4")
        results = select_primary([a, b])
        assert len(results) == 1
        r = results[0]
        assert r.primary == a
        assert b in r.superseded


# ---------------------------------------------------------------------------
# Fixture: confirm specific real-server entries parse correctly
# ---------------------------------------------------------------------------


class TestFixtureParseFilenames:
    """Parse known filenames from the real fixture to verify end-to-end."""

    def test_6pol0101_plain_srt(self):
        result = parse_filename("6POL0101.srt")
        # Does not appear in fixture but grammar test
        assert isinstance(result, ParsedFilename)

    def test_6pol0101_rev_mp4(self):
        """6POL0101_REV20260319.mp4 — the real revision file from the fixture."""
        result = parse_filename("6POL0101_REV20260319.mp4")
        assert isinstance(result, ParsedFilename)
        assert result.prefix == "6POL"
        assert result.season == 1
        assert result.episode == 1
        assert result.revision_date == "2026-03-19"
        assert result.media_id == "6POL0101"
        assert result.variant_tag is None
        assert result.show_name == "Inside Wisconsin Politics"

    def test_6pol0101_rev_srt(self):
        result = parse_filename("6POL0101_REV20260319.srt")
        assert isinstance(result, ParsedFilename)
        assert result.revision_date == "2026-03-19"
        assert result.file_type == ".srt"

    def test_6pol0106_rev_scc(self):
        result = parse_filename("6POL0106_REV20260423.scc")
        assert isinstance(result, ParsedFilename)
        assert result.revision_date == "2026-04-23"

    def test_inside_wi_intro_is_parse_error(self):
        """INSIDE_WI_INTRO_20260409.srt — freeform name, not a Media ID."""
        result = parse_filename("INSIDE_WI_INTRO_20260409.srt")
        assert isinstance(result, ParseError)

    def test_6pols0101nil_is_parse_error(self):
        """6POLS0101NIL — 5-char-ish thing that doesn't fit 4-char prefix grammar."""
        result = parse_filename("6POLS0101NIL.srt")
        assert isinstance(result, ParseError)
