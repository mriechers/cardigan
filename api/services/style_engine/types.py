"""Core dataclasses for the style_engine rule engine.

Pure data types — no I/O, no dependencies beyond dataclasses/typing. These
represent a single rule violation, a deterministic fix applied to model
output, and the results of the per-phase pre/post pipeline stages that later
tasks (scanner, limits checker, prompt renderer) will produce and consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# AppliedFix.before/after are stored in full but capped to this length when
# serialized for audit events, so a long substitution doesn't bloat logs.
_EXCERPT_CAP = 200


@dataclass
class RuleViolation:
    """A single house-style rule violation surfaced during a phase check."""

    rule_id: str  # e.g. "limits.short_description.max", "voice.forbidden.cta"
    phase: str
    severity: str  # "error" | "warning"
    message: str
    field: str | None = None
    span: tuple[int, int] | None = None
    model_fixable: bool = True

    def to_flag_text(self) -> str:
        """Render as an inline flag string embedded in review/audit output."""
        prefix = "style" if self.model_fixable else "style-nonfixable"
        return f"[{prefix}:{self.rule_id}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "phase": self.phase,
            "severity": self.severity,
            "message": self.message,
            "field": self.field,
            "span": list(self.span) if self.span is not None else None,
            "model_fixable": self.model_fixable,
        }


@dataclass
class AppliedFix:
    """A deterministic substitution/fix applied to model output."""

    rule_id: str
    before: str  # capped excerpt for audit events — cap at 200 chars in to_dict
    after: str  # capped likewise
    count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "before": self.before[:_EXCERPT_CAP],
            "after": self.after[:_EXCERPT_CAP],
            "count": self.count,
        }


@dataclass
class PhaseCheckResult:
    """Result of running the deterministic rule checks for one pipeline phase."""

    phase: str
    violations: list[RuleViolation] = field(default_factory=list)
    fixes: list[AppliedFix] = field(default_factory=list)
    parse_ok: bool = True
    skipped: bool = False

    @property
    def error_flags(self) -> list[RuleViolation]:
        """Violations with severity == 'error'."""
        return [v for v in self.violations if v.severity == "error"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "violations": [v.to_dict() for v in self.violations],
            "fixes": [f.to_dict() for f in self.fixes],
            "parse_ok": self.parse_ok,
            "skipped": self.skipped,
        }


@dataclass
class PreStageResult:
    """Result of the pre-generation stage: rendered prompt section + raw data."""

    phase: str
    prompt_section: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "prompt_section": self.prompt_section,
            "data": self.data,
        }


@dataclass
class PostStageResult:
    """Result of the post-generation stage: normalized output + its check result."""

    phase: str
    normalized_output: str
    changed: bool
    check: PhaseCheckResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "normalized_output": self.normalized_output,
            "changed": self.changed,
            "check": self.check.to_dict(),
        }
