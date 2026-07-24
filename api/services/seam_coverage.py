"""Seam-gap detection for chunked formatter output.

The global word-ratio check in ``completeness.py`` only catches catastrophic
truncation. A chunked formatter can silently drop a *localized* span of dialogue
at a chunk seam (job 12 / 6POL0115 lost ~96s — ~5% of the transcript — so global
coverage was ~95%, well above the 0.70 ratio gate).

This module detects that failure mode by *content anchoring*: it walks the source
captions and flags any contiguous run whose word-order **trigrams** are absent
from the formatter output.

Why trigrams and not single words: topic words (Hong, Tiffany, primary,
Republicans) recur all over the transcript, so a dropped caption's individual
words still appear elsewhere in the output — a bag-of-words check misses the gap.
Word-order trigrams are specific enough that a dropped caption scores ~0 while a
retained-but-lightly-reflowed caption scores ~1.0 (measured on job 12: retained
captions 1.00, dropped captions 0.00). The comparison is against a global trigram
set, so reordering whole turns does not cause false positives.

Blocking vs. detection: trigram detection cannot distinguish a genuine drop from
a HEAVILY reconstructed caption (e.g. garbled ASR "gouess"→"guess", "yout"→"out")
— both destroy the source word order and score ~0. So a detected gap only
*blocks* (pauses the job) when corroborated by **net content loss**: the output
has fewer content words than the source. A reconstruction adds content (labels,
cleanup) so its net ratio is ≥ 1.0 → recorded but non-blocking; a real dropped
span removes content → ratio below ``DEFAULT_BLOCKING_RATIO`` → blocking.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

from api.services.completeness import count_content_words, count_source_words
from api.services.utils import parse_srt

logger = logging.getLogger(__name__)

# A caption is "missing" when fewer than this fraction of its trigrams appear in
# the output. Retained captions score ~1.0 and dropped ones ~0.0, so the exact
# value is not sensitive; 0.5 sits squarely in the empty band between them.
DEFAULT_PER_CAPTION_FLOOR = 0.5

# Only a contiguous run of at least this many missing captions counts as a
# dropped section. Below this is treated as paraphrase noise, not content loss.
DEFAULT_MIN_RUN = 4

# A detected gap only *blocks* (pauses the job) when the formatter output has
# fewer content words than the source × this ratio — corroborating that content
# was actually lost rather than reconstructed. Reconstruction of garbled captions
# keeps output ≥ source; a real dropped span pushes it below this.
DEFAULT_BLOCKING_RATIO = 0.98

# Trigram tokens are alphabetic runs of at least this length.
DEFAULT_MIN_TOKEN_LEN = 3

_NGRAM = 3
_TOKEN_RE = re.compile(r"[a-z']+")


@dataclass
class DroppedSpan:
    """A contiguous run of source captions absent from the output."""

    start_timecode: str
    end_timecode: str
    caption_count: int
    sample_text: str


@dataclass
class SeamCoverageResult:
    has_gap: bool
    dropped_spans: List[DroppedSpan] = field(default_factory=list)
    captions_checked: int = 0
    # Output content words / source content words. < 1.0 means net content loss.
    net_coverage_ratio: float = 1.0
    # has_gap AND net content loss — the signal the worker pauses on.
    blocking: bool = False

    def to_dict(self) -> dict:
        return {
            "has_gap": self.has_gap,
            "blocking": self.blocking,
            "net_coverage_ratio": round(self.net_coverage_ratio, 4),
            "captions_checked": self.captions_checked,
            "dropped_spans": [
                {
                    "start_timecode": s.start_timecode,
                    "end_timecode": s.end_timecode,
                    "caption_count": s.caption_count,
                    "sample_text": s.sample_text,
                }
                for s in self.dropped_spans
            ],
        }


def format_gap_message(result: "SeamCoverageResult") -> str:
    """Operator-facing summary of detected seam gaps (empty if none).

    Used for the job pause / validator-flag message so an editor sees exactly
    which spans of source dialogue are missing from the formatter output.
    """
    if not result.has_gap or not result.dropped_spans:
        return ""

    spans = "; ".join(
        f"{s.start_timecode}–{s.end_timecode} " f'({s.caption_count} captions, e.g. "{s.sample_text}")'
        for s in result.dropped_spans
    )
    n = len(result.dropped_spans)
    return (
        f"SEAM GAP DETECTED: Formatter output is missing {n} "
        f"section{'s' if n != 1 else ''} of source dialogue: {spans}. "
        "Likely a chunk-boundary drop — retry to escalate / re-chunk."
    )


def _tokens(text: str, min_token_len: int) -> List[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= min_token_len]


def _trigrams(tokens: List[str]) -> Set[Tuple[str, ...]]:
    return {tuple(tokens[i : i + _NGRAM]) for i in range(len(tokens) - _NGRAM + 1)}


def _output_trigrams(formatter_output: str, min_token_len: int) -> Set[Tuple[str, ...]]:
    """Trigrams present anywhere in the formatter output body.

    Strips HTML comments (provenance / review notes) and the metadata header
    before the first ``---`` so header boilerplate can't 'cover' a caption.
    """
    text = re.sub(r"<!--.*?-->", "", formatter_output, flags=re.DOTALL)
    parts = re.split(r"^---+\s*$", text, maxsplit=1, flags=re.MULTILINE)
    if len(parts) > 1:
        text = parts[1]
    return _trigrams(_tokens(text, min_token_len))


def _caption_status(
    cap_text: str,
    out_trigrams: Set[Tuple[str, ...]],
    floor: float,
    min_token_len: int,
) -> Optional[bool]:
    """True = missing, False = present, None = too short to anchor (skip)."""
    grams = _trigrams(_tokens(cap_text, min_token_len))
    if not grams:
        return None
    coverage = len(grams & out_trigrams) / len(grams)
    return coverage < floor


def _net_coverage_ratio(source_transcript: str, formatter_output: str, is_srt: bool) -> float:
    """Output content words / source content words (reusing the completeness
    tokenizers so this matches the global gate's notion of 'content')."""
    src_words = count_source_words(source_transcript, is_srt=is_srt)
    if src_words <= 0:
        return 1.0
    return count_content_words(formatter_output) / src_words


def find_dropped_spans(
    source_transcript: str,
    formatter_output: str,
    is_srt: bool = True,
    min_run: int = DEFAULT_MIN_RUN,
    per_caption_floor: float = DEFAULT_PER_CAPTION_FLOOR,
    min_token_len: int = DEFAULT_MIN_TOKEN_LEN,
    blocking_ratio: float = DEFAULT_BLOCKING_RATIO,
) -> SeamCoverageResult:
    """Detect contiguous source spans missing from the formatter output.

    Only the SRT path is supported (chunked formatting operates on SRT). Plain
    text or empty input returns a no-gap result rather than raising.

    Captions too short to form a trigram are transparent: they neither count as
    missing nor break a missing run, so a dropped block isn't split by an
    interleaved short caption.
    """
    if not is_srt or not source_transcript.strip():
        return SeamCoverageResult(has_gap=False)

    captions = parse_srt(source_transcript)
    if not captions:
        return SeamCoverageResult(has_gap=False)

    out_trigrams = _output_trigrams(formatter_output, min_token_len)
    statuses = [_caption_status(c.text, out_trigrams, per_caption_floor, min_token_len) for c in captions]

    # Walk captions, accumulating maximal blocks bounded by PRESENT (False)
    # captions. SKIP (None) captions are transparent. A block qualifies if it
    # holds at least ``min_run`` MISSING captions.
    spans: List[DroppedSpan] = []
    n = len(captions)
    i = 0
    while i < n:
        if statuses[i] is not True:
            i += 1
            continue
        # Start of a potential missing block.
        first = i
        last_missing = i
        j = i
        while j < n and statuses[j] is not False:  # extend over MISSING and SKIP
            if statuses[j] is True:
                last_missing = j
            j += 1
        missing_count = sum(1 for k in range(first, last_missing + 1) if statuses[k] is True)
        if missing_count >= min_run:
            spans.append(
                DroppedSpan(
                    start_timecode=captions[first].start_timecode,
                    end_timecode=captions[last_missing].end_timecode,
                    caption_count=missing_count,
                    sample_text=captions[first].text.strip().replace("\n", " ")[:160],
                )
            )
        i = j

    net_ratio = _net_coverage_ratio(source_transcript, formatter_output, is_srt)
    has_gap = bool(spans)
    blocking = has_gap and net_ratio < blocking_ratio

    if spans:
        logger.warning(
            "Seam gap detected in formatter output",
            extra={
                "dropped_spans": [s.start_timecode for s in spans],
                "net_coverage_ratio": round(net_ratio, 4),
                "blocking": blocking,
            },
        )

    return SeamCoverageResult(
        has_gap=has_gap,
        dropped_spans=spans,
        captions_checked=n,
        net_coverage_ratio=net_ratio,
        blocking=blocking,
    )
