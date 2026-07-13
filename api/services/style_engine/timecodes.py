"""Timestamp structured-contract engine -- pure timecode math + chapter emission.

Pure stdlib, no I/O, no DB/async/FastAPI imports. This is the deterministic
half of the timestamp phase's structured contract (see
``api.services.style_engine.phase_io.parse_chapter_list`` /
``emit_timestamp_report`` and ``pre_stage.py``/``post_stage.py``'s timestamp
paths): the model returns a minimal chapter list -- title + a chosen
boundary timecode, picked from a candidate list the pre-stage computed from
the SRT -- and this module renders BOTH publishing formats (the PBS Media
Manager table and the YouTube description list) with exact math the model
never has to get right.

Chapter start times are always parsed from model-authored M:SS / H:MM:SS
text with no milliseconds component (see ``phase_io.CHAPTER_LINE_RE``), so
every ``Chapter.start_ms`` produced by the structured contract is an exact
multiple of 1000. That invariant is what makes :func:`emit_media_manager_table`'s
end-of-chapter math exact: "end = next start - 1ms" on a whole-second start
always lands on an ``XXX999`` millisecond remainder with no rounding trick
required -- :func:`format_media_manager` just does honest ``ms -> H:MM:SS.mmm``
arithmetic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Matches M:SS, MM:SS, H:MM:SS, and H:MM:SS.mmm. The hours group is optional
# and greedy -- for a single-colon input like "2:30" the engine first tries
# to consume "2:" as hours, fails to find the required trailing ":SS", and
# backtracks to the (correct) M:SS interpretation. See parse_timecode_to_ms's
# docstring for the range validation this regex alone doesn't enforce.
_TIMECODE_RE = re.compile(r"^(?:(?P<h>\d+):)?(?P<m>\d{1,2}):(?P<s>\d{2})(?:\.(?P<ms>\d{1,3}))?$")


@dataclass
class Chapter:
    """A single chapter marker: title + its start time in milliseconds."""

    title: str
    start_ms: int


def parse_timecode_to_ms(text: str) -> int | None:
    """Parse a M:SS / MM:SS / H:MM:SS / H:MM:SS.mmm timecode into milliseconds.

    Returns ``None`` on anything that isn't a well-formed, in-range timecode
    (wrong shape, non-numeric, seconds >= 60, or -- when an hours segment is
    present -- minutes >= 60). A bare M:SS/MM:SS minutes value has no upper
    bound (a boundary candidate could legitimately read "75:30"). Never
    raises, including on non-string input.
    """
    if not isinstance(text, str):
        return None

    match = _TIMECODE_RE.match(text.strip())
    if not match:
        return None

    hours_text = match.group("h")
    minutes = int(match.group("m"))
    seconds = int(match.group("s"))
    ms_text = match.group("ms")

    if seconds >= 60:
        return None
    if hours_text is not None and minutes >= 60:
        return None

    hours = int(hours_text) if hours_text is not None else 0
    # A ".5" fractional suffix means 500ms, not 5ms -- pad on the right to a
    # fixed 3-digit decimal before converting, the same way "0.5" seconds is
    # "500" milliseconds.
    milliseconds = int(ms_text.ljust(3, "0")) if ms_text else 0

    return ((hours * 3600 + minutes * 60 + seconds) * 1000) + milliseconds


def format_media_manager(ms: int, *, end: bool = False) -> str:
    """Format ``ms`` as a PBS Media Manager timestamp: ``H:MM:SS.mmm``.

    ``end`` does not change the arithmetic here -- it documents which side
    of a chapter boundary the caller is formatting (mirrors
    config/house_style.yaml's ``phases.timestamp.formats.media_manager``
    ``start``/``end`` template pair). The ".000"/".999" look of house
    style's start/end convention falls out automatically rather than being
    special-cased: chapter start times are always whole-second multiples
    (see module docstring), so ``end_ms = next_start_ms - 1`` always has a
    true millisecond remainder of 999. Because the arithmetic here is
    honest (no flooring/rounding), this also stays exact for the one value
    that ISN'T a "next start - 1" -- the final row's end, which is the
    literal ``srt_end_ms`` from the SRT file and may carry any millisecond
    remainder.
    """
    total_seconds, ms_remainder = divmod(ms, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{ms_remainder:03d}"


def format_youtube(ms: int) -> str:
    """Format ``ms`` as a YouTube description timestamp.

    ``M:SS`` (no leading zero on minutes) under one hour; ``H:MM:SS`` at or
    over one hour. No milliseconds -- the sub-second remainder is floored
    off, matching house style's "No milliseconds" YouTube spec.
    """
    total_seconds = ms // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def snap_chapters(
    chapters: list[Chapter],
    *,
    srt_end_ms: int,
    max_chapters: int,
    first_chapter_title: str,
) -> tuple[list[Chapter], list[str]]:
    """Deterministic chapter-list cleanup. Returns ``(snapped, notes)``.

    Applied in this order, each step operating on the previous step's
    output:

    1. Sort chronologically by ``start_ms`` (stable).
    2. Drop exact-duplicate starts, keeping the first occurrence.
    3. Force the first chapter (enforce-tier per
       ``phases.timestamp.first_chapter``): the resulting chapter[0] is
       ALWAYS ``Chapter(first_chapter_title, 0)`` -- if the surviving first
       chapter already starts at 0, its title is overwritten (noted only
       when the title actually changes); otherwise a new chapter is
       prepended (noted, including when the model produced no chapters at
       all).
    4. Drop any chapter whose start is beyond ``srt_end_ms`` (noted, one
       entry per drop).
    5. If more than ``max_chapters`` remain, truncate to the first
       ``max_chapters`` and note the truncation (one summary entry naming
       every dropped title).

    ``notes`` are human-readable strings describing each adjustment --
    empty when the input already satisfied every constraint (aside from the
    always-applied first-chapter enforcement, which only produces a note
    when it actually changes something).
    """
    notes: list[str] = []

    working = sorted(chapters, key=lambda chapter: chapter.start_ms)

    deduped: list[Chapter] = []
    seen_starts: set[int] = set()
    for chapter in working:
        if chapter.start_ms in seen_starts:
            notes.append(
                f'dropped duplicate chapter "{chapter.title}" at {format_youtube(chapter.start_ms)} '
                "(start time already used by an earlier chapter)"
            )
            continue
        seen_starts.add(chapter.start_ms)
        deduped.append(chapter)

    if deduped and deduped[0].start_ms == 0:
        original = deduped[0]
        if original.title != first_chapter_title:
            notes.append(f'forced first chapter title to "{first_chapter_title}" (model used "{original.title}")')
        deduped[0] = Chapter(title=first_chapter_title, start_ms=0)
    else:
        if deduped:
            notes.append(
                f'prepended first chapter "{first_chapter_title}" at 0:00 '
                f"(model's first chapter started at {format_youtube(deduped[0].start_ms)})"
            )
        else:
            notes.append(f'prepended first chapter "{first_chapter_title}" at 0:00 (model provided no chapters)')
        deduped.insert(0, Chapter(title=first_chapter_title, start_ms=0))

    in_range: list[Chapter] = []
    for chapter in deduped:
        if chapter.start_ms > srt_end_ms:
            notes.append(
                f'dropped chapter "{chapter.title}" at {format_youtube(chapter.start_ms)} '
                f"-- beyond SRT end ({format_youtube(srt_end_ms)})"
            )
            continue
        in_range.append(chapter)

    if len(in_range) > max_chapters:
        dropped = in_range[max_chapters:]
        in_range = in_range[:max_chapters]
        titles = ", ".join(f'"{chapter.title}"' for chapter in dropped)
        notes.append(f"dropped {len(dropped)} chapter(s) beyond max_chapters ({max_chapters}): {titles}")

    return in_range, notes


def emit_media_manager_table(chapters: list[Chapter], srt_end_ms: int) -> str:
    """Markdown table: each row's end = next start - 1ms; last end = srt_end_ms."""
    lines = ["| Title | Start Time | End Time |", "|-------|------------|----------|"]
    count = len(chapters)
    for index, chapter in enumerate(chapters):
        end_ms = chapters[index + 1].start_ms - 1 if index + 1 < count else srt_end_ms
        start_text = format_media_manager(chapter.start_ms, end=False)
        end_text = format_media_manager(end_ms, end=True)
        lines.append(f"| {chapter.title} | {start_text} | {end_text} |")
    return "\n".join(lines)


def emit_youtube_list(chapters: list[Chapter]) -> str:
    """One ``M:SS Title`` / ``H:MM:SS Title`` line per chapter, in order."""
    return "\n".join(f"{format_youtube(chapter.start_ms)} {chapter.title}" for chapter in chapters)
