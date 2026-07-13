"""SEO field extraction with span fidelity, plus the timestamp phase's
structured-contract chapter list I/O.

Parses a Cardigan ``seo_output.md`` report's "Recommended" title / short /
long description values, returning ``FieldSpan`` objects whose
``(start, end)`` are exact character offsets into the source document --
so a later pipeline stage can splice a normalized value back into the
document in place. Port/extension of
``scripts/poc_house_style_normalizer.py``'s ``extract_recommended``, which
this module generalizes to also carry span information.

The timestamp-phase functions below (``parse_chapter_list``,
``emit_timestamp_report``) are the I/O half of the structured contract
described in ``api.services.style_engine.timecodes``'s module docstring:
the model returns a minimal ``` ```chapters ``` ``` fenced block (one
"<timecode> <title>" line per chapter); this module parses that block into
``Chapter`` objects and renders the full ``timestamp_output.md`` body from
the deterministically-snapped result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from api.services.style_engine.rules import StyleRules
from api.services.style_engine.timecodes import (
    Chapter,
    emit_media_manager_table,
    emit_youtube_list,
    format_youtube,
    parse_timecode_to_ms,
)

# Header substrings are intentionally loose (unanchored, no end-of-line
# assertion) so they tolerate the real prompt template's parenthetical
# suffixes, e.g. "### Title (Final Recommendation)" or "### Short
# Description (150 chars max)" -- see prompts/seo.md's output template.
_FIELD_HEADERS: dict[str, str] = {
    "title": r"### Title",
    "short_description": r"### Short Description",
    "long_description": r"### Long Description",
}


@dataclass
class FieldSpan:
    """A field's extracted value plus its exact span within the source doc."""

    value: str
    start: int  # span of VALUE text within the source document
    end: int


@dataclass
class SeoFields:
    """Extracted SEO fields. Each is ``None`` if its section wasn't found."""

    title: FieldSpan | None = None
    short_description: FieldSpan | None = None
    long_description: FieldSpan | None = None

    def to_dict(self) -> dict:
        return {
            "title": _span_to_dict(self.title),
            "short_description": _span_to_dict(self.short_description),
            "long_description": _span_to_dict(self.long_description),
        }


def _span_to_dict(span: FieldSpan | None) -> dict | None:
    if span is None:
        return None
    return {"value": span.value, "start": span.start, "end": span.end}


def extract_seo_fields(seo_md: str) -> SeoFields:
    """Extract title / short_description / long_description from ``seo_md``.

    Each field appears as a ``**Recommended:**`` line under its
    ``### <Heading>`` section. Missing fields are ``None`` -- this never
    raises, even on malformed or empty input. A leading HTML provenance
    comment (or any other preamble) before the markdown body is tolerated
    automatically, since header matching is unanchored (``re.search``, not
    anchored to string start).

    Spans are computed so that ``seo_md[start:end] == value`` exactly,
    accounting for any leading whitespace on the captured line that gets
    stripped from ``value``.
    """
    extracted: dict[str, FieldSpan] = {}

    for key, header in _FIELD_HEADERS.items():
        pattern = header + r".*?\*\*Recommended:\*\*\s*\n+([^\n]+)"
        match = re.search(pattern, seo_md, re.DOTALL)
        if not match:
            continue

        raw = match.group(1)
        value = raw.strip()
        if not value:
            continue

        # raw.strip() only removes a prefix/suffix of raw, so the stripped
        # value is a contiguous substring of raw at this leading-whitespace
        # offset -- recompute the exact span into seo_md for it.
        leading_ws = len(raw) - len(raw.lstrip())
        value_start = match.start(1) + leading_ws
        value_end = value_start + len(value)
        extracted[key] = FieldSpan(value=value, start=value_start, end=value_end)

    return SeoFields(
        title=extracted.get("title"),
        short_description=extracted.get("short_description"),
        long_description=extracted.get("long_description"),
    )


def splice_seo_fields(seo_md: str, fields: SeoFields, replacements: dict[str, str]) -> str:
    """Replace field VALUES in ``seo_md`` using the spans carried by ``fields``.

    ``replacements`` keys are ``"title"`` | ``"short_description"`` |
    ``"long_description"``; only fields present in *both* ``fields``
    (non-``None``) and ``replacements`` are spliced -- an unknown key, or a
    key whose ``fields`` span is ``None``, is silently skipped.

    Splices are applied from the LAST span forward (descending ``start``
    offset) so that spans earlier in the document stay valid for the
    remainder of the loop -- replacing a value with one of a different
    length shifts every offset after it, but never before it.

    Before each splice, re-checks ``seo_md[start:end] == field.value`` --
    the span-integrity guard. If the document has been tampered with (or the
    spans are stale) since extraction, raises ``ValueError`` rather than
    silently splicing the wrong text.

    Returns the new document. Everything outside the spliced spans is
    preserved byte-for-byte. When ``replacements`` contributes no applicable
    splices, returns ``seo_md`` unchanged.
    """
    field_map = {
        "title": fields.title,
        "short_description": fields.short_description,
        "long_description": fields.long_description,
    }

    to_splice: list[tuple[str, FieldSpan, str]] = []
    for key, new_value in replacements.items():
        span = field_map.get(key)
        if span is None:
            continue
        to_splice.append((key, span, new_value))

    # Descending start offset: splice the rightmost span first so already
    # spliced regions never shift the not-yet-spliced spans that precede them.
    to_splice.sort(key=lambda item: item[1].start, reverse=True)

    result = seo_md
    for key, span, new_value in to_splice:
        actual = result[span.start : span.end]
        if actual != span.value:
            raise ValueError(
                f"splice_seo_fields: span integrity check failed for field "
                f"{key!r} at [{span.start}:{span.end}] -- expected {span.value!r}, "
                f"found {actual!r}"
            )
        result = result[: span.start] + new_value + result[span.end :]

    return result


# ---------------------------------------------------------------------------
# timestamp phase -- chapter list structured-contract I/O
# ---------------------------------------------------------------------------

# The model's chapter list is a ```chapters fenced block, one chapter per
# line: "<timecode> <title>" (e.g. "2:30 Sports betting debate begins").
# Unanchored + DOTALL so a leading provenance comment or trailing prose
# around the fence never breaks the match.
_CHAPTERS_FENCE_RE = re.compile(r"```chapters\s*\n(.*?)```", re.DOTALL)

# A chapter line: a M:SS / H:MM:SS timecode (no milliseconds -- the model
# never has sub-second precision to offer), whitespace, then the title (the
# rest of the line). Exported so a caller can reuse the same line shape
# elsewhere (e.g. to validate a candidate line before it's ever parsed).
CHAPTER_LINE_RE = re.compile(r"^(\d{1,2}(?::\d{2}){1,2})\s+(.+)$")


def parse_chapter_list(raw_output: str) -> list[Chapter] | None:
    """Extract the model's chapter list from a ```chapters fenced block.

    Returns ``None`` when the fence is absent, empty, or every line inside
    it is unparseable -- graceful in every case, never raises. Lines that
    don't match ``CHAPTER_LINE_RE``, or whose timecode
    :func:`~api.services.style_engine.timecodes.parse_timecode_to_ms`
    rejects, or whose title is blank after stripping, are individually
    skipped rather than failing the whole block -- a partially-garbled
    response still yields whatever chapters it validly contains. Prose
    before/after the fence (including a leading HTML provenance comment) is
    tolerated since the fence is located via an unanchored search.
    """
    if not raw_output:
        return None

    fence_match = _CHAPTERS_FENCE_RE.search(raw_output)
    if not fence_match:
        return None

    chapters: list[Chapter] = []
    for line in fence_match.group(1).splitlines():
        line = line.strip()
        if not line:
            continue

        line_match = CHAPTER_LINE_RE.match(line)
        if not line_match:
            continue

        timecode_text, title_text = line_match.groups()
        start_ms = parse_timecode_to_ms(timecode_text)
        title = title_text.strip()
        if start_ms is None or not title:
            continue

        chapters.append(Chapter(title=title, start_ms=start_ms))

    return chapters or None


# Human-readable bullet text for each phases.timestamp.constraints key seen
# in config/house_style.yaml. An unrecognized key (a synthetic test's made-up
# constraint name) falls back to a generic underscore->space transform, same
# pattern as pre_stage.py's _CATEGORY_LABELS.
_TIMESTAMP_CONSTRAINT_NOTES: dict[str, str] = {
    "no_gaps": "No gaps between chapters -- each ends exactly where the next begins.",
    "chronological": "Chapters are listed in chronological order.",
    "final_end_equals_srt_end": "Final chapter end time matches the last SRT timestamp.",
}


def emit_timestamp_report(
    chapters: list[Chapter],
    *,
    srt_end_ms: int,
    rules: StyleRules,
    project_name: str | None = None,
) -> str:
    """Render the full ``timestamp_output.md`` body from a snapped chapter list.

    Built entirely from ``timecodes.emit_media_manager_table`` /
    ``emit_youtube_list`` (the actual math) plus
    ``rules`` ``phases.timestamp.constraints`` (which of the documented
    guarantees to call out in the Notes section -- a constraint absent or
    ``false`` in ``rules`` is simply not mentioned, proving the section is
    rules-driven rather than a fixed string). Headings mirror
    ``prompts/timestamp.md``'s current output template.

    ``project_name`` -- when truthy -- renders a ``**Project:** {project_name}``
    line directly above ``**Duration:**``, matching
    ``prompts/timestamp.md``'s output template. Omitted entirely (not even a
    blank ``**Project:**`` line) when ``None`` or empty, which keeps every
    caller that doesn't pass it byte-identical to this function's
    pre-task-4b output -- callers are expected to pass
    ``context.get("project_name")`` from the worker's phase context (see
    ``post_stage._run_timestamp_post_stage``).
    """
    media_manager_table = emit_media_manager_table(chapters, srt_end_ms)
    youtube_list = emit_youtube_list(chapters)
    duration = format_youtube(srt_end_ms)

    timestamp_cfg = (rules.raw.get("phases", {}) or {}).get("timestamp", {}) or {}
    constraints = timestamp_cfg.get("constraints", {}) or {}
    note_lines = [
        f"- {_TIMESTAMP_CONSTRAINT_NOTES.get(key, key.replace('_', ' '))}"
        for key, enabled in constraints.items()
        if enabled
    ]
    if not note_lines:
        note_lines = ["- Timestamps derived from SRT timecodes."]

    project_line = f"**Project:** {project_name}\n" if project_name else ""

    return (
        "# Timestamp Report\n\n"
        f"{project_line}"
        f"**Duration:** {duration}\n\n"
        "---\n\n"
        "## Media Manager Format\n\n"
        "Copy-paste this table into PBS Media Manager chapter fields:\n\n"
        f"{media_manager_table}\n\n"
        "---\n\n"
        "## YouTube Format\n\n"
        "Copy-paste these timestamps directly into your YouTube description:\n\n"
        f"{youtube_list}\n\n"
        "---\n\n"
        "## Notes\n\n" + "\n".join(note_lines) + "\n"
    )
