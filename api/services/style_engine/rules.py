"""YAML-backed house-style rule loader.

Pure stdlib + PyYAML — no DB, no async, no FastAPI imports, no I/O beyond
reading the YAML file path given to ``load_rules``. Loads
``config/house_style.yaml`` (or any given path) into a validated
``StyleRules`` object exposing typed accessors for the deterministic rule
engine (scanner, limits checker, prompt renderer — later tasks). The loader
mtime-caches by resolved path so repeated calls within a request avoid
re-parsing the YAML, while an edited file (mtime moved forward) is picked up
on the next call.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_RULES_PATH = Path("config/house_style.yaml")

# path (resolved) -> (mtime at load time, StyleRules instance)
_cache: dict[Path, tuple[float, "StyleRules"]] = {}


class StyleRulesError(Exception):
    """Raised when the house-style rules file is missing or invalid."""


def load_rules(path: str | Path = DEFAULT_RULES_PATH) -> "StyleRules":
    """Load and validate the YAML rule file at ``path``.

    mtime-cached: repeated calls with the same path return the same
    StyleRules object until the file's mtime changes.

    Raises:
        StyleRulesError: missing file, YAML parse error, non-dict root, or a
            missing top-level ``meta`` section.
    """
    resolved = Path(path).resolve()

    try:
        mtime = resolved.stat().st_mtime
    except OSError as exc:
        raise StyleRulesError(f"House style rules file not found: {resolved}") from exc

    cached = _cache.get(resolved)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        text = resolved.read_text()
    except OSError as exc:
        raise StyleRulesError(f"Could not read house style rules file {resolved}: {exc}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise StyleRulesError(f"Invalid YAML in house style rules file {resolved}: {exc}") from exc

    if not isinstance(data, dict):
        raise StyleRulesError(
            f"House style rules file {resolved} must have a mapping (dict) at its root, " f"got {type(data).__name__}"
        )

    if "meta" not in data:
        raise StyleRulesError(f"House style rules file {resolved} is missing required top-level 'meta' section")

    rules = StyleRules(raw=data)
    _cache[resolved] = (mtime, rules)
    return rules


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` onto ``base`` without mutating either."""
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


@dataclass
class StyleRules:
    """Typed accessors over a validated house-style rule document."""

    raw: dict

    def limits_for(self, program: str | None = None, content_type: str = "full") -> dict[str, dict]:
        """Per-field limit dicts from limits.fields, deep-merged with
        limits.content_type_overrides[content_type] when present. ``program``
        is accepted for future program-level overrides (no-op merge today).
        """
        limits = self.raw.get("limits", {}) or {}
        base = limits.get("fields", {}) or {}
        overrides = limits.get("content_type_overrides", {}) or {}
        override = overrides.get(content_type) or {}
        return _deep_merge(base, override)

    def substitutions(self, tier: str | None = None) -> list[dict]:
        """phases.formatter.substitutions, optionally filtered by tier."""
        phases = self.raw.get("phases", {}) or {}
        formatter = phases.get("formatter", {}) or {}
        subs = formatter.get("substitutions", []) or []
        if tier is None:
            return list(subs)
        return [sub for sub in subs if sub.get("tier") == tier]

    def forbidden(self) -> list[dict]:
        """voice.forbidden_phrases (each: match, category, tier, severity, optional regex)."""
        voice = self.raw.get("voice", {}) or {}
        return list(voice.get("forbidden_phrases", []) or [])

    def first_person_markers(self) -> list[str]:
        voice = self.raw.get("voice", {}) or {}
        return list(voice.get("first_person_markers", []) or [])

    def second_person_markers(self) -> list[str]:
        voice = self.raw.get("voice", {}) or {}
        return list(voice.get("second_person_markers", []) or [])

    def canonical_seed(self) -> dict[str, str]:
        """lowercased term -> canonical form, built from casing.proper_nouns
        (term.lower() -> term), casing.acronyms (acronym.lower() -> acronym),
        casing.casing_variants (key.lower() -> value). Multi-word entries are
        included as-is (lowercased key).
        """
        casing = self.raw.get("casing", {}) or {}
        seed: dict[str, str] = {}
        for term in casing.get("proper_nouns", []) or []:
            seed[term.lower()] = term
        for acronym in casing.get("acronyms", []) or []:
            seed[acronym.lower()] = acronym
        for key, value in (casing.get("casing_variants", {}) or {}).items():
            seed[key.lower()] = value
        return seed

    def surname_stoplist(self) -> set[str]:
        casing = self.raw.get("casing", {}) or {}
        return set(casing.get("surname_stoplist", []) or [])

    def chapter_max(self, duration_min: float) -> int:
        """First entry in phases.timestamp.chapter_max_by_duration whose `lt`
        is null or duration_min < lt; return its `max`. Entries are ordered
        ascending; `lt: null` (None) is the catch-all.
        """
        phases = self.raw.get("phases", {}) or {}
        timestamp = phases.get("timestamp", {}) or {}
        entries = timestamp.get("chapter_max_by_duration", []) or []
        for entry in entries:
            lt = entry.get("lt")
            if lt is None or duration_min < lt:
                return entry["max"]
        raise StyleRulesError("phases.timestamp.chapter_max_by_duration has no catch-all entry (lt: null)")

    def program_rules(self, program: str | None) -> dict:
        """programs[program] or {} — exact key match."""
        if program is None:
            return {}
        programs = self.raw.get("programs", {}) or {}
        return programs.get(program, {}) or {}
