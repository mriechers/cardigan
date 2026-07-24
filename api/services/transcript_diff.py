"""Mine glossary corrections from a transcript review's edits.

Deterministic (no LLM): word-level diff between each raw segment and its
edited counterpart, keeping short replacements that look like proper-noun
fixes. Feeds the same glossary the WhisperX initial_prompt reads, so a
correction made once improves every future transcription.
"""

import difflib
import re
from typing import Dict, Iterable, List, Optional, Tuple

# Replacements longer than this many words are rewordings, not corrections.
MAX_REPLACEMENT_WORDS = 3
# A pair must recur this often to be accepted — unless the corrected form is
# already a known intake term (speaker name / context term), which is strong
# evidence on its own.
MIN_OCCURRENCES = 2

_WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)


def _tokenize(text: str) -> List[str]:
    return _WORD_RE.findall(text or "")


def _has_capitalized_token(words: Iterable[str]) -> bool:
    return any(w[:1].isupper() for w in words)


def mine_corrections(
    segment_pairs: List[Tuple[str, str]],
    known_terms: Optional[Iterable[str]] = None,
) -> List[Tuple[str, str, str]]:
    """Extract (correct, wrong, context) glossary entries from edit pairs.

    Args:
        segment_pairs: (raw_text, edited_text) per changed segment.
        known_terms: intake speakers/terms; a replacement matching one of
            these is accepted on first occurrence.

    Returns:
        Deduplicated entries ordered by first occurrence, ready for
        glossary.add_corrections().
    """
    known_lower = {t.lower().strip() for t in (known_terms or []) if t}

    counts: Dict[Tuple[str, str], int] = {}
    order: List[Tuple[str, str]] = []

    for raw_text, edited_text in segment_pairs:
        if raw_text == edited_text:
            continue
        raw_words = _tokenize(raw_text)
        edited_words = _tokenize(edited_text)
        matcher = difflib.SequenceMatcher(a=[w.lower() for w in raw_words], b=[w.lower() for w in edited_words])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "replace":
                continue
            if (i2 - i1) > MAX_REPLACEMENT_WORDS or (j2 - j1) > MAX_REPLACEMENT_WORDS:
                continue
            wrong = " ".join(raw_words[i1:i2])
            correct = " ".join(edited_words[j1:j2])
            if not wrong or not correct or wrong.lower() == correct.lower():
                continue
            # Only proper-noun-looking fixes: the replacement carries a
            # capitalized token or matches a known intake term.
            if not _has_capitalized_token(edited_words[j1:j2]) and correct.lower() not in known_lower:
                continue
            key = (correct, wrong)
            if key not in counts:
                order.append(key)
            counts[key] = counts.get(key, 0) + 1

    entries: List[Tuple[str, str, str]] = []
    for correct, wrong in order:
        occurrences = counts[(correct, wrong)]
        if occurrences >= MIN_OCCURRENCES or correct.lower() in known_lower:
            entries.append((correct, wrong, "Transcript review correction"))
    return entries
