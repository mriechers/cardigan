"""Formatter enforce-tier text primitives: word-boundary substitutions and
speaker-turn whitespace normalization.

Pure stdlib -- no worker/DB/async/FastAPI imports. These are the "never eat
content" primitives the post-generation formatter stage composes: unlike
``casing.to_down_style`` (a whole-field down-style rewrite used by the ``seo``
phase), these operate on full dialogue documents and are deliberately narrow
in what they're allowed to touch --

- :func:`apply_substitutions` applies ``phases.formatter.substitutions``
  "enforce"-tier entries (regex ``find`` / ``replace`` pairs, e.g. AP-style
  honorific abbreviations, the "okay"->"OK" house-style fix) as literal
  word-boundary regex substitutions -- never a general down-style/casing pass
  over dialogue. Two guards keep it from corrupting content it shouldn't
  touch:

  1. **Fenced code blocks and URLs are never rewritten.** A match whose span
     falls inside a ``` ``` fence or a ``http(s)://`` URL is left as-is.
  2. **Sentence-initial guard for pure case-fold substitutions.** An entry
     whose ``replace`` is exactly the lowercased form of what it matched
     (e.g. ``Liberals`` -> ``liberals``) is a down-casing fix, not a spelling
     fix -- and normal English capitalizes the first word of a sentence
     regardless of house style on that word elsewhere. This guard is
     detected generically by comparing the match text to the expanded
     replacement at match time; it is never keyed off specific words, so any
     future down-casing entry gets the same protection for free. Entries
     whose replacement is NOT a pure case-fold (an abbreviation, a spelling
     fix, an italics-stripping backreference) are not case-fold matches and
     always apply, sentence-initial or not -- AP style allows "Sen. Smith
     spoke first," to open a sentence.

  Flag-tier entries (``detect`` instead of ``find``/``replace``) are
  detection-only by construction elsewhere in the engine; this module simply
  never treats a ``detect``-shaped entry as a rewrite pair.

- :func:`normalize_speaker_turns` is whitespace-only: it normalizes the
  trailing-space count on speaker-label lines (the markdown hard-break
  convention, e.g. "``**Name:**``" + 2 trailing spaces) and the blank-line
  count between two speaker turns. It never touches dialogue content, and it
  never touches the whitespace preceding the *first* speaker label (that is
  document-header spacing, not a between-turns gap).

Both primitives are idempotent by construction: down-casing substitutions
are case-sensitive on the over-capitalized form only (so a second pass over
already-lowercased text is a no-op), and once whitespace matches its target
count, the "collapse to N" substitution is naturally a no-op the second time.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from api.services.style_engine.types import AppliedFix

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_URL_RE = re.compile(r"https?://\S+")

# Mirrors lint.py's _body_region -- the formatter document template is
# header / "---" / body (speaker turns) / "---" / "**Status:**" footer, and
# several header/footer fields share speaker-labels' "**Two Words:**"
# bold-colon shape (e.g. "**Date Processed:**" is two capitalized words,
# same as a first+last name). Scoping speaker-turn normalization to
# strictly between the first and last horizontal rule (when both are
# present) keeps it from treating those metadata fields as speaker turns.
_HR_LINE_RE = re.compile(r"^-{3,}[ \t]*$", re.MULTILINE)

_SENTENCE_TERMINAL_CHARS = ".!?\"'”’"


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Spans of ``text`` that a substitution match must never overlap."""
    spans = [m.span() for m in _FENCE_RE.finditer(text)]
    spans += [m.span() for m in _URL_RE.finditer(text)]
    return spans


def _overlaps(span: tuple[int, int], protected: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < p_end and end > p_start for p_start, p_end in protected)


def _is_sentence_initial(text: str, pos: int) -> bool:
    """Whether position ``pos`` in ``text`` starts a new sentence or line.

    Walks backward over spaces/tabs; ``True`` at start-of-text, immediately
    after a newline (a new dialogue/paragraph line), or immediately after
    sentence-terminal punctuation (optionally followed by a closing quote).

    Note: A quote/apostrophe character only counts as sentence-terminal when it
    immediately follows a real terminal punctuation character (.!?), not when
    appearing bare (e.g., possessive 's' in "workers'" is not sentence-terminal).
    """
    i = pos
    while i > 0 and text[i - 1] in " \t":
        i -= 1
    if i == 0:
        return True
    if text[i - 1] == "\n":
        return True
    # Check for real terminal punctuation (.!?) directly preceding
    if text[i - 1] in ".!?":
        return True
    # Check for quote/apostrophe following terminal punctuation
    # (e.g., closing quote after "Stop!" should count as sentence-terminal)
    # Quotes only count as sentence-terminal when they follow .!?
    if text[i - 1] in _SENTENCE_TERMINAL_CHARS[3:] and i >= 2 and text[i - 2] in ".!?":
        return True
    return False


def _is_rewrite_entry(sub: Mapping[str, Any]) -> bool:
    """Whether ``sub`` is an enforce-tier find/replace rewrite pair.

    Flag-tier entries use ``detect`` instead of ``find``/``replace`` and are
    never rewrite pairs -- this is a structural check (presence of the
    right keys), not a check of the ``tier`` value, so a synthetic test
    fixture that omits ``tier`` still behaves correctly.
    """
    return bool(sub.get("find")) and sub.get("replace") is not None


def _substitute_one(
    text: str, find: str, replace: str
) -> tuple[str, int, str | None, str | None]:
    """Apply one find/replace pair to ``text`` with the fence/URL + sentence-
    initial guards. Returns ``(new_text, applied_count, first_before,
    first_after)`` -- the latter two are ``None`` when nothing was applied.
    """
    protected = _protected_spans(text)
    pattern = re.compile(find)

    applied_count = 0
    first_before: str | None = None
    first_after: str | None = None

    def _repl(match: re.Match) -> str:
        nonlocal applied_count, first_before, first_after

        if _overlaps(match.span(), protected):
            return match.group(0)

        matched_text = match.group(0)
        expanded = match.expand(replace)

        if expanded == matched_text:
            return expanded

        is_case_fold_only = expanded == matched_text.lower()
        if is_case_fold_only and _is_sentence_initial(text, match.start()):
            return matched_text

        applied_count += 1
        if first_before is None:
            first_before = matched_text
            first_after = expanded
        return expanded

    new_text = pattern.sub(_repl, text)
    return new_text, applied_count, first_before, first_after


def apply_substitutions(text: str, substitutions: Sequence[Mapping[str, Any]]) -> str:
    """Apply enforce-tier ``find``/``replace`` pairs to ``text`` in order.

    ``substitutions`` is ``StyleRules.substitutions(tier="enforce")``-shaped:
    each entry has ``find`` (a regex pattern) and ``replace`` (a
    replacement string, backreferences like ``\\1`` supported). Entries
    missing either key (including flag-tier ``detect``-only entries) are
    silently skipped -- never raised on. Guarded so matches inside fenced
    code blocks, inside URLs, or (for pure case-fold entries only)
    sentence-initial are left untouched. Never mutates/truncates content
    otherwise -- every match either becomes its exact expansion or is left
    exactly as matched.
    """
    if not text:
        return text
    result = text
    for sub in substitutions:
        if not _is_rewrite_entry(sub):
            continue
        result, _, _, _ = _substitute_one(result, sub["find"], sub["replace"])
    return result


def _rule_id_for(sub: Mapping[str, Any]) -> str:
    identifier = sub.get("id") or sub.get("replace") or sub.get("find") or "substitution"
    slug = re.sub(r"[^a-z0-9]+", "_", str(identifier).lower()).strip("_") or "substitution"
    return f"formatter.substitution.{slug}"


def apply_substitutions_with_fixes(
    text: str, substitutions: Sequence[Mapping[str, Any]]
) -> tuple[str, list[AppliedFix]]:
    """Wrapper around :func:`apply_substitutions` that also reports per-pair
    :class:`AppliedFix` entries (rule_id, one representative before/after
    excerpt, and the total match count for that pair). A new wrapper
    function rather than a second return-shape on ``apply_substitutions``
    itself, so any caller wanting only the normalized text keeps the plain
    ``str`` signature.
    """
    if not text:
        return text, []

    result = text
    fixes: list[AppliedFix] = []
    for sub in substitutions:
        if not _is_rewrite_entry(sub):
            continue
        result, count, before, after = _substitute_one(result, sub["find"], sub["replace"])
        if count:
            fixes.append(
                AppliedFix(rule_id=_rule_id_for(sub), before=before or "", after=after or "", count=count)
            )
    return result, fixes


# ---------------------------------------------------------------------------
# normalize_speaker_turns -- whitespace-only
# ---------------------------------------------------------------------------


def normalize_speaker_turns(text: str, speaker_label_spec: Mapping[str, Any]) -> str:
    """Normalize speaker-label trailing-space count and blank-line count
    between speaker turns. Whitespace-only -- never touches dialogue
    content, markdown markers, or anything else about the document.

    ``speaker_label_spec`` (``phases.formatter.speaker_label``-shaped) keys
    read, all optional:
      pattern -- regex matching a speaker-label line's ``**Name:**`` token
        (anchored at line-start, e.g. ``^\\*\\*[A-Z]...:\\*\\*``). Missing
        -> no-op (nothing to find turns with).
      trailing_spaces -- exact space count required after a label token,
        before the line's newline. Missing -> that dimension untouched.
      blank_lines_between_turns -- exact blank-line count required between
        the end of one speaker's content and the next label line. Missing
        -> that dimension untouched. Only gaps BETWEEN two label matches are
        touched -- the whitespace preceding the very first label (document-
        header spacing) is left alone.

    ``no_honorifics`` is read elsewhere (prompt rendering, lint) -- it does
    not describe a whitespace property and has no effect here.
    """
    pattern = speaker_label_spec.get("pattern")
    if not text or not pattern:
        return text

    label_re = re.compile(pattern, re.MULTILINE)

    body_start, body_end = _body_region_span(text)
    head, body, tail = text[:body_start], text[body_start:body_end], text[body_end:]

    trailing_spaces = speaker_label_spec.get("trailing_spaces")
    if trailing_spaces is not None:
        body = _normalize_label_trailing_spaces(body, pattern, trailing_spaces)

    blank_lines = speaker_label_spec.get("blank_lines_between_turns")
    if blank_lines is not None:
        body = _normalize_blank_lines_between_turns(body, label_re, blank_lines)

    return head + body + tail


def _body_region_span(text: str) -> tuple[int, int]:
    """Span strictly between the first and last horizontal-rule lines.

    Falls back to ``(0, len(text))`` (the whole string) when fewer than two
    rules are found -- e.g. a bare speaker-turns-only snippet with no
    document header/footer.
    """
    matches = list(_HR_LINE_RE.finditer(text))
    if len(matches) >= 2:
        return matches[0].end(), matches[-1].start()
    return 0, len(text)


def _normalize_label_trailing_spaces(text: str, pattern: str, trailing_spaces: int) -> str:
    # ``pattern`` already anchors at line-start (^...); requiring the line to
    # END right after the label token (only trailing spaces/tabs before the
    # newline) scopes this to lines that ARE just the label -- a malformed
    # line with dialogue trailing the label on the same line is left alone.
    line_label_re = re.compile(rf"(?:{pattern})[ \t]*$", re.MULTILINE)

    def _fix(match: re.Match) -> str:
        label_end = match.start() + len(match.group(0).rstrip(" \t"))
        return match.group(0)[: label_end - match.start()] + " " * trailing_spaces

    return line_label_re.sub(_fix, text)


def _normalize_blank_lines_between_turns(text: str, label_re: re.Pattern, blank_lines: int) -> str:
    matches = list(label_re.finditer(text))
    if len(matches) < 2:
        return text

    result = text
    # Walk backward through label occurrences (skipping the first) so
    # earlier offsets are never invalidated by a length-changing splice.
    for match in reversed(matches[1:]):
        start = match.start()
        gap_start = start
        while gap_start > 0 and result[gap_start - 1] in "\n \t\r":
            gap_start -= 1
        replacement = "\n" * (blank_lines + 1)
        result = result[:gap_start] + replacement + result[start:]

    return result
