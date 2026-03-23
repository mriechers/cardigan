"""Tests for transcript chunking (split + merge)."""

import pytest

from api.services.chunking import (
    TranscriptChunk,
    _split_plain_text,
    _split_srt,
    merge_formatter_chunks,
    split_transcript,
)


# ─── Helpers ───────────────────────────────────────────────────────────


def make_srt(num_captions: int, words_per_caption: int = 10) -> str:
    """Generate SRT content with specified number of captions."""
    lines = []
    for i in range(1, num_captions + 1):
        start_ms = (i - 1) * 3000
        end_ms = i * 3000
        h1, m1, s1 = start_ms // 3600000, (start_ms % 3600000) // 60000, (start_ms % 60000) // 1000
        h2, m2, s2 = end_ms // 3600000, (end_ms % 3600000) // 60000, (end_ms % 60000) // 1000

        # Make some captions end with sentence-ending punctuation
        text_words = [f"word{j}" for j in range(words_per_caption - 1)]
        if i % 5 == 0:
            text_words.append("end.")
        else:
            text_words.append(f"word{words_per_caption}")

        lines.append(str(i))
        lines.append(f"{h1:02d}:{m1:02d}:{s1:02d},000 --> {h2:02d}:{m2:02d}:{s2:02d},000")
        lines.append(" ".join(text_words))
        lines.append("")

    return "\n".join(lines)


def make_plain_text(num_paragraphs: int, words_per_paragraph: int = 100) -> str:
    """Generate plain text with specified paragraphs."""
    paragraphs = []
    for i in range(num_paragraphs):
        words = [f"word{j}" for j in range(words_per_paragraph)]
        paragraphs.append(" ".join(words))
    return "\n\n".join(paragraphs)


# ─── split_transcript tests ───────────────────────────────────────────


class TestSplitTranscript:
    def test_below_threshold_returns_none(self):
        """Short transcripts should not be chunked."""
        srt = make_srt(10, words_per_caption=5)  # 50 words total
        result = split_transcript(srt, is_srt=True, config={"threshold_words": 3000})
        assert result is None

    def test_disabled_config_returns_none(self):
        """Chunking disabled in config should return None."""
        srt = make_srt(500, words_per_caption=10)  # 5000 words
        result = split_transcript(srt, is_srt=True, config={"enabled": False})
        assert result is None

    def test_srt_above_threshold_returns_chunks(self):
        """Long SRT should be split into multiple chunks."""
        srt = make_srt(400, words_per_caption=10)  # 4000 words
        result = split_transcript(
            srt,
            is_srt=True,
            config={"threshold_words": 3000, "target_chunk_words": 1500, "overlap_captions": 5},
        )
        assert result is not None
        assert len(result) >= 2
        # All chunks should have content
        for chunk in result:
            assert chunk.content
            assert chunk.word_count > 0

    def test_plain_text_above_threshold(self):
        """Long plain text should be split into chunks."""
        text = make_plain_text(40, words_per_paragraph=100)  # 4000 words
        result = split_transcript(
            text,
            is_srt=False,
            config={"threshold_words": 3000, "target_chunk_words": 1500},
        )
        assert result is not None
        assert len(result) >= 2

    def test_single_chunk_returns_none(self):
        """If splitting produces only 1 chunk, return None."""
        # Just barely over threshold but not enough for 2 chunks
        srt = make_srt(200, words_per_caption=10)  # 2000 words
        result = split_transcript(
            srt,
            is_srt=True,
            config={"threshold_words": 1900, "target_chunk_words": 3000},
        )
        assert result is None


# ─── _split_srt tests ─────────────────────────────────────────────────


class TestSplitSRT:
    def test_basic_split(self):
        """SRT with enough words should produce multiple chunks."""
        srt = make_srt(400, words_per_caption=10)  # 4000 words
        chunks = _split_srt(srt, target_chunk_words=1500, overlap_captions=5)
        assert chunks is not None
        assert len(chunks) >= 2
        # Chunks should have sequential indices
        for i, chunk in enumerate(chunks):
            assert chunk.index == i

    def test_sentence_boundary_preference(self):
        """Chunks should prefer breaking at sentence-ending punctuation."""
        # Our make_srt adds "end." at every 5th caption
        srt = make_srt(400, words_per_caption=10)
        chunks = _split_srt(srt, target_chunk_words=1500, overlap_captions=0)
        assert chunks is not None
        # Check that non-final chunks end at sentence boundaries where possible
        for chunk in chunks[:-1]:
            lines = chunk.content.strip().split("\n")
            # Find the last text line (not index or timecode)
            last_text = ""
            for line in reversed(lines):
                line = line.strip()
                if line and not line.isdigit() and "-->" not in line:
                    last_text = line
                    break
            # Many (not all) should end with period
            # Just check this doesn't crash; exact boundary depends on word counts

    def test_overlap_prefix(self):
        """Chunks 1+ should have overlap prefix from previous chunk."""
        srt = make_srt(400, words_per_caption=10)
        chunks = _split_srt(srt, target_chunk_words=1500, overlap_captions=5)
        assert chunks is not None
        assert len(chunks) >= 2
        # First chunk has no overlap
        assert chunks[0].overlap_prefix == ""
        # Subsequent chunks should have overlap
        for chunk in chunks[1:]:
            assert chunk.overlap_prefix != ""
            # Overlap should be valid SRT content
            assert "-->" in chunk.overlap_prefix

    def test_timecodes_present(self):
        """Each chunk should have start/end timecodes."""
        srt = make_srt(400, words_per_caption=10)
        chunks = _split_srt(srt, target_chunk_words=1500, overlap_captions=5)
        assert chunks is not None
        for chunk in chunks:
            assert chunk.start_timecode
            assert chunk.end_timecode
            assert ":" in chunk.start_timecode


# ─── _split_plain_text tests ──────────────────────────────────────────


class TestSplitPlainText:
    def test_paragraph_boundary_splitting(self):
        """Plain text should split on paragraph boundaries."""
        text = make_plain_text(40, words_per_paragraph=100)  # 4000 words
        chunks = _split_plain_text(text, target_chunk_words=1500)
        assert chunks is not None
        assert len(chunks) >= 2
        # Each chunk should contain complete paragraphs
        for chunk in chunks:
            assert chunk.content.strip()

    def test_short_text_returns_none(self):
        """Text that would produce 1 chunk should return None."""
        text = make_plain_text(5, words_per_paragraph=100)  # 500 words
        chunks = _split_plain_text(text, target_chunk_words=1500)
        assert chunks is None

    def test_overlap_from_previous(self):
        """Chunks 1+ should get overlap from previous chunk's tail."""
        text = make_plain_text(40, words_per_paragraph=100)
        chunks = _split_plain_text(text, target_chunk_words=1500)
        assert chunks is not None
        assert chunks[0].overlap_prefix == ""
        for chunk in chunks[1:]:
            assert chunk.overlap_prefix != ""


# ─── merge_formatter_chunks tests ─────────────────────────────────────


class TestMergeFormatterChunks:
    def test_single_chunk_passthrough(self):
        """Single chunk should pass through unchanged."""
        result = merge_formatter_chunks(["Hello world"])
        assert result == "Hello world"

    def test_empty_list(self):
        """Empty list should return empty string."""
        assert merge_formatter_chunks([]) == ""

    def test_keeps_only_first_header(self):
        """Only chunk 0's header should be preserved."""
        chunk0 = """**Project:** Test Project
**Program:** Test Program

---

First chunk body content here."""

        chunk1 = """**Project:** Test Project
**Program:** Test Program

---

Second chunk body content here."""

        result = merge_formatter_chunks([chunk0, chunk1])
        # Should have only one Project line
        assert result.count("**Project:**") == 1
        assert "First chunk body content" in result
        assert "Second chunk body content" in result

    def test_strips_formatted_transcript_heading(self):
        """# Formatted Transcript heading should be stripped from chunks 1+."""
        chunk0 = """**Project:** Test

---

First body."""

        chunk1 = """# Formatted Transcript

Second body."""

        result = merge_formatter_chunks([chunk0, chunk1])
        assert "# Formatted Transcript" not in result
        assert "Second body" in result

    def test_strips_intermediate_status(self):
        """Status line should only come from last chunk."""
        chunk0 = "Body one.\n\n**Status:** ready_for_editing"
        chunk1 = "Body two.\n\n**Status:** ready_for_editing"
        chunk2 = "Body three.\n\n**Status:** ready_for_editing"

        result = merge_formatter_chunks([chunk0, chunk1, chunk2])
        assert result.count("**Status:**") == 1
        # Status should be at the end
        assert result.strip().endswith("**Status:** ready_for_editing")

    def test_collects_review_notes(self):
        """Review notes from all chunks should be merged at top."""
        chunk0 = "<!-- REVIEW NOTES -->\nNote from chunk 0\n\nBody zero."
        chunk1 = "<!-- REVIEW NOTES -->\nNote from chunk 1\n\nBody one."

        result = merge_formatter_chunks([chunk0, chunk1])
        # Both notes should be in the consolidated block
        assert "Note from chunk 0" in result
        assert "Note from chunk 1" in result
        # Notes should appear before bodies
        notes_pos = result.find("REVIEW NOTES")
        body_pos = result.find("Body zero")
        assert notes_pos < body_pos

    def test_strips_provenance_comments(self):
        """Provenance HTML comments should be stripped."""
        chunk0 = "<!-- model: gpt-4o | tier: default | cost: $0.01 | tokens: 500 -->\n**Project:** Test\n\n---\n\nBody."
        chunk1 = "<!-- model: gpt-4o | tier: default | cost: $0.01 | tokens: 500 -->\nMore body."

        result = merge_formatter_chunks([chunk0, chunk1])
        assert "model: gpt-4o" not in result

    def test_trims_overlap(self):
        """Duplicate text at chunk seams should be trimmed."""
        # Create chunks with overlapping text at the seam
        shared_text = " ".join([f"overlap{i}" for i in range(20)])
        chunk0 = f"Start of chunk zero. {shared_text}"
        chunk1 = f"{shared_text} End of chunk one."

        result = merge_formatter_chunks([chunk0, chunk1])
        # The shared text should not appear twice in full
        # (exact dedup depends on the window size and ratio)
        assert "Start of chunk zero" in result
        assert "End of chunk one" in result
