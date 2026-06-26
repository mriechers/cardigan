"""Tests for interior-`>>` caption splitting.

Live-caption SRTs mark speaker changes with `>>`, and that marker sometimes
falls *inside* a caption (e.g. "She's the only. >> One, right?"). When it does,
the formatter LLM mis-segments the turn — on job 12 this produced a turn-order
inversion (an answer placed before its question) and speaker misattribution.

split_interior_speaker_changes() deterministically splits any caption on an
interior `>>` into separate captions (timecodes apportioned by length), so the
formatter sees at most one speaker turn per caption. A leading-only `>>` is a
normal turn marker and is left intact. No dialogue words are added or removed.
"""

from api.services.speaker_segmentation import split_interior_speaker_changes
from api.services.utils import parse_srt


def _block(idx: int, start: str, end: str, text: str) -> str:
    return f"{idx}\n{start} --> {end}\n{text}\n\n"


def test_interior_marker_splits_into_two_captions():
    srt = _block(1, "00:00:05,000", "00:00:06,000", "She's the only. >> One, right?")

    out = parse_srt(split_interior_speaker_changes(srt))

    assert len(out) == 2
    assert out[0].text == "She's the only."
    assert out[1].text == ">> One, right?"
    # Timecodes stay within the original span, monotonic and non-overlapping.
    assert out[0].start_ms == 5000
    assert out[1].end_ms == 6000
    assert out[0].end_ms == out[1].start_ms
    assert out[0].start_ms < out[0].end_ms < out[1].end_ms


def test_leading_only_marker_is_unchanged():
    srt = _block(1, "00:00:01,000", "00:00:03,000", ">> Not that I know of, not that I know of.")

    out = parse_srt(split_interior_speaker_changes(srt))

    assert len(out) == 1
    assert out[0].text == ">> Not that I know of, not that I know of."
    assert out[0].start_ms == 1000 and out[0].end_ms == 3000


def test_caption_without_marker_is_unchanged():
    srt = _block(1, "00:00:01,000", "00:00:02,000", "Just an ordinary caption line.")

    out = parse_srt(split_interior_speaker_changes(srt))

    assert len(out) == 1
    assert out[0].text == "Just an ordinary caption line."


def test_multiple_interior_markers_split_into_three():
    srt = _block(1, "00:00:00,000", "00:00:09,000", "First part. >> Second part. >> Third part.")

    out = parse_srt(split_interior_speaker_changes(srt))

    assert [c.text for c in out] == ["First part.", ">> Second part.", ">> Third part."]
    assert out[0].start_ms == 0 and out[-1].end_ms == 9000
    # Monotonic, non-overlapping boundaries across all three.
    for a, b in zip(out, out[1:]):
        assert a.end_ms == b.start_ms
        assert a.start_ms < a.end_ms


def test_reindexes_sequentially_across_file():
    srt = (
        _block(1, "00:00:01,000", "00:00:02,000", "Plain one.")
        + _block(2, "00:00:02,000", "00:00:04,000", "Alpha here. >> Beta there.")
        + _block(3, "00:00:04,000", "00:00:05,000", "Plain three.")
    )

    out = parse_srt(split_interior_speaker_changes(srt))

    assert [c.index for c in out] == [1, 2, 3, 4]
    assert [c.text for c in out] == ["Plain one.", "Alpha here.", ">> Beta there.", "Plain three."]


def test_no_dialogue_words_are_added_or_dropped():
    """The transform must be word-preserving (so seam/completeness checks are
    unaffected) — the only added token is the `>>` marker, which is not a word."""
    srt = _block(1, "00:00:00,000", "00:00:03,000", "We agree completely. >> I disagree strongly.") + _block(
        2, "00:00:03,000", "00:00:05,000", "No interior marker present here."
    )

    before = parse_srt(srt)
    after = parse_srt(split_interior_speaker_changes(srt))

    def words(caps):
        return sorted(w for c in caps for w in c.text.replace(">>", " ").split())

    assert words(before) == words(after)
