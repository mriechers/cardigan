"""Shared read/write helpers for knowledge/glossary.md.

The glossary is a single markdown file with two kinds of content:

- Correction tables (Place Names, Editor Corrections, ...) consumed verbatim
  by the analyst/formatter LLM prompts.
- A ``## Whisper Prompt Terms`` bullet list merged into the WhisperX
  ``initial_prompt`` for audio transcription jobs. Only ``- `` bullet lines
  in that section are injected; prose stays out of prompts.

Both the API and worker containers write here through the shared
``./knowledge`` bind mount. Writes are rare, append-only, and performed as a
read-modify-write inside a single function call; a concurrent write from the
other container could theoretically drop an append, which we accept for a
human-paced editorial tool.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

WHISPER_TERMS_HEADING = "## Whisper Prompt Terms"
EDITOR_CORRECTIONS_HEADING = "## Editor Corrections"
NAME_DISAMBIGUATION_HEADING = "## Name Disambiguation"

_TABLE_SEPARATOR_RE = re.compile(r"^\|[\s\-:|]+\|$")


def get_glossary_path() -> Path:
    """Resolve the glossary path from KNOWLEDGE_DIR (read per call for tests)."""
    return Path(os.getenv("KNOWLEDGE_DIR", "knowledge")) / "glossary.md"


def _read(path: Optional[Path]) -> Tuple[Path, str]:
    resolved = path or get_glossary_path()
    if not resolved.exists():
        return resolved, ""
    return resolved, resolved.read_text()


def _section_bounds(lines: List[str], heading: str) -> Optional[Tuple[int, int]]:
    """Return (start, end) line indexes of a section's body, exclusive of heading.

    ``end`` is the index of the next ``## `` heading (or EOF).
    """
    start = None
    for i, line in enumerate(lines):
        if line.strip() == heading:
            start = i + 1
            break
    if start is None:
        return None
    for j in range(start, len(lines)):
        if lines[j].startswith("## "):
            return start, j
    return start, len(lines)


def get_whisper_terms(path: Optional[Path] = None) -> List[str]:
    """Return the bullet terms under ``## Whisper Prompt Terms``, in file order."""
    _, text = _read(path)
    if not text:
        return []
    lines = text.split("\n")
    bounds = _section_bounds(lines, WHISPER_TERMS_HEADING)
    if bounds is None:
        return []
    start, end = bounds
    terms = []
    for line in lines[start:end]:
        stripped = line.strip()
        if stripped.startswith("- "):
            term = stripped[2:].strip()
            if term:
                terms.append(term)
    return terms


def _table_first_cells(text: str) -> List[str]:
    """First-column values from every table body row in the file.

    These are known-correct terms (place names, people, programs) used for
    case-insensitive dedupe when adding whisper terms.
    """
    cells = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if _TABLE_SEPARATOR_RE.match(stripped):
            continue
        first = stripped.strip("|").split("|")[0].strip()
        # Skip header rows by their well-known labels
        if first in {"Correct", "Term", "Name", "Program", "Correct "} or not first:
            continue
        cells.append(first)
    return cells


def add_whisper_terms(terms: List[str], path: Optional[Path] = None) -> int:
    """Append new terms to the Whisper Prompt Terms section.

    Deduplicates case-insensitively against existing bullets AND the correct
    forms already present in the glossary tables. Creates the section if the
    file lacks one. Returns the number of terms actually added.
    """
    resolved, text = _read(path)
    if not text:
        logger.warning("Glossary file missing at %s; cannot add whisper terms", resolved)
        return 0

    known = {t.lower() for t in get_whisper_terms(resolved)}
    known.update(c.lower() for c in _table_first_cells(text))

    new_terms = []
    for term in terms:
        cleaned = " ".join(term.split())
        if cleaned and cleaned.lower() not in known:
            new_terms.append(cleaned)
            known.add(cleaned.lower())

    if not new_terms:
        return 0

    lines = text.split("\n")
    bounds = _section_bounds(lines, WHISPER_TERMS_HEADING)
    if bounds is None:
        # Append a fresh section at EOF
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(WHISPER_TERMS_HEADING)
        lines.append("")
        lines.append("Terms merged into the WhisperX initial_prompt for audio jobs.")
        lines.append("Only `- ` bullet lines are injected into prompts.")
        lines.append("")
        insert_at = len(lines)
    else:
        start, end = bounds
        # Insert after the last bullet (or at section end), before trailing blanks
        insert_at = end
        while insert_at > start and not lines[insert_at - 1].strip():
            insert_at -= 1

    for offset, term in enumerate(new_terms):
        lines.insert(insert_at + offset, f"- {term}")

    resolved.write_text("\n".join(lines))
    logger.info("Added %d whisper prompt terms: %s", len(new_terms), ", ".join(new_terms))
    return len(new_terms)


def add_corrections(entries: List[Tuple[str, str, str]], path: Optional[Path] = None) -> int:
    """Append (correct, wrong, context) rows to the Editor Corrections table.

    Skips entries whose wrong AND correct forms already appear anywhere in the
    glossary. Each accepted correct form is also added to the Whisper Prompt
    Terms section so future transcriptions hear it. Returns rows added.
    """
    resolved, text = _read(path)
    if not text:
        logger.warning("Glossary file missing at %s; cannot add corrections", resolved)
        return 0

    lower_text = text.lower()
    new_entries = []
    for correct, wrong, context_note in entries:
        correct = " ".join(correct.split())
        wrong = " ".join(wrong.split())
        if not correct or not wrong:
            continue
        if wrong.lower() in lower_text and correct.lower() in lower_text:
            continue
        new_entries.append((correct, wrong, context_note.strip()))

    if not new_entries:
        return 0

    # Add correct forms to Whisper Prompt Terms first — once the table row
    # exists, the term would be deduped against its own Correct cell.
    add_whisper_terms([correct for correct, _, _ in new_entries], resolved)

    _, text = _read(resolved)
    lines = text.split("\n")
    insert_idx = None
    for i, line in enumerate(lines):
        if line.strip() == EDITOR_CORRECTIONS_HEADING:
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("## "):
                    insert_idx = j
                    break
                if lines[j].startswith("|") and not lines[j].startswith("| Correct"):
                    insert_idx = j + 1
            if insert_idx is None:
                insert_idx = len(lines)
            break
        if line.strip() == NAME_DISAMBIGUATION_HEADING:
            insert_idx = i
            break

    if insert_idx is None:
        insert_idx = len(lines)

    new_lines = [f"| {correct} | {wrong} | {context_note} |" for correct, wrong, context_note in new_entries]
    for offset, new_line in enumerate(new_lines):
        lines.insert(insert_idx + offset, new_line)

    resolved.write_text("\n".join(lines))
    logger.info(
        "Appended %d glossary corrections: %s",
        len(new_entries),
        "; ".join(f"{w} -> {c}" for c, w, _ in new_entries),
    )

    return len(new_entries)


def read_glossary_summary(path: Optional[Path] = None) -> Dict[str, Any]:
    """Summary for the API: whisper terms plus correction-table row count."""
    resolved, text = _read(path)
    whisper_terms = get_whisper_terms(resolved) if text else []

    correction_count = 0
    if text:
        lines = text.split("\n")
        bounds = _section_bounds(lines, EDITOR_CORRECTIONS_HEADING)
        if bounds is not None:
            start, end = bounds
            for line in lines[start:end]:
                stripped = line.strip()
                if (
                    stripped.startswith("|")
                    and not _TABLE_SEPARATOR_RE.match(stripped)
                    and not stripped.startswith("| Correct")
                ):
                    correction_count += 1

    return {
        "whisper_terms": whisper_terms,
        "whisper_term_count": len(whisper_terms),
        "correction_count": correction_count,
    }
