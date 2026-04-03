#!/usr/bin/env python3
"""Standalone transcript formatter using Gemini API.

Splits long SRT files into chunks, sends each to Gemini in parallel,
and stitches the formatted outputs into a single markdown document.

Usage:
    python format-transcript.py input.srt
    python format-transcript.py input.srt --speakers "Host: Frederica Freyberg, Guest: Tony Evers"
    python format-transcript.py input.srt --program "Here and Now"
    python format-transcript.py input.srt -o formatted_output.md

Requires:
    pip install google-genai

Environment:
    GEMINI_API_KEY  — your Gemini API key
"""

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# SRT parsing (self-contained — no Cardigan imports needed)
# ---------------------------------------------------------------------------

@dataclass
class SRTCaption:
    index: int
    start_ms: int
    end_ms: int
    text: str

    @property
    def start_timecode(self) -> str:
        return ms_to_srt_timecode(self.start_ms)

    @property
    def end_timecode(self) -> str:
        return ms_to_srt_timecode(self.end_ms)

    def to_srt(self) -> str:
        return f"{self.index}\n{self.start_timecode} --> {self.end_timecode}\n{self.text}\n"


def ms_to_srt_timecode(ms: int) -> str:
    if ms < 0:
        ms = 0
    h = ms // 3_600_000
    m = (ms % 3_600_000) // 60_000
    s = (ms % 60_000) // 1_000
    frac = ms % 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{frac:03d}"


def srt_timecode_to_ms(tc: str) -> int:
    tc = tc.replace(".", ",")
    parts = tc.split(",")
    hms = parts[0].split(":")
    h, m, s = int(hms[0]), int(hms[1]), int(hms[2])
    frac = int(parts[1]) if len(parts) > 1 else 0
    return h * 3_600_000 + m * 60_000 + s * 1_000 + frac


def parse_srt(content: str) -> list[SRTCaption]:
    captions = []
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        if len(lines) < 3:
            continue
        try:
            index = int(lines[0].strip())
            time_match = re.match(
                r"(\d{1,2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,\.]\d{3})",
                lines[1].strip(),
            )
            if not time_match:
                continue
            start_ms = srt_timecode_to_ms(time_match.group(1))
            end_ms = srt_timecode_to_ms(time_match.group(2))
            text = "\n".join(lines[2:]).strip()
            captions.append(SRTCaption(index=index, start_ms=start_ms, end_ms=end_ms, text=text))
        except (ValueError, IndexError):
            continue
    return captions


def generate_srt(captions: list[SRTCaption]) -> str:
    parts = []
    for i, c in enumerate(captions, 1):
        c.index = i
        parts.append(c.to_srt())
    return "\n".join(parts)


def format_duration(ms: int) -> str:
    h = ms // 3_600_000
    m = (ms % 3_600_000) // 60_000
    s = (ms % 60_000) // 1_000
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

TARGET_CHUNK_WORDS = 1500
OVERLAP_CAPTIONS = 5
CHUNK_THRESHOLD_WORDS = 3000


@dataclass
class Chunk:
    index: int
    content: str
    overlap_prefix: str = ""


def split_srt_into_chunks(captions: list[SRTCaption]) -> list[Chunk]:
    """Split captions into chunks at sentence boundaries."""
    total_words = sum(len(c.text.split()) for c in captions)
    if total_words < CHUNK_THRESHOLD_WORDS:
        return [Chunk(index=0, content=generate_srt(captions))]

    chunks: list[Chunk] = []
    chunk_start = 0
    accumulated = 0

    i = 0
    while i < len(captions):
        accumulated += len(captions[i].text.split())

        if accumulated >= TARGET_CHUNK_WORDS and i < len(captions) - 1:
            # Look ahead for sentence boundary
            break_idx = i
            for j in range(i, min(i + 10, len(captions))):
                if captions[j].text.strip() and captions[j].text.strip()[-1] in ".?!":
                    break_idx = j
                    break

            chunk_captions = captions[chunk_start : break_idx + 1]

            overlap = ""
            if chunks and OVERLAP_CAPTIONS > 0:
                ov_start = max(chunk_start - OVERLAP_CAPTIONS, 0)
                ov_caps = captions[ov_start:chunk_start]
                if ov_caps:
                    overlap = generate_srt(ov_caps)

            chunks.append(Chunk(
                index=len(chunks),
                content=generate_srt(chunk_captions),
                overlap_prefix=overlap,
            ))

            chunk_start = break_idx + 1
            accumulated = 0
            i = break_idx + 1
            continue
        i += 1

    # Final chunk
    if chunk_start < len(captions):
        remaining = captions[chunk_start:]
        overlap = ""
        if chunks and OVERLAP_CAPTIONS > 0:
            ov_start = max(chunk_start - OVERLAP_CAPTIONS, 0)
            ov_caps = captions[ov_start:chunk_start]
            if ov_caps:
                overlap = generate_srt(ov_caps)
        chunks.append(Chunk(
            index=len(chunks),
            content=generate_srt(remaining),
            overlap_prefix=overlap,
        ))

    return chunks


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

def merge_chunks(formatted_chunks: list[str]) -> str:
    """Merge formatted outputs into a single document."""
    if not formatted_chunks:
        return ""
    if len(formatted_chunks) == 1:
        return formatted_chunks[0]

    review_pattern = re.compile(r"<!--\s*REVIEW NOTES.*?-->", re.DOTALL | re.IGNORECASE)
    review_notes: list[str] = []
    header = ""
    bodies: list[str] = []
    status_line = ""

    for i, chunk in enumerate(formatted_chunks):
        chunk = chunk.strip()
        # Strip model attribution comments
        chunk = re.sub(r"^<!--\s*model:.*?-->\s*\n?", "", chunk)

        # Collect review notes
        notes = review_pattern.findall(chunk)
        for note in notes:
            if note.strip() and note not in review_notes:
                review_notes.append(note.strip())
        chunk = review_pattern.sub("", chunk).strip()

        if i == 0:
            parts = re.split(r"^---+\s*$", chunk, maxsplit=1, flags=re.MULTILINE)
            if len(parts) > 1:
                header = parts[0].strip()
                body = parts[1].strip()
            else:
                body = chunk
        else:
            body = chunk
            body = re.sub(r"^#\s+Formatted Transcript\s*\n?", "", body, flags=re.MULTILINE)
            body = re.sub(
                r"^\*\*(?:Project|Program|Duration|Date|Air Date|Media ID):\*\*.*\n?",
                "", body, flags=re.MULTILINE,
            )
            body = re.sub(r"^---+\s*\n?", "", body.strip(), flags=re.MULTILINE)
            body = body.strip()

        # Extract status
        status_match = re.search(r"^\*\*Status:\*\*\s+.*$", body, flags=re.MULTILINE)
        if status_match:
            if i == len(formatted_chunks) - 1:
                status_line = status_match.group(0)
            body = re.sub(r"^\*\*Status:\*\*\s+.*$", "", body, flags=re.MULTILINE).strip()

        # Remove trailing --- separators
        body = re.sub(r"\n---+\s*$", "", body.strip())

        bodies.append(body)

    # Deduplicate overlap at seams
    for i in range(len(bodies) - 1):
        bodies[i + 1] = _trim_overlap(bodies[i], bodies[i + 1])

    # Assemble
    result_parts = []
    if header:
        result_parts.append(header)
    if review_notes:
        result_parts.append("\n".join(review_notes))
    if header or review_notes:
        result_parts.append("---")
    result_parts.append("\n\n".join(b for b in bodies if b))
    if status_line:
        result_parts.append("---")
        result_parts.append(status_line)

    return "\n\n".join(result_parts)


def _trim_overlap(prev: str, next_body: str, window: int = 100) -> str:
    """Remove duplicate text at chunk seams."""
    prev_words = prev.split()
    next_words = next_body.split()
    if len(prev_words) < 10 or len(next_words) < 10:
        return next_body

    prev_tail = prev_words[-window:]
    next_head = next_words[:window]

    best = 0
    for start in range(len(prev_tail)):
        candidate = prev_tail[start:]
        if len(candidate) < 5:
            break
        match_len = 0
        for k in range(min(len(candidate), len(next_head))):
            if candidate[k].lower() == next_head[k].lower():
                match_len += 1
            else:
                break
        if match_len > best and match_len >= 5:
            best = match_len

    if best > 0 and best / min(window, len(next_head)) > 0.5:
        return " ".join(next_words[best:])
    return next_body


# ---------------------------------------------------------------------------
# Gemini API
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (Path(__file__).parent / "PROMPT.md").read_text()

VERBATIM_INSTRUCTION = """CRITICAL: You MUST preserve ALL spoken dialogue. Do NOT summarize, condense, or paraphrase.
Every sentence spoken in the transcript must appear in your output. You may remove filler words
(um, uh) and fix grammar, but do NOT drop or merge sentences. Completeness is more important than brevity.
If a caption is garbled or unclear, include your best reconstruction rather than dropping it. NEVER silently omit content."""


async def format_chunk(
    client,
    model: str,
    chunk: Chunk,
    total_chunks: int,
    user_context: str,
) -> str:
    """Send one chunk to Gemini and return the formatted text."""
    if chunk.index == 0:
        user_message = f"{VERBATIM_INSTRUCTION}\n\n"
        if user_context:
            user_message += f"Context: {user_context}\n\n"
        user_message += f"Please format this transcript:\n\n---\n{chunk.content}\n---"
    else:
        user_message = f"""{VERBATIM_INSTRUCTION}

IMPORTANT: This is section {chunk.index + 1} of {total_chunks} of a long transcript being processed in parts.
DO NOT generate the metadata header (Program, Duration, Date).
DO NOT generate "# Formatted Transcript" heading.
Begin directly with speaker attribution and dialogue.
The previous section ended with:
---
{chunk.overlap_prefix}
---
Continue formatting from where the previous section left off. Do NOT repeat content from the overlap above.

Please format this transcript section:

---
{chunk.content}
---"""

    response = await client.aio.models.generate_content(
        model=model,
        contents=user_message,
        config={
            "system_instruction": SYSTEM_PROMPT,
            "temperature": 0.2,
        },
    )
    return response.text


async def run(
    srt_path: str,
    speakers: Optional[str],
    program: Optional[str],
    output_path: Optional[str],
    model: str,
    max_parallel: int,
):
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("Error: Set GEMINI_API_KEY or GOOGLE_API_KEY environment variable.", file=sys.stderr)
        sys.exit(1)

    from google import genai

    client = genai.Client(api_key=api_key)

    # Read and parse SRT
    srt_content = Path(srt_path).read_text(encoding="utf-8")
    captions = parse_srt(srt_content)
    if not captions:
        print("Error: No captions parsed from SRT file.", file=sys.stderr)
        sys.exit(1)

    duration = format_duration(captions[-1].end_ms)
    total_words = sum(len(c.text.split()) for c in captions)

    # Build user context string
    context_parts = []
    if speakers:
        context_parts.append(f"Speakers: {speakers}")
    if program:
        context_parts.append(f"Program: {program}")
    context_parts.append(f"Duration: {duration}")
    user_context = ". ".join(context_parts)

    # Split into chunks
    chunks = split_srt_into_chunks(captions)
    total_chunks = len(chunks)

    print(f"Parsed {len(captions)} captions ({total_words:,} words, {duration})")
    print(f"Processing in {total_chunks} chunk(s) with up to {max_parallel} parallel requests...")

    # Process chunks with semaphore for backpressure
    semaphore = asyncio.Semaphore(max_parallel)

    async def bounded_format(chunk: Chunk) -> tuple[int, str]:
        async with semaphore:
            print(f"  Chunk {chunk.index + 1}/{total_chunks}...", flush=True)
            result = await format_chunk(client, model, chunk, total_chunks, user_context)
            print(f"  Chunk {chunk.index + 1}/{total_chunks} done ({len(result):,} chars)")
            return chunk.index, result

    tasks = [bounded_format(c) for c in chunks]
    results = await asyncio.gather(*tasks)

    # Sort by chunk index and merge
    results.sort(key=lambda r: r[0])
    formatted_chunks = [r[1] for r in results]
    merged = merge_chunks(formatted_chunks)

    # Write output
    if output_path:
        out = Path(output_path)
    else:
        out = Path(srt_path).with_suffix(".md")

    out.write_text(merged, encoding="utf-8")
    print(f"\nFormatted transcript written to: {out}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Format an SRT transcript using Gemini API with PBS Wisconsin editorial standards.",
    )
    parser.add_argument("srt_file", help="Path to the SRT file")
    parser.add_argument("--speakers", "-s", help='Speaker names (e.g., "Host: Frederica Freyberg, Guest: Tony Evers")')
    parser.add_argument("--program", "-p", help='Program name (e.g., "Here and Now")')
    parser.add_argument("--output", "-o", help="Output file path (default: same name with .md extension)")
    parser.add_argument("--model", "-m", default="gemini-2.5-flash", help="Gemini model (default: gemini-2.5-flash)")
    parser.add_argument("--parallel", type=int, default=3, help="Max parallel API requests (default: 3)")

    args = parser.parse_args()

    if not Path(args.srt_file).exists():
        print(f"Error: File not found: {args.srt_file}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run(
        srt_path=args.srt_file,
        speakers=args.speakers,
        program=args.program,
        output_path=args.output,
        model=args.model,
        max_parallel=args.parallel,
    ))


if __name__ == "__main__":
    main()
