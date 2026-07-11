"""Tests for the timestamp structured-contract engine's pure machinery.

Covers api.services.style_engine.timecodes (parse_timecode_to_ms,
format_media_manager, format_youtube, snap_chapters,
emit_media_manager_table, emit_youtube_list) and the chapter-list I/O half
in api.services.style_engine.phase_io (parse_chapter_list,
emit_timestamp_report). All rule data is synthetic, built inline as
StyleRules(raw=...) -- never depends on config/house_style.yaml -- so tests
that assert on a rendered value with an ODD config number (an off-spec
first_chapter title, an unusual constraints set) prove the engine is
data-driven, not hardcoded to the real house-style numbers.

Pipeline-stage integration (pre_stage.py/post_stage.py's timestamp path) is
covered separately in tests/test_style_stages.py.
"""

from __future__ import annotations

from api.services.style_engine.phase_io import emit_timestamp_report, parse_chapter_list
from api.services.style_engine.rules import StyleRules
from api.services.style_engine.timecodes import (
    Chapter,
    emit_media_manager_table,
    format_media_manager,
    format_youtube,
    parse_timecode_to_ms,
    snap_chapters,
)

# ---------------------------------------------------------------------------
# parse_timecode_to_ms
# ---------------------------------------------------------------------------


class TestParseTimecodeToMs:
    def test_mss(self):
        assert parse_timecode_to_ms("2:30") == 150000

    def test_mmss(self):
        assert parse_timecode_to_ms("12:34") == 754000

    def test_hmmss(self):
        assert parse_timecode_to_ms("1:23:45") == 5025000

    def test_hmmss_with_ms(self):
        assert parse_timecode_to_ms("0:02:29.999") == 149999

    def test_zero(self):
        assert parse_timecode_to_ms("0:00") == 0

    def test_ms_fractional_padding(self):
        # ".5" is 500ms (decimal fixed-point), not 5ms.
        assert parse_timecode_to_ms("0:00:01.5") == 1500

    def test_ms_two_digit_padding(self):
        assert parse_timecode_to_ms("0:00:01.50") == 1500

    def test_ms_three_digit_exact(self):
        assert parse_timecode_to_ms("0:00:01.123") == 1123

    def test_large_mss_minutes_no_upper_bound(self):
        # M:SS format has no hours segment -- minutes may exceed 59.
        assert parse_timecode_to_ms("75:30") == 4530000

    def test_leading_trailing_whitespace_tolerated(self):
        assert parse_timecode_to_ms("  2:30  ") == 150000

    def test_garbage_returns_none(self):
        assert parse_timecode_to_ms("not a time") is None

    def test_empty_string_returns_none(self):
        assert parse_timecode_to_ms("") is None

    def test_none_input_returns_none(self):
        assert parse_timecode_to_ms(None) is None

    def test_seconds_out_of_range_returns_none(self):
        assert parse_timecode_to_ms("1:75") is None

    def test_hmmss_minutes_out_of_range_returns_none(self):
        assert parse_timecode_to_ms("1:75:00") is None

    def test_too_many_segments_returns_none(self):
        assert parse_timecode_to_ms("1:02:03:04") is None

    def test_negative_not_matched_returns_none(self):
        assert parse_timecode_to_ms("-1:00") is None


# ---------------------------------------------------------------------------
# format_media_manager / format_youtube -- perfect math, boundary shapes
# ---------------------------------------------------------------------------


class TestFormatMediaManager:
    def test_start_format(self):
        assert format_media_manager(150000) == "0:02:30.000"

    def test_end_format(self):
        assert format_media_manager(149999, end=True) == "0:02:29.999"

    def test_exact_round_trip(self):
        # "perfect math": formatting then re-parsing recovers the exact ms.
        for ms in (0, 999, 150000, 149999, 5025678, 3599999):
            assert parse_timecode_to_ms(format_media_manager(ms, end=True)) == ms

    def test_no_leading_zero_stripped_on_hour_but_minutes_seconds_padded(self):
        assert format_media_manager(3661000) == "1:01:01.000"

    def test_zero(self):
        assert format_media_manager(0) == "0:00:00.000"

    def test_end_flag_does_not_alter_arithmetic(self):
        # end is documentation-only (see module docstring); math is honest
        # ms -> H:MM:SS.mmm either way.
        assert format_media_manager(1234567) == format_media_manager(1234567, end=True)


class TestFormatYoutube:
    def test_mss_under_one_hour(self):
        assert format_youtube(150000) == "2:30"

    def test_no_leading_zero_on_minutes(self):
        assert format_youtube(65000) == "1:05"

    def test_boundary_just_under_one_hour(self):
        assert format_youtube(3599000) == "59:59"

    def test_boundary_exactly_one_hour(self):
        assert format_youtube(3600000) == "1:00:00"

    def test_over_one_hour(self):
        assert format_youtube(5025000) == "1:23:45"

    def test_zero(self):
        assert format_youtube(0) == "0:00"

    def test_no_milliseconds_floors_subsecond_remainder(self):
        assert format_youtube(150999) == "2:30"


# ---------------------------------------------------------------------------
# Timecode math across a chain -- end = next start - 1ms; last end = srt end
# ---------------------------------------------------------------------------


class TestChapterChainMath:
    def test_three_chapter_chain_ends_are_next_start_minus_one_ms(self):
        chapters = [
            Chapter(title="Episode intro", start_ms=0),
            Chapter(title="Sports betting debate begins", start_ms=150000),
            Chapter(title="Legislative response", start_ms=495000),
        ]
        srt_end_ms = 1200000
        table = emit_media_manager_table(chapters, srt_end_ms)
        rows = table.splitlines()[2:]
        assert rows[0] == "| Episode intro | 0:00:00.000 | 0:02:29.999 |"
        assert rows[1] == "| Sports betting debate begins | 0:02:30.000 | 0:08:14.999 |"
        assert rows[2] == "| Legislative response | 0:08:15.000 | 0:20:00.000 |"

    def test_last_end_equals_srt_end_exactly(self):
        chapters = [Chapter(title="Episode intro", start_ms=0), Chapter(title="Wrap-up", start_ms=60000)]
        srt_end_ms = 1234567
        table = emit_media_manager_table(chapters, srt_end_ms)
        last_row = table.splitlines()[-1]
        end_text = last_row.split("|")[3].strip()
        assert parse_timecode_to_ms(end_text) == srt_end_ms


# ---------------------------------------------------------------------------
# snap_chapters
# ---------------------------------------------------------------------------


class TestSnapChaptersFirstChapterForcing:
    def test_replaces_title_when_model_first_chapter_is_at_zero(self):
        chapters = [Chapter(title="Intro stuff", start_ms=0), Chapter(title="Topic", start_ms=60000)]
        snapped, notes = snap_chapters(chapters, srt_end_ms=600000, max_chapters=10, first_chapter_title="Cold open")
        assert snapped[0] == Chapter(title="Cold open", start_ms=0)
        assert any("Cold open" in note for note in notes)

    def test_no_note_when_model_already_used_configured_title(self):
        chapters = [Chapter(title="Cold open", start_ms=0), Chapter(title="Topic", start_ms=60000)]
        snapped, notes = snap_chapters(chapters, srt_end_ms=600000, max_chapters=10, first_chapter_title="Cold open")
        assert snapped[0] == Chapter(title="Cold open", start_ms=0)
        assert notes == []

    def test_prepends_when_model_first_chapter_not_at_zero(self):
        chapters = [Chapter(title="Topic one", start_ms=30000)]
        snapped, notes = snap_chapters(chapters, srt_end_ms=600000, max_chapters=10, first_chapter_title="Cold open")
        assert len(snapped) == 2
        assert snapped[0] == Chapter(title="Cold open", start_ms=0)
        assert snapped[1] == Chapter(title="Topic one", start_ms=30000)
        assert any("prepended" in note for note in notes)

    def test_prepends_when_model_gave_no_chapters(self):
        snapped, notes = snap_chapters([], srt_end_ms=600000, max_chapters=10, first_chapter_title="Cold open")
        assert snapped == [Chapter(title="Cold open", start_ms=0)]
        assert any("no chapters" in note for note in notes)


class TestSnapChaptersDuplicates:
    def test_duplicate_starts_keep_first(self):
        chapters = [
            Chapter(title="Episode intro", start_ms=0),
            Chapter(title="First take", start_ms=90000),
            Chapter(title="Second take", start_ms=90000),
        ]
        snapped, notes = snap_chapters(
            chapters, srt_end_ms=600000, max_chapters=10, first_chapter_title="Episode intro"
        )
        starts = [c.start_ms for c in snapped]
        assert starts == [0, 90000]
        assert snapped[1].title == "First take"
        assert any("Second take" in note and "duplicate" in note for note in notes)

    def test_sorts_out_of_order_input(self):
        chapters = [
            Chapter(title="Later", start_ms=90000),
            Chapter(title="Episode intro", start_ms=0),
            Chapter(title="Middle", start_ms=45000),
        ]
        snapped, _ = snap_chapters(chapters, srt_end_ms=600000, max_chapters=10, first_chapter_title="Episode intro")
        assert [c.start_ms for c in snapped] == [0, 45000, 90000]


class TestSnapChaptersOutOfRange:
    def test_drops_chapter_beyond_srt_end(self):
        chapters = [
            Chapter(title="Episode intro", start_ms=0),
            Chapter(title="In range", start_ms=60000),
            Chapter(title="Beyond end", start_ms=999999999),
        ]
        snapped, notes = snap_chapters(
            chapters, srt_end_ms=600000, max_chapters=10, first_chapter_title="Episode intro"
        )
        assert [c.title for c in snapped] == ["Episode intro", "In range"]
        assert any("Beyond end" in note for note in notes)

    def test_start_equal_to_srt_end_is_kept(self):
        chapters = [Chapter(title="Episode intro", start_ms=0), Chapter(title="Right at the end", start_ms=600000)]
        snapped, notes = snap_chapters(
            chapters, srt_end_ms=600000, max_chapters=10, first_chapter_title="Episode intro"
        )
        assert [c.title for c in snapped] == ["Episode intro", "Right at the end"]
        assert notes == []


class TestSnapChaptersOverCap:
    def test_truncates_to_max_chapters(self):
        chapters = [Chapter(title="Episode intro", start_ms=0)] + [
            Chapter(title=f"Chapter {i}", start_ms=i * 60000) for i in range(1, 6)
        ]
        snapped, notes = snap_chapters(chapters, srt_end_ms=600000, max_chapters=3, first_chapter_title="Episode intro")
        assert len(snapped) == 3
        assert [c.title for c in snapped] == ["Episode intro", "Chapter 1", "Chapter 2"]
        assert any("max_chapters" in note and "3" in note for note in notes)
        assert any("Chapter 3" in note and "Chapter 4" in note and "Chapter 5" in note for note in notes)


class TestSnapChaptersNotesAreDataDriven:
    def test_odd_max_chapters_and_title_appear_in_notes(self):
        # Odd, off-spec values (not the real config's numbers) prove notes
        # are built from the caller's arguments, not hardcoded strings.
        chapters = [Chapter(title="Episode intro", start_ms=0)] + [
            Chapter(title=f"Bit {i}", start_ms=i * 1000) for i in range(1, 4)
        ]
        snapped, notes = snap_chapters(
            chapters, srt_end_ms=600000, max_chapters=2, first_chapter_title="Cold open, everyone"
        )
        assert len(snapped) == 2
        assert snapped[0].title == "Cold open, everyone"
        assert any("max_chapters (2)" in note for note in notes)


# ---------------------------------------------------------------------------
# parse_chapter_list
# ---------------------------------------------------------------------------

HAPPY_CHAPTERS_BLOCK = """<!-- Provenance: Generated by cardigan-v4 timestamp phase -->

Here are the chapters I identified:

```chapters
0:00 Episode intro
2:30 Sports betting debate begins
8:15 Legislative response
```

Let me know if you'd like adjustments.
"""


class TestParseChapterListHappyPath:
    def test_parses_all_lines(self):
        chapters = parse_chapter_list(HAPPY_CHAPTERS_BLOCK)
        assert chapters == [
            Chapter(title="Episode intro", start_ms=0),
            Chapter(title="Sports betting debate begins", start_ms=150000),
            Chapter(title="Legislative response", start_ms=495000),
        ]

    def test_hmmss_line_parses(self):
        block = "```chapters\n0:00 Episode intro\n1:05:30 Closing thoughts\n```"
        chapters = parse_chapter_list(block)
        assert chapters[1] == Chapter(title="Closing thoughts", start_ms=3930000)


class TestParseChapterListAbsentFence:
    def test_no_fence_returns_none(self):
        assert parse_chapter_list("Just some prose, no fenced block here.") is None

    def test_empty_string_returns_none(self):
        assert parse_chapter_list("") is None

    def test_wrong_language_fence_returns_none(self):
        assert parse_chapter_list("```python\n0:00 Episode intro\n```") is None


class TestParseChapterListGarbageLines:
    def test_garbage_lines_skipped_valid_lines_kept(self):
        block = "```chapters\nnot a valid line\n0:00 Episode intro\nalso garbage\n2:30 Real chapter\n```"
        chapters = parse_chapter_list(block)
        assert chapters == [
            Chapter(title="Episode intro", start_ms=0),
            Chapter(title="Real chapter", start_ms=150000),
        ]

    def test_blank_lines_skipped(self):
        block = "```chapters\n0:00 Episode intro\n\n\n2:30 Next\n```"
        chapters = parse_chapter_list(block)
        assert len(chapters) == 2

    def test_all_garbage_returns_none(self):
        block = "```chapters\nnope\nstill nope\n```"
        assert parse_chapter_list(block) is None

    def test_out_of_range_timecode_line_skipped(self):
        block = "```chapters\n0:00 Episode intro\n1:75 Bad seconds\n2:30 Good one\n```"
        chapters = parse_chapter_list(block)
        assert [c.title for c in chapters] == ["Episode intro", "Good one"]


class TestParseChapterListProseTolerance:
    def test_prose_before_and_after_fence_tolerated(self):
        block = (
            "Some intro text about the episode.\n\n"
            "```chapters\n0:00 Episode intro\n2:30 Middle\n```\n\n"
            "Some trailing commentary."
        )
        chapters = parse_chapter_list(block)
        assert len(chapters) == 2


# ---------------------------------------------------------------------------
# Round-trip: parse -> snap -> emit -> byte-exact golden string
# ---------------------------------------------------------------------------


def _timestamp_rules(
    first_chapter_title: str = "Cold open",
    constraints: dict | None = None,
) -> StyleRules:
    raw = {
        "meta": {"version": 1},
        "phases": {
            "timestamp": {
                "chapter_max_by_duration": [{"lt": None, "max": 10}],
                "first_chapter": {"time": "0:00", "title": first_chapter_title, "tier": "enforce"},
                "chapter_name": {"case": "sentence", "words": {"min": 2, "max": 6}, "tier": "flag"},
                "formats": {
                    "media_manager": {"start": "H:MM:SS.000", "end": "H:MM:SS.999", "tier": "enforce"},
                    "youtube": {"style": "M:SS", "tier": "enforce"},
                },
                "constraints": (
                    {"no_gaps": True, "chronological": True, "final_end_equals_srt_end": True}
                    if constraints is None
                    else constraints
                ),
            }
        },
    }
    return StyleRules(raw=raw)


class TestRoundTripGoldenString:
    def test_parse_snap_emit_matches_golden_string_with_odd_config(self):
        # No project_name passed -- proves task 4b's new keyword-only
        # parameter is opt-in and this golden string (predating task 4b) is
        # unaffected when the caller doesn't supply it.
        raw_output = HAPPY_CHAPTERS_BLOCK
        rules = _timestamp_rules(first_chapter_title="Cold open")

        chapters = parse_chapter_list(raw_output)
        assert chapters is not None
        srt_end_ms = 600000
        snapped, notes = snap_chapters(
            chapters, srt_end_ms=srt_end_ms, max_chapters=rules.chapter_max(srt_end_ms / 60000), first_chapter_title="Cold open"
        )
        report = emit_timestamp_report(snapped, srt_end_ms=srt_end_ms, rules=rules)

        expected = (
            "# Timestamp Report\n\n"
            "**Duration:** 10:00\n\n"
            "---\n\n"
            "## Media Manager Format\n\n"
            "Copy-paste this table into PBS Media Manager chapter fields:\n\n"
            "| Title | Start Time | End Time |\n"
            "|-------|------------|----------|\n"
            "| Cold open | 0:00:00.000 | 0:02:29.999 |\n"
            "| Sports betting debate begins | 0:02:30.000 | 0:08:14.999 |\n"
            "| Legislative response | 0:08:15.000 | 0:10:00.000 |\n\n"
            "---\n\n"
            "## YouTube Format\n\n"
            "Copy-paste these timestamps directly into your YouTube description:\n\n"
            "0:00 Cold open\n"
            "2:30 Sports betting debate begins\n"
            "8:15 Legislative response\n\n"
            "---\n\n"
            "## Notes\n\n"
            "- No gaps between chapters -- each ends exactly where the next begins.\n"
            "- Chapters are listed in chronological order.\n"
            "- Final chapter end time matches the last SRT timestamp.\n"
        )
        assert report == expected
        # The model's own first-chapter title ("Episode intro") was forced
        # to the odd configured title, so exactly one note is produced.
        assert len(notes) == 1
        assert "Cold open" in notes[0]

    def test_parse_snap_emit_matches_golden_string_with_project_name(self):
        """Same fixture as above, but with project_name supplied -- proves
        the **Project:** line lands exactly where prompts/timestamp.md's
        template puts it: directly above **Duration:**, no blank line
        between them (task 4b)."""
        raw_output = HAPPY_CHAPTERS_BLOCK
        rules = _timestamp_rules(first_chapter_title="Cold open")

        chapters = parse_chapter_list(raw_output)
        assert chapters is not None
        srt_end_ms = 600000
        snapped, _ = snap_chapters(
            chapters, srt_end_ms=srt_end_ms, max_chapters=rules.chapter_max(srt_end_ms / 60000), first_chapter_title="Cold open"
        )
        report = emit_timestamp_report(
            snapped, srt_end_ms=srt_end_ms, rules=rules, project_name="Sports Betting Debate"
        )

        expected = (
            "# Timestamp Report\n\n"
            "**Project:** Sports Betting Debate\n"
            "**Duration:** 10:00\n\n"
            "---\n\n"
            "## Media Manager Format\n\n"
            "Copy-paste this table into PBS Media Manager chapter fields:\n\n"
            "| Title | Start Time | End Time |\n"
            "|-------|------------|----------|\n"
            "| Cold open | 0:00:00.000 | 0:02:29.999 |\n"
            "| Sports betting debate begins | 0:02:30.000 | 0:08:14.999 |\n"
            "| Legislative response | 0:08:15.000 | 0:10:00.000 |\n\n"
            "---\n\n"
            "## YouTube Format\n\n"
            "Copy-paste these timestamps directly into your YouTube description:\n\n"
            "0:00 Cold open\n"
            "2:30 Sports betting debate begins\n"
            "8:15 Legislative response\n\n"
            "---\n\n"
            "## Notes\n\n"
            "- No gaps between chapters -- each ends exactly where the next begins.\n"
            "- Chapters are listed in chronological order.\n"
            "- Final chapter end time matches the last SRT timestamp.\n"
        )
        assert report == expected


class TestEmitTimestampReportProjectLine:
    """Focused unit coverage for the project_name keyword-only parameter
    added in task 4b, independent of the round-trip golden strings above."""

    def test_project_line_included_when_provided(self):
        chapters = [Chapter(title="Cold open", start_ms=0)]
        rules = _timestamp_rules()
        report = emit_timestamp_report(chapters, srt_end_ms=60000, rules=rules, project_name="Here & Now")
        assert "**Project:** Here & Now\n**Duration:**" in report

    def test_project_line_omitted_when_none(self):
        chapters = [Chapter(title="Cold open", start_ms=0)]
        rules = _timestamp_rules()
        report = emit_timestamp_report(chapters, srt_end_ms=60000, rules=rules, project_name=None)
        assert "**Project:**" not in report
        assert report.startswith("# Timestamp Report\n\n**Duration:**")

    def test_project_line_omitted_when_empty_string(self):
        chapters = [Chapter(title="Cold open", start_ms=0)]
        rules = _timestamp_rules()
        report = emit_timestamp_report(chapters, srt_end_ms=60000, rules=rules, project_name="")
        assert "**Project:**" not in report

    def test_project_name_default_matches_call_without_the_kwarg_at_all(self):
        chapters = [Chapter(title="Cold open", start_ms=0)]
        rules = _timestamp_rules()
        with_default = emit_timestamp_report(chapters, srt_end_ms=60000, rules=rules)
        with_explicit_none = emit_timestamp_report(chapters, srt_end_ms=60000, rules=rules, project_name=None)
        assert with_default == with_explicit_none

    def test_notes_section_omits_disabled_constraints(self):
        # Proves the Notes section is rendered from rules.constraints, not a
        # fixed string -- disabling a constraint removes its bullet.
        chapters = [Chapter(title="Cold open", start_ms=0)]
        rules = _timestamp_rules(constraints={"no_gaps": False, "chronological": True})
        report = emit_timestamp_report(chapters, srt_end_ms=60000, rules=rules)
        assert "No gaps between chapters" not in report
        assert "Chapters are listed in chronological order." in report

    def test_no_enabled_constraints_falls_back_to_generic_note(self):
        chapters = [Chapter(title="Cold open", start_ms=0)]
        rules = _timestamp_rules(constraints={})
        report = emit_timestamp_report(chapters, srt_end_ms=60000, rules=rules)
        assert "Timestamps derived from SRT timecodes." in report

    def test_unrecognized_constraint_key_falls_back_to_humanized_label(self):
        chapters = [Chapter(title="Cold open", start_ms=0)]
        rules = _timestamp_rules(constraints={"some_future_constraint": True})
        report = emit_timestamp_report(chapters, srt_end_ms=60000, rules=rules)
        assert "some future constraint" in report
