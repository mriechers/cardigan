"""Tests for seam-gap detection in chunked formatter output.

The global word-ratio completeness check (see test_completeness.py) only
catches catastrophic truncation. A chunked formatter can silently drop a
*localized* span of dialogue at a chunk seam (observed on job 12 / 6POL0115:
~96s lost between chunk 0 and chunk 1, ~5% of the transcript, coverage ≈ 95%).

These tests cover a content-anchoring detector that finds a contiguous run of
source captions whose distinctive tokens are absent from the formatter output —
the signature of a dropped section — while tolerating the light paraphrase /
filler-removal the formatter legitimately performs.
"""

from api.services.seam_coverage import (
    DroppedSpan,
    SeamCoverageResult,
    find_dropped_spans,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _srt_block(index: int, text: str) -> str:
    """One SRT block; index also drives the (monotonic) timecode minute:second."""
    mm = index // 60
    ss = index % 60
    return f"{index}\n00:{mm:02d}:{ss:02d},000 --> 00:{mm:02d}:{ss:02d},900\n{text}\n\n"


def _srt(texts: list[str]) -> str:
    return "".join(_srt_block(i + 1, t) for i, t in enumerate(texts))


# A set of caption texts rich in distinctive (non-stopword, >=4 char) tokens so
# that a dropped caption's tokens are genuinely absent from the output.
CAPTIONS = [
    "Democratic Socialists swept three primaries in Manhattan",
    "Mayor Mamdani endorsed the insurgent challengers",
    "Wisconsin Democrats watching Francesca Hong closely",
    "Emily Berge championed Medicare expansion in Platteville",
    "Rebecca Cooke favored incremental Affordable healthcare tactics",
    "Republicans secretly prefer Hong facing Tiffany",
    "Conservative strategists floated crossover meddling schemes",
    "Tiffany campaign circulated favorable polling internally",
    "Missy Hughes abruptly suspended her gubernatorial bid",
    "Hughes immediately endorsed Lieutenant Rodriguez afterward",
    "Western counties drifted rightward despite Hughes background",
    "Closing remarks thanked Schultz returning fortnight later",
]


def _output_from(indices: list[int]) -> str:
    """Build a formatter-style document containing only the given caption indices."""
    body = "\n\n".join(f"**Speaker:**\n{CAPTIONS[i]}." for i in indices)
    return (
        "<!-- model: test -->\n# Formatted Transcript\n**Project:** Test\n---\n\n"
        f"{body}\n\n**Status:** ready_for_editing"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_complete_output_has_no_gap():
    """Every source caption represented in the output → no gap."""
    src = _srt(CAPTIONS)
    out = _output_from(list(range(len(CAPTIONS))))

    result = find_dropped_spans(src, out, is_srt=True)

    assert isinstance(result, SeamCoverageResult)
    assert result.has_gap is False
    assert result.dropped_spans == []


def test_dropped_contiguous_block_is_detected():
    """A contiguous block (captions 6-9, the chunk-seam analogue) omitted from
    the output → one dropped span flagged with that block's timecodes."""
    src = _srt(CAPTIONS)
    kept = [0, 1, 2, 3, 4, 5, 10, 11]  # drop indices 6,7,8,9 (4 captions)
    out = _output_from(kept)

    result = find_dropped_spans(src, out, is_srt=True, min_run=4)

    assert result.has_gap is True
    assert len(result.dropped_spans) == 1
    span = result.dropped_spans[0]
    assert isinstance(span, DroppedSpan)
    assert span.caption_count == 4
    # Captions are 1-indexed in SRT; dropped indices 6,7,8,9 → blocks 7..10
    assert span.start_timecode == "00:00:07,000"
    assert span.end_timecode == "00:00:10,900"


def test_light_reflow_does_not_false_positive():
    """The formatter reflows captions — normalizes case/punctuation and joins
    lines — but preserves word order. That must NOT be flagged as a gap.

    (Word-order reversal is deliberately NOT tested: the formatter never reorders
    words within a turn, and a trigram detector correctly cannot survive that —
    testing it would assert a behavior the real system doesn't exhibit.)"""
    src = _srt(CAPTIONS)
    # Lowercase, strip terminal punctuation, join into one prose blob per caption.
    reflowed = [c.lower().replace(",", "") for c in CAPTIONS]
    out = (
        "<!-- model: test -->\n# Formatted Transcript\n**Project:** Test\n---\n\n"
        + "\n\n".join(f"**Speaker:** {p}." for p in reflowed)
        + "\n\n**Status:** ready_for_editing"
    )

    result = find_dropped_spans(src, out, is_srt=True, min_run=4)

    assert result.has_gap is False, [s.sample_text for s in result.dropped_spans]


def test_isolated_short_drop_below_min_run_is_ignored():
    """A 2-caption gap is below min_run and treated as noise, not a dropped section."""
    src = _srt(CAPTIONS)
    kept = [i for i in range(len(CAPTIONS)) if i not in (6, 7)]  # drop only 2
    out = _output_from(kept)

    result = find_dropped_spans(src, out, is_srt=True, min_run=4)

    assert result.has_gap is False


def test_recurring_topic_words_do_not_mask_a_drop():
    """Regression guard for the core design decision (trigrams, not bag-of-words).

    The dropped captions share individual topic words (Hong, Tiffany, primary,
    Republicans) with retained captions elsewhere — so a single-word check would
    find those words 'present' and miss the gap (the bug the first implementation
    actually had on real data). Trigrams are specific enough to still catch it."""
    captions = [
        "Republicans keep insisting every Democrat embraces socialism loudly",  # kept
        "Hong leads primary polling among Wisconsin Democrats currently",  # kept
        "Republicans privately want Hong winning primary against Tiffany",  # DROP
        "Tiffany strategists encouraged crossover primary ballots quietly",  # DROP
        "Republicans circulated primary polling showing Tiffany defeating Hong",  # DROP
        "Crossover primary meddling could doom Democrats badly",  # DROP
        "Hong rejected these primary attacks during Madison rally",  # kept
        "Tiffany declined commenting about primary crossover speculation",  # kept
    ]
    src = _srt(captions)
    kept = [0, 1, 6, 7]  # drop the four middle captions (a contiguous seam block)
    body = "\n\n".join(f"**Speaker:** {captions[i]}." for i in kept)
    out = (
        "<!-- model: test -->\n# Formatted Transcript\n**Project:** Test\n---\n\n"
        f"{body}\n\n**Status:** ready_for_editing"
    )

    result = find_dropped_spans(src, out, is_srt=True, min_run=4)

    assert result.has_gap is True
    assert result.dropped_spans[0].caption_count == 4


def test_non_srt_and_empty_input_are_graceful():
    """Non-SRT or empty source returns a no-gap result rather than raising."""
    assert find_dropped_spans("", "", is_srt=True).has_gap is False
    plain = find_dropped_spans("just some prose", "output", is_srt=False)
    assert plain.has_gap is False
