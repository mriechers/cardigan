"""Proper-noun extraction from analyst speaker tables.

Port of ``scripts/poc_house_style_normalizer.py``'s ``extract_proper_nouns``.
Parses the "Speakers & Roles" markdown table from an analyst phase's
``analyst_output.md`` and returns the per-job proper nouns (full names plus
bare surnames) used to extend the casing engine's canonical map
(:func:`api.services.style_engine.casing.build_canonical`) for a specific
episode -- no hand-curated per-show list required.
"""

from __future__ import annotations

import re

# Analyst speaker table rows look like:
#   | Robin Vos | Assembly Speaker | Budget debate | 1:20 |
# Captures 2-4 capitalized words. Every word in the match -- including
# continuation words -- must itself start with a capital letter, so a
# lowercase mid-name particle (e.g. "van der") breaks the match early; this
# is the same limitation the PoC has and is intentionally not "fixed" here.
_TABLE_ROW_RE = re.compile(r"\|\s*([A-Z][a-zA-Z.'-]+(?: [A-Z][a-zA-Z.'-]+){1,3})\s*\|")

# Header-row cell text to skip (case-insensitive).
_HEADER_CELLS = {"speaker", "role/title", "name"}

# PoC threshold: only register a standalone surname when it's longer than
# this many characters (i.e. 4+ chars).
_MIN_SURNAME_LEN = 4


def extract_proper_nouns(analyst_md: str, stoplist: set[str] | None = None) -> list[str]:
    """Extract proper nouns from an analyst output's speaker table.

    Returns a de-duplicated list, first-seen order preserved, containing
    each captured full name plus -- for multi-word names -- its bare
    surname (last token) registered standalone, unless that surname is in
    ``stoplist`` (case-insensitive) or shorter than 4 characters. Tolerates
    a leading HTML provenance comment and any other non-table-row lines
    (they simply don't match ``_TABLE_ROW_RE`` and are skipped). Empty or
    ``None`` input returns an empty list -- this never raises.
    """
    if not analyst_md:
        return []

    stoplist_lower = {s.lower() for s in (stoplist or set())}
    seen: dict[str, None] = {}

    for line in analyst_md.splitlines():
        match = _TABLE_ROW_RE.match(line)
        if not match:
            continue

        candidate = match.group(1).strip()
        if candidate.lower() in _HEADER_CELLS:
            continue
        seen.setdefault(candidate, None)

        parts = candidate.split()
        if len(parts) >= 2:
            surname = parts[-1]
            if surname.lower() not in stoplist_lower and len(surname) >= _MIN_SURNAME_LEN:
                seen.setdefault(surname, None)

    return list(seen.keys())
