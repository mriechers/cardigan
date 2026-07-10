"""Down-style casing engine -- deterministic proper-noun casing restoration.

Port of ``scripts/poc_house_style_normalizer.py``'s ``build_canonical`` /
``to_down_style`` into the style_engine package. Pure stdlib: given a
canonical lowercase-term -> cased-form map (built from ``StyleRules`` plus
optional per-job proper nouns), down-styles arbitrary title/description
text so that LLM output converges to identical casing regardless of which
model produced it -- one model's over-capitalized title and another's
over-lowercased title both normalize to the same byte-identical string.
"""

from __future__ import annotations

import re
from typing import Iterable

from api.services.style_engine.rules import StyleRules

# Matches a single alphabetic character -- used to find and capitalize the
# first letter of the down-styled result.
_FIRST_ALPHA_RE = re.compile(r"[A-Za-z]")


def build_canonical(rules: StyleRules, extra_nouns: Iterable[str] = ()) -> dict[str, str]:
    """Build the lowercase-term -> canonical-cased-form restoration map.

    Starts from ``rules.canonical_seed()`` (proper nouns, acronyms, casing
    variants) and merges in ``extra_nouns`` -- per-job terms, e.g. an
    episode's speaker names extracted by
    :func:`api.services.style_engine.entities.extract_proper_nouns`.
    ``extra_nouns`` win on key collision: they represent that specific
    episode's ground truth and should override any stale/generic seed
    entry sharing the same lowercase key.
    """
    canonical = dict(rules.canonical_seed())
    for noun in extra_nouns:
        canonical[noun.lower()] = noun
    return canonical


def to_down_style(text: str, canonical: dict[str, str]) -> str:
    """Down-style ``text``: lowercase everything, then restore canonical casing.

    Canonical terms are restored longest-first so multi-word terms (e.g.
    "wisconsin supreme court") win over shorter terms they contain (e.g.
    "wisconsin") -- if the shorter term won first, the compound term would
    no longer case-match on its later, losing pass. Restoration is
    word-boundary aware (``\\b...\\b`` around each escaped term) so
    substrings inside longer words are never touched. Finally, the first
    alphabetic character of the result is uppercased.

    Idempotent for text produced by this function, given the same
    canonical map: a second pass is a no-op as long as no canonical value
    introduces trailing punctuation that could re-trigger a shorter
    canonical key on the next pass (e.g. chaining an abbreviation-style
    casing_variant like "gov" -> "Gov." back through the function a second
    time is not guaranteed idempotent -- callers should apply this once
    per generation, which is how the pipeline uses it).
    """
    result = text.lower()
    for lowered, cased in sorted(canonical.items(), key=lambda kv: -len(kv[0])):
        if not lowered:
            continue
        result = re.sub(rf"\b{re.escape(lowered)}\b", cased, result)

    match = _FIRST_ALPHA_RE.search(result)
    if match and result[match.start()].islower():
        i = match.start()
        result = result[:i] + result[i].upper() + result[i + 1 :]
    return result
