"""Interior speaker-change splitting for live-caption SRTs.

Live/real-time caption SRTs mark speaker changes with ``>>``. That marker
sometimes lands *inside* a caption rather than at its start (e.g.
``"She's the only. >> One, right?"``). When it does, the formatter LLM tends to
mis-segment the turn — on job 12 (6POL0115) this produced a turn-order inversion
(an answer rendered before the question it answers) and speaker misattribution.

``split_interior_speaker_changes`` rewrites the SRT so every caption holds at
most one speaker turn: any caption with an interior ``>>`` is split into separate
captions at each marker, with the caption's timespan apportioned by segment
length. A leading-only ``>>`` (the normal turn marker) is left untouched. The
transform adds and drops no dialogue words, so downstream word/coverage checks
(completeness, seam) are unaffected.
"""

import logging
from typing import List

from api.services.utils import SRTCaption, generate_srt, parse_srt

logger = logging.getLogger(__name__)

_MARKER = ">>"


def _segments(text: str) -> List[str]:
    """Split caption text on ``>>``; re-attach the marker to interior segments.

    Returns a single-element list when there is no interior marker (text with no
    marker, or only a leading one), so the caller leaves the caption untouched.
    """
    parts = text.split(_MARKER)
    segments: List[str] = []
    # parts[0] is the text before the first marker; empty when text leads with >>.
    if parts[0].strip():
        segments.append(parts[0].strip())
    for p in parts[1:]:
        if p.strip():
            segments.append(f"{_MARKER} {p.strip()}")
    return segments or [text.strip()]


def _apportion(start_ms: int, end_ms: int, segments: List[str]) -> List[tuple]:
    """Slice [start_ms, end_ms] into one sub-span per segment, by char length.

    Boundaries are monotonic and non-overlapping; the first sub-span starts at
    start_ms and the last ends at end_ms.
    """
    total_chars = sum(len(s) for s in segments) or 1
    spans = []
    cursor = start_ms
    span_total = end_ms - start_ms
    for k, seg in enumerate(segments):
        if k == len(segments) - 1:
            seg_end = end_ms
        else:
            seg_end = cursor + round(span_total * len(seg) / total_chars)
            # Guarantee strictly increasing boundaries even for tiny spans.
            seg_end = max(seg_end, cursor + 1)
            seg_end = min(seg_end, end_ms - (len(segments) - 1 - k))
        spans.append((cursor, seg_end))
        cursor = seg_end
    return spans


def split_interior_speaker_changes(srt_content: str) -> str:
    """Return SRT content with interior ``>>`` markers split into own captions.

    No-ops gracefully (returns the input) when the content has no captions.
    """
    captions = parse_srt(srt_content)
    if not captions:
        return srt_content

    rebuilt: List[SRTCaption] = []
    next_index = 1
    split_count = 0

    for cap in captions:
        segments = _segments(cap.text)
        if len(segments) <= 1:
            rebuilt.append(SRTCaption(index=next_index, start_ms=cap.start_ms, end_ms=cap.end_ms, text=cap.text))
            next_index += 1
            continue

        split_count += 1
        for (seg_start, seg_end), seg_text in zip(_apportion(cap.start_ms, cap.end_ms, segments), segments):
            rebuilt.append(SRTCaption(index=next_index, start_ms=seg_start, end_ms=seg_end, text=seg_text))
            next_index += 1

    if split_count:
        logger.info(
            "Split interior speaker-change markers",
            extra={"captions_split": split_count, "new_caption_count": len(rebuilt)},
        )

    return generate_srt(rebuilt)
