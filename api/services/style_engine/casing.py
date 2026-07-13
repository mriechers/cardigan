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
from collections.abc import Iterable

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

    Idempotence guarantee: for every (key, value) pair, this also registers
    ``value.lower() -> value`` as an additional match alternative whenever
    it differs from ``key`` (proper nouns and acronyms already satisfy
    ``value.lower() == key``, so those are no-ops). This is what makes
    already-canonical text a fixed point of :func:`to_down_style`: a
    casing_variant like "atty gen" -> "Atty. Gen." injects a period *inside*
    the multi-word term, so the value's own lowercase form ("atty. gen.")
    no longer matches the "atty gen" key pattern on a second pass -- without
    this mirrored registration, re-applying the down-style pass would lose
    the restored casing instead of leaving it untouched.
    """
    canonical = dict(rules.canonical_seed())
    for noun in extra_nouns:
        canonical[noun.lower()] = noun
    for lowered, cased in list(canonical.items()):
        value_lower = cased.lower()
        if value_lower != lowered:
            canonical.setdefault(value_lower, cased)
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
    canonical map: a second pass is always a no-op -- including for
    period-terminated casing_variants (e.g. "gov" -> "Gov.") and
    internally-punctuated multi-word ones (e.g. "atty gen" ->
    "Atty. Gen."). This holds by construction: ``build_canonical`` also
    registers each canonical value's own lowercase form as a match
    alternative mapping back to that value, so text already carrying a
    restored value (from a prior pass, or because the source was already
    house-styled) is guaranteed to have a matching key on this pass too --
    it is never silently un-cased just because the value injected internal
    punctuation the original key pattern didn't account for.
    """
    result = text.lower()
    for lowered, cased in sorted(canonical.items(), key=lambda kv: -len(kv[0])):
        if not lowered:
            continue
        # A canonical key that itself ends in "." (e.g. the mirrored
        # "atty. gen." key build_canonical registers for "atty gen" ->
        # "Atty. Gen.") strips that one trailing period from the match
        # base -- it's re-added below as an optional trailing match so the
        # substitution never doubles it. Any *internal* period (e.g. the
        # one after "atty") stays in the base as literal matched text.
        base = lowered[:-1] if lowered.endswith(".") else lowered
        # Period-terminated canonical values/keys (e.g. "gov" -> "Gov.", or
        # the mirrored "atty. gen." key) must not double their period when
        # the source text already carries one -- consume one optional
        # pre-existing trailing period so the substitution always yields
        # exactly one, regardless of source.
        trailing_period = r"\.?" if lowered.endswith(".") or cased.endswith(".") else ""
        result = re.sub(rf"\b{re.escape(base)}\b{trailing_period}", cased, result)

    match = _FIRST_ALPHA_RE.search(result)
    if match and result[match.start()].islower():
        i = match.start()
        result = result[:i] + result[i].upper() + result[i + 1 :]
    return result
