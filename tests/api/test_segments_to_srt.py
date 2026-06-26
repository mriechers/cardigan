"""Tests for segments_to_srt — whisperX segments → SRT conversion."""

from api.services.utils import parse_srt, segments_to_srt


def test_basic_conversion_with_timecodes():
    segments = [
        {"start": 0.0, "end": 1.5, "text": "Hello world"},
        {"start": 1.5, "end": 3.0, "text": "Second line"},
    ]
    out = segments_to_srt(segments)
    assert "1\n00:00:00,000 --> 00:00:01,500\nHello world" in out
    assert "2\n00:00:01,500 --> 00:00:03,000\nSecond line" in out
    # Round-trips back through the parser
    captions = parse_srt(out)
    assert len(captions) == 2
    assert captions[0].text == "Hello world"
    assert captions[1].start_ms == 1500


def test_empty_text_skipped_and_renumbered():
    segments = [
        {"start": 0.0, "end": 1.0, "text": "  keep me  "},
        {"start": 1.0, "end": 2.0, "text": "   "},  # whitespace-only → dropped
        {"start": 2.0, "end": 3.0, "text": "also kept"},
    ]
    captions = parse_srt(segments_to_srt(segments))
    assert [c.text for c in captions] == ["keep me", "also kept"]
    assert [c.index for c in captions] == [1, 2]  # sequential after drop


def test_handles_missing_and_out_of_order_bounds():
    # end before start should not produce a negative duration; missing text is skipped
    segments = [
        {"start": 5.0, "end": 4.0, "text": "clamped"},
        {"start": 0.2, "end": 0.4},  # no text key → skipped
    ]
    captions = parse_srt(segments_to_srt(segments))
    assert len(captions) == 1
    assert captions[0].end_ms >= captions[0].start_ms
