"""QA-failure escalation + shared pause-and-suggest terminal handling (Spec B)."""

from __future__ import annotations

FAMILY_ORDER = ["haiku", "sonnet", "opus"]


def parse_model_family(model_slug: str | None) -> str | None:
    """Return 'haiku' | 'sonnet' | 'opus' parsed from a model slug, else None.

    Robust to OpenRouter's mixed word order (claude-4.6-sonnet vs claude-sonnet-4.6).
    """
    if not model_slug:
        return None
    s = model_slug.lower()
    for family in FAMILY_ORDER:
        if family in s:
            return family
    return None


def bump_family(family: str | None) -> str | None:
    """Return the next-stronger family, or None if already opus / unknown."""
    if family not in FAMILY_ORDER:
        return None
    idx = FAMILY_ORDER.index(family)
    return FAMILY_ORDER[idx + 1] if idx + 1 < len(FAMILY_ORDER) else None
