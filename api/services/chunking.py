"""Transcript chunking for parallel formatter processing.

Splits long transcripts into chunks that can be processed concurrently,
then merges the formatted outputs back into a single document.

Short transcripts (<threshold) bypass chunking entirely.
"""

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from api.services.utils import SRTCaption, generate_srt, parse_srt

logger = logging.getLogger(__name__)

# Default config values (overridden by llm-config.json routing.chunking)
DEFAULT_CHUNKING_CONFIG = {
    "enabled": True,
    "threshold_words": 3000,
    "target_chunk_words": 1500,
    "overlap_captions": 5,
    "max_parallel": 3,
}


@dataclass
class TranscriptChunk:
    """A portion of a transcript for parallel processing."""

    index: int
    content: str  # Raw SRT or plain text for this chunk
    start_timecode: str  # Display timecode for logging
    end_timecode: str
    word_count: int
    overlap_prefix: str = ""  # Context from previous chunk's tail


def _count_dialogue_words_srt(captions: List[SRTCaption]) -> int:
    """Count dialogue words across SRT captions (text only, no timecodes)."""
    return sum(len(c.text.split()) for c in captions)


def _split_srt(
    content: str,
    target_chunk_words: int,
    overlap_captions: int,
) -> Optional[List[TranscriptChunk]]:
    """Split SRT content into chunks at sentence boundaries.

    Walks captions accumulating word count. At the target, looks ahead
    up to 10 captions for sentence-ending punctuation to find a natural
    break point.
    """
    captions = parse_srt(content)
    if not captions:
        return None

    total_words = _count_dialogue_words_srt(captions)
    if total_words < target_chunk_words * 1.5:
        # Would produce only 1 chunk
        return None

    chunks: List[TranscriptChunk] = []
    chunk_start_idx = 0
    accumulated_words = 0
    LOOKAHEAD = 10

    i = 0
    while i < len(captions):
        caption_words = len(captions[i].text.split())
        accumulated_words += caption_words

        if accumulated_words >= target_chunk_words and i < len(captions) - 1:
            # Look ahead for sentence-ending punctuation
            break_idx = i
            for j in range(i, min(i + LOOKAHEAD, len(captions))):
                text = captions[j].text.strip()
                if text and text[-1] in ".?!":
                    break_idx = j
                    break
            else:
                # No sentence boundary found in lookahead, break at current
                break_idx = i

            # Build this chunk's captions
            chunk_captions = captions[chunk_start_idx : break_idx + 1]

            # Build overlap prefix from previous chunk's tail
            overlap = ""
            if chunks and overlap_captions > 0:
                overlap_start = max(chunk_start_idx - overlap_captions, 0)
                overlap_caps = captions[overlap_start:chunk_start_idx]
                if overlap_caps:
                    overlap = generate_srt(overlap_caps)

            chunk = TranscriptChunk(
                index=len(chunks),
                content=generate_srt(chunk_captions),
                start_timecode=chunk_captions[0].start_timecode,
                end_timecode=chunk_captions[-1].end_timecode,
                word_count=_count_dialogue_words_srt(chunk_captions),
                overlap_prefix=overlap,
            )
            chunks.append(chunk)

            chunk_start_idx = break_idx + 1
            accumulated_words = 0
            i = break_idx + 1
            continue

        i += 1

    # Don't forget the final chunk
    if chunk_start_idx < len(captions):
        remaining = captions[chunk_start_idx:]
        overlap = ""
        if chunks and overlap_captions > 0:
            overlap_start = max(chunk_start_idx - overlap_captions, 0)
            overlap_caps = captions[overlap_start:chunk_start_idx]
            if overlap_caps:
                overlap = generate_srt(overlap_caps)

        chunk = TranscriptChunk(
            index=len(chunks),
            content=generate_srt(remaining),
            start_timecode=remaining[0].start_timecode,
            end_timecode=remaining[-1].end_timecode,
            word_count=_count_dialogue_words_srt(remaining),
            overlap_prefix=overlap,
        )
        chunks.append(chunk)

    if len(chunks) <= 1:
        return None

    return chunks


def _split_plain_text(
    content: str,
    target_chunk_words: int,
) -> Optional[List[TranscriptChunk]]:
    """Split plain text at paragraph boundaries.

    Last 2 paragraphs of each chunk become overlap for the next.
    """
    paragraphs = re.split(r"\n\s*\n", content.strip())
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    if not paragraphs:
        return None

    total_words = sum(len(p.split()) for p in paragraphs)
    if total_words < target_chunk_words * 1.5:
        return None

    chunks: List[TranscriptChunk] = []
    current_paragraphs: List[str] = []
    current_words = 0

    for para in paragraphs:
        para_words = len(para.split())
        current_paragraphs.append(para)
        current_words += para_words

        if current_words >= target_chunk_words:
            chunk_text = "\n\n".join(current_paragraphs)

            # Build overlap from last 2 paragraphs
            overlap = ""
            if chunks:
                # Get last 2 paragraphs from previous chunk
                prev_text = chunks[-1].content
                prev_paras = re.split(r"\n\s*\n", prev_text.strip())
                overlap_paras = prev_paras[-2:] if len(prev_paras) >= 2 else prev_paras
                overlap = "\n\n".join(overlap_paras)

            chunk = TranscriptChunk(
                index=len(chunks),
                content=chunk_text,
                start_timecode="",
                end_timecode="",
                word_count=current_words,
                overlap_prefix=overlap,
            )
            chunks.append(chunk)

            current_paragraphs = []
            current_words = 0

    # Final chunk
    if current_paragraphs:
        chunk_text = "\n\n".join(current_paragraphs)
        overlap = ""
        if chunks:
            prev_text = chunks[-1].content
            prev_paras = re.split(r"\n\s*\n", prev_text.strip())
            overlap_paras = prev_paras[-2:] if len(prev_paras) >= 2 else prev_paras
            overlap = "\n\n".join(overlap_paras)

        chunk = TranscriptChunk(
            index=len(chunks),
            content=chunk_text,
            start_timecode="",
            end_timecode="",
            word_count=current_words,
            overlap_prefix=overlap,
        )
        chunks.append(chunk)

    if len(chunks) <= 1:
        return None

    return chunks


def split_transcript(
    content: str,
    is_srt: bool,
    config: Optional[Dict] = None,
) -> Optional[List[TranscriptChunk]]:
    """Split a transcript into chunks for parallel processing.

    Returns None if the transcript is below threshold or only produces
    one chunk — caller should use the normal single-call path.

    Args:
        content: Raw transcript content (SRT or plain text)
        is_srt: Whether the content is SRT format
        config: Chunking config from llm-config.json routing.chunking

    Returns:
        List of TranscriptChunk if chunking applies, None otherwise
    """
    cfg = {**DEFAULT_CHUNKING_CONFIG, **(config or {})}

    if not cfg.get("enabled", True):
        return None

    # Count words (dialogue only for SRT)
    if is_srt:
        captions = parse_srt(content)
        word_count = _count_dialogue_words_srt(captions) if captions else 0
    else:
        word_count = len(content.split())

    threshold = cfg["threshold_words"]
    if word_count < threshold:
        logger.debug(
            "Transcript below chunking threshold",
            extra={"word_count": word_count, "threshold": threshold},
        )
        return None

    target = cfg["target_chunk_words"]
    overlap = cfg.get("overlap_captions", 5)

    if is_srt:
        chunks = _split_srt(content, target, overlap)
    else:
        chunks = _split_plain_text(content, target)

    if chunks:
        logger.info(
            "Transcript split into chunks",
            extra={
                "chunk_count": len(chunks),
                "word_count": word_count,
                "target_per_chunk": target,
            },
        )

    return chunks


def merge_formatter_chunks(chunks: List[str]) -> str:
    """Merge formatted chunk outputs into a single document.

    1. Keep header (before first ---) from chunk 0 only
    2. Strip headers from chunks 1+
    3. Strip Status line from all but last chunk
    4. Collect review notes into one block at top
    5. Deduplicate overlap at chunk seams
    6. Concatenate

    Args:
        chunks: List of formatted output strings, one per chunk

    Returns:
        Merged formatter output
    """
    if not chunks:
        return ""
    if len(chunks) == 1:
        return chunks[0]

    # Extract all review notes
    review_notes: List[str] = []
    review_pattern = re.compile(r"<!--\s*REVIEW NOTES\s*-->.*?(?=<!--|$)", re.DOTALL | re.IGNORECASE)

    # Process each chunk
    header = ""
    bodies: List[str] = []
    status_line = ""

    for i, chunk in enumerate(chunks):
        # Strip provenance HTML comment from top (<!-- model: ... -->)
        chunk = re.sub(r"^<!--\s*model:.*?-->\s*\n?", "", chunk.strip())

        # Strip LLM-generated model/creator attribution lines (appear at end of chunk responses)
        chunk = re.sub(
            r"^\*\*(?:Model|Creator|Agent):\*\*.*\n?",
            "",
            chunk,
            flags=re.MULTILINE,
        )
        # Clean up orphaned --- separators left after attribution removal
        chunk = re.sub(r"\n---+\s*\n*$", "", chunk.strip())

        # Extract review notes from this chunk
        notes = review_pattern.findall(chunk)
        for note in notes:
            note = note.strip()
            if note and note not in review_notes:
                review_notes.append(note)
        # Remove review notes from chunk body
        chunk = review_pattern.sub("", chunk).strip()

        if i == 0:
            # First chunk: extract header (everything before first ---)
            parts = re.split(r"^---+\s*$", chunk, maxsplit=1, flags=re.MULTILINE)
            if len(parts) > 1:
                header = parts[0].strip()
                body = parts[1].strip()
            else:
                body = chunk
        else:
            # Subsequent chunks: strip any generated header
            body = chunk
            # Remove "# Formatted Transcript" heading
            body = re.sub(r"^#\s+Formatted Transcript\s*\n?", "", body, flags=re.MULTILINE)
            # Remove metadata lines (Project:, Program:, Duration:, Date:)
            body = re.sub(
                r"^\*\*(?:Project|Program|Duration|Date|Air Date|Media ID):\*\*.*\n?",
                "",
                body,
                flags=re.MULTILINE,
            )
            # Remove --- separator at top if present after header removal
            body = re.sub(r"^---+\s*\n?", "", body.strip(), flags=re.MULTILINE)
            body = body.strip()

        # Extract and save Status line from last chunk only
        status_match = re.search(r"^\*\*Status:\*\*\s+.*$", body, flags=re.MULTILINE)
        if status_match:
            if i == len(chunks) - 1:
                status_line = status_match.group(0)
            # Remove status from all chunks
            body = re.sub(r"^\*\*Status:\*\*\s+.*$", "", body, flags=re.MULTILINE).strip()

        bodies.append(body)

    # Deduplicate overlap at seams
    for i in range(len(bodies) - 1):
        bodies[i + 1] = _trim_overlap(bodies[i], bodies[i + 1])

    # Build final document
    parts = []

    if header:
        parts.append(header)

    # Add consolidated review notes
    if review_notes:
        notes_block = "<!-- REVIEW NOTES -->\n" + "\n".join(review_notes) + "\n<!-- /REVIEW NOTES -->"
        parts.append(notes_block)

    if header or review_notes:
        parts.append("---")

    parts.append("\n\n".join(b for b in bodies if b))

    if status_line:
        parts.append(status_line)

    return "\n\n".join(parts)


def _trim_overlap(prev_body: str, next_body: str, window: int = 100) -> str:
    """Remove duplicate text at the seam between two chunks.

    Compares the last ~window words of prev with the first ~window
    words of next. If >50% overlap, trims the duplicate from next.
    """
    prev_words = prev_body.split()
    next_words = next_body.split()

    if len(prev_words) < 10 or len(next_words) < 10:
        return next_body

    prev_tail = prev_words[-window:]
    next_head = next_words[:window]

    # Find the longest matching suffix of prev_tail that starts next_head
    best_match_len = 0
    for start in range(len(prev_tail)):
        candidate = prev_tail[start:]
        candidate_len = len(candidate)
        if candidate_len < 5:
            break
        # Check if next_head starts with this candidate
        match_len = 0
        for k in range(min(candidate_len, len(next_head))):
            if candidate[k].lower() == next_head[k].lower():
                match_len += 1
            else:
                break
        if match_len > best_match_len and match_len >= 5:
            best_match_len = match_len

    # If >50% of the window overlaps, trim
    if best_match_len > 0 and best_match_len / min(window, len(next_head)) > 0.5:
        logger.info(
            "Trimming overlap at chunk seam",
            extra={"overlap_words": best_match_len},
        )
        trimmed_words = next_words[best_match_len:]
        return " ".join(trimmed_words)

    return next_body
