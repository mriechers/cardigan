"""SEO field extraction with span fidelity.

Parses a Cardigan ``seo_output.md`` report's "Recommended" title / short /
long description values, returning ``FieldSpan`` objects whose
``(start, end)`` are exact character offsets into the source document --
so a later pipeline stage can splice a normalized value back into the
document in place. Port/extension of
``scripts/poc_house_style_normalizer.py``'s ``extract_recommended``, which
this module generalizes to also carry span information.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
