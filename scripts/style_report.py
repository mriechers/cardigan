#!/usr/bin/env python3
"""Task 6b -- rule-update feedback-loop aggregator (v1).

Reads ``style_violation`` and ``editor_correction`` events out of the
``session_stats`` table and renders a markdown report: violation counts by
rule/phase, recurring editor-correction patterns, house-style entries that
never fired in the window (retirement candidates), and heuristic PROPOSED
``config/house_style.yaml`` diffs backed by evidence counts.

HARD INVARIANT: this script NEVER writes to ``config/house_style.yaml`` (or
any rules file) and has no code path that could. Every "proposed edit" is a
rendered YAML snippet printed in the markdown report for a human to copy
into a hand-authored PR -- proposals only, always requiring editor review.
See docs/STYLE_FEEDBACK_LOOP.md for the full review workflow.

Usage:
    python -m scripts.style_report [--db PATH] [--since YYYY-MM-DD] [--until YYYY-MM-DD]
        [--app-version vX.Y] [--rules-file PATH] [--out FILE]

DB access: plain ``sqlite3`` against the same SQLite file the API/worker
write to (default: ``$DATABASE_PATH`` or ``dashboard.db`` at the repo
root) -- matching ``scripts/backfill_transcript_metrics.py``'s established
precedent for one-shot CLI scripts (plain ``sqlite3``, no
``api.services.database`` import), rather than ``api.services.database``'s
async SQLAlchemy session machinery, which is built for the live FastAPI
app's request lifecycle. (``scripts/backfill_v21_data.py`` is NOT a
precedent for this: it also reads its *source* snapshot DB with raw
``sqlite3``, but it imports ``api.services.database``/SQLAlchemy to write
the live DB, so it mixes both approaches rather than avoiding ``api.*``.
``scripts/eval_compare.py`` is the honest precedent for this script's
overall "stay out of ``api.*``, plain stdlib" stance -- see the rules-file
paragraph below.) Read-only: no INSERT/UPDATE/DELETE anywhere in this
module.

Rules file access: plain ``yaml.safe_load`` (not
``api.services.style_engine.rules.load_rules``) -- mirrors
``scripts/eval_compare.py``'s "no style_engine import" precedent, so this
script works standalone against a house-style YAML file without needing the
rest of the app importable.

Pure aggregation/clustering logic lives in module-level functions with no
I/O (tested in tests/test_style_report.py with synthetic event-row dicts);
DB fetch + rules-file load + CLI plumbing are isolated in
``fetch_events``/``main`` and not unit-tested.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

DEFAULT_DB_PATH = Path(os.environ.get("DATABASE_PATH") or (Path(__file__).parent.parent / "dashboard.db"))
DEFAULT_RULES_PATH = Path("config/house_style.yaml")
EVENT_TYPES = ("style_violation", "editor_correction")

PROPOSAL_THRESHOLD = 3  # correction-cluster count required to emit a proposed YAML edit
_EXAMPLE_CAP = 3  # max provenance examples kept per correction cluster

PROPOSAL_BANNER = (
    "> **PROPOSAL -- requires editor review and a PR; never auto-applied.** "
    "This script cannot and does not write to `config/house_style.yaml`."
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _as_dict(value: object) -> dict:
    """Tolerate ``data``/``extra`` arriving as an already-parsed dict (the
    shape ``fetch_events`` produces) or as a raw JSON string (the shape a
    caller might pass when feeding rows straight from a DB cursor). Anything
    else -- ``None``, malformed JSON, a non-dict JSON value -- degrades to
    ``{}`` rather than raising."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


# ---------------------------------------------------------------------------
# Section (a) -- violations summary
# ---------------------------------------------------------------------------


def classify_action(extra: dict) -> str:
    """Map a ``style_violation`` event's ``extra`` payload to one of
    "enforce" / "flagged" / "shadow" / "fixed" / "unknown".

    Mirrors the two emitters in ``api/services/worker.py``:

    - ``_apply_style_post`` (post-generation stage) sets an explicit
      ``action`` key directly:

      - ``"flagged"`` when the engine ran in enforce mode (post-stage
        ``RuleViolation``s are flag-tier by construction -- surfaced for
        review, never auto-fixed, even when enforce mode is active for the
        phase's OTHER, fixable substitutions).
      - ``"shadow"`` when the whole engine is in shadow (record-only) mode
        -- covers both violations and (as of the fixed-tier signal below)
        would-be fixes, though shadow mode never actually applies a fix.
      - ``"fixed"`` -- enforce mode only, one event per deterministic
        ``AppliedFix`` (e.g. ``formatter.substitution.ok``,
        ``casing.down_style.title``) the post-stage actually applied to the
        model's output, logged after the ordinary violations loop. This is
        the primary feedback-loop signal for "the model keeps getting X
        wrong (auto-fixed N times)" -- see ``docs/STYLE_FEEDBACK_LOOP.md``.

      This emitter never sets ``action="enforce"``.
    - ``_apply_style_lint`` (validator lint pass) sets no ``action`` key at
      all -- only ``source: "lint"`` and ``mode`` (``"shadow"`` |
      ``"enforce"``). Its ``"enforce"`` mode means the lint flags were
      merged into the persisted QA verdict -- an outcome distinct from
      post-stage's ``"flagged"``, so it is reported here as the
      ``"enforce"`` bucket.

    Any payload matching neither shape (missing/garbled ``extra``) buckets
    as ``"unknown"`` rather than raising or silently dropping the event.
    """
    action = extra.get("action")
    if action in ("flagged", "shadow", "fixed"):
        return action
    if extra.get("source") == "lint":
        mode = extra.get("mode")
        if mode in ("shadow", "enforce"):
            return mode
    return "unknown"


def normalize_violation_record(row: dict) -> dict:
    """Flatten one ``style_violation`` event row into a flat summary record."""
    data = _as_dict(row.get("data"))
    extra = _as_dict(data.get("extra"))
    return {
        "rule_id": extra.get("rule_id") or "(unknown_rule)",
        "phase": extra.get("phase") or data.get("phase") or "(unknown_phase)",
        "severity": extra.get("severity"),
        "model": extra.get("model") or data.get("model"),
        "app_version": row.get("app_version"),
        "action": classify_action(extra),
        "model_fixable": extra.get("model_fixable"),
        "job_id": row.get("job_id"),
    }


def summarize_violations(records: list[dict]) -> list[dict]:
    """Group normalized violation records by ``(rule_id, phase)``.

    Returns a list of dicts (sorted by total desc, then rule_id/phase),
    each: ``{rule_id, phase, enforce, flagged, shadow, fixed, unknown,
    total, by_model, by_app_version}`` -- the last two are ``{value: count}``
    dicts (value ``"(unset)"`` when the field was absent from the payload),
    most-common first.
    """
    groups: dict[tuple[str, str], dict] = {}
    for rec in records:
        key = (rec["rule_id"], rec["phase"])
        g = groups.setdefault(
            key,
            {
                "rule_id": rec["rule_id"],
                "phase": rec["phase"],
                "enforce": 0,
                "flagged": 0,
                "shadow": 0,
                "fixed": 0,
                "unknown": 0,
                "by_model": Counter(),
                "by_app_version": Counter(),
            },
        )
        action = rec["action"]
        if action in ("enforce", "flagged", "shadow", "fixed"):
            g[action] += 1
        else:
            g["unknown"] += 1
        g["by_model"][rec.get("model") or "(unset)"] += 1
        g["by_app_version"][rec.get("app_version") or "(unset)"] += 1

    out: list[dict] = []
    for g in groups.values():
        row = dict(g)
        row["total"] = row["enforce"] + row["flagged"] + row["shadow"] + row["fixed"] + row["unknown"]
        row["by_model"] = dict(row["by_model"].most_common())
        row["by_app_version"] = dict(row["by_app_version"].most_common())
        out.append(row)
    out.sort(key=lambda r: (-r["total"], r["rule_id"], r["phase"]))
    return out


# ---------------------------------------------------------------------------
# Section (b) -- correction patterns
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\w+(?:['’-]\w+)*|[^\w\s]")


def _tokenize(text: str | None) -> list[str]:
    return _TOKEN_RE.findall(text or "")


def diff_replacement_pairs(pipeline_value: str | None, committed_value: str) -> list[tuple[str, str]]:
    """Word-level diff between ``pipeline_value`` and ``committed_value``.

    Tokenizes both (words + punctuation, whitespace-insensitive) and walks
    ``difflib.SequenceMatcher`` opcodes, returning one ``(old_phrase,
    new_phrase)`` tuple per contiguous non-"equal" block (replace, delete,
    or insert), each phrase the space-joined tokens of that block.

    Returns ``[]`` when ``pipeline_value`` is ``None`` (the null-pipeline
    bucket is handled separately -- see ``null_pipeline_correction_counts``)
    or when the two values are token-identical (a purely whitespace/
    formatting difference, e.g. collapsed double spaces, is NOT a
    correction pattern and must not be counted).
    """
    if pipeline_value is None:
        return []
    old_tokens = _tokenize(pipeline_value)
    new_tokens = _tokenize(committed_value)
    if old_tokens == new_tokens:
        return []

    matcher = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    pairs: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_phrase = " ".join(old_tokens[i1:i2])
        new_phrase = " ".join(new_tokens[j1:j2])
        if not old_phrase and not new_phrase:
            continue
        pairs.append((old_phrase, new_phrase))
    return pairs


def normalize_correction_record(row: dict) -> dict:
    """Flatten one ``editor_correction`` event row into a flat record."""
    data = _as_dict(row.get("data"))
    extra = _as_dict(data.get("extra"))
    return {
        "field": extra.get("field") or "(unknown_field)",
        "job_id": row.get("job_id"),
        "media_id": extra.get("media_id"),
        "pipeline_value": extra.get("pipeline_value"),
        "committed_value": extra.get("committed_value") or "",
        "original_value": extra.get("original_value"),
        "app_version": row.get("app_version"),
    }


def cluster_corrections(records: list[dict]) -> list[dict]:
    """Cluster recurring word/short-phrase replacements across normalized
    ``editor_correction`` records.

    Returns clusters sorted by count desc (then old/new): ``[{old, new,
    count, examples}]`` where ``examples`` is up to ``_EXAMPLE_CAP``
    ``{field, job_id, media_id}`` dicts for provenance. Records with a
    ``None`` ``pipeline_value`` contribute nothing here (see
    ``null_pipeline_correction_counts``).
    """
    counts: Counter[tuple[str, str]] = Counter()
    examples: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for rec in records:
        if rec.get("pipeline_value") is None:
            continue
        for old, new in diff_replacement_pairs(rec["pipeline_value"], rec["committed_value"]):
            key = (old, new)
            counts[key] += 1
            if len(examples[key]) < _EXAMPLE_CAP:
                examples[key].append(
                    {"field": rec.get("field"), "job_id": rec.get("job_id"), "media_id": rec.get("media_id")}
                )

    clusters = [
        {"old": old, "new": new, "count": count, "examples": examples[(old, new)]}
        for (old, new), count in counts.items()
    ]
    clusters.sort(key=lambda c: (-c["count"], c["old"], c["new"]))
    return clusters


def null_pipeline_correction_counts(records: list[dict]) -> dict[str, int]:
    """Counts, by field, of ``editor_correction`` records with no
    recoverable ``pipeline_value`` -- sorted by count desc then field."""
    counter = Counter(rec.get("field") or "(unknown_field)" for rec in records if rec.get("pipeline_value") is None)
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


# ---------------------------------------------------------------------------
# Section (c) -- zero-hit rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateRule:
    """One house-style rule-file entry (or group of entries), and the
    event ``rule_id`` it would appear under in ``session_stats`` if it ever
    fired."""

    rule_id: str
    tier: str  # "enforce" | "flag"
    kind: str  # "substitution" | "forbidden"
    labels: tuple[str, ...]  # human-readable id(s)/pattern(s) this candidate represents
    note: str = ""


def build_candidate_rules(rules_raw: dict) -> list[CandidateRule]:
    """Enumerate enforce/flag-tier ``phases.formatter.substitutions`` and
    ``voice.forbidden_phrases`` entries from a house-style rules dict,
    tagged with the event ``rule_id`` they'd actually be logged under.

    Mirrors the *real* rule_id generation so "zero hits" is checked at the
    same granularity ``session_stats`` can actually see:
    ``api.services.style_engine.substitutions._rule_id_for`` (enforce-tier:
    ``formatter.substitution.<slug>``, replicated verbatim below -- see the
    comment at the call site for why this is a copy rather than an import)
    and ``api.services.style_engine.scanner.scan_forbidden`` (forbidden
    phrases: ``voice.forbidden.<category>`` -- NOT keyed by the entry's own
    ``id``, so every entry sharing a ``category`` collapses onto one
    candidate).

    Enforce-tier substitutions apply as deterministic ``AppliedFix``
    records; ``api/services/worker.py``'s ``_apply_style_post`` logs one
    ``style_violation`` event per ``AppliedFix`` in enforce mode
    (``action: "fixed"``, after the ordinary violations loop -- see
    ``docs/STYLE_FEEDBACK_LOOP.md``). So a substitution that actually fires
    now shows up here as a hit under its ``formatter.substitution.<slug>``
    rule_id; a genuine zero-hit result means the substitution never fired
    in the window, not a structural blind spot in what ``session_stats``
    can observe.
    """
    candidates: list[CandidateRule] = []

    phases = rules_raw.get("phases", {}) or {}
    formatter = phases.get("formatter", {}) or {}
    for sub in formatter.get("substitutions", []) or []:
        tier = sub.get("tier")
        if tier == "enforce":
            # Verbatim copy of api.services.style_engine.substitutions.
            # _rule_id_for (a private, pure-stdlib helper) rather than an
            # import: this script deliberately keeps zero api.* imports
            # (see the module docstring), and the logic is a two-line slug
            # -- cheaper to duplicate-with-a-comment than to special-case
            # an import of a single private function. Keep in sync with
            # substitutions.py if that function's slugging ever changes.
            identifier = sub.get("id") or sub.get("replace") or sub.get("find") or "substitution"
            slug = re.sub(r"[^a-z0-9]+", "_", str(identifier).lower()).strip("_") or "substitution"
            rule_id = f"formatter.substitution.{slug}"
            label = sub.get("id") or f"{sub.get('find')} -> {sub.get('replace')}"
            candidates.append(
                CandidateRule(
                    rule_id=rule_id,
                    tier="enforce",
                    kind="substitution",
                    labels=(str(label),),
                    note='enforce-tier fix -- logged as a style_violation event with action="fixed" once applied (see _apply_style_post)',
                )
            )
        elif tier == "flag":
            rule_id = f"formatter.{sub.get('id') or 'unnamed_flag'}"
            label = sub.get("id") or sub.get("detect") or "unnamed_flag"
            candidates.append(
                CandidateRule(rule_id=rule_id, tier="flag", kind="substitution", labels=(str(label),))
            )

    voice = rules_raw.get("voice", {}) or {}
    by_category: dict[str, list[str]] = defaultdict(list)
    for entry in voice.get("forbidden_phrases", []) or []:
        category = entry.get("category") or "uncategorized"
        by_category[category].append(str(entry.get("id") or entry.get("match") or "?"))
    for category, labels in by_category.items():
        note = ""
        if len(labels) > 1:
            note = "multiple entries share this category's event rule_id -- a hit on any one marks the whole group as hit"
        candidates.append(
            CandidateRule(
                rule_id=f"voice.forbidden.{category}", tier="flag", kind="forbidden", labels=tuple(labels), note=note
            )
        )

    return candidates


def zero_hit_rules(candidates: list[CandidateRule], observed_rule_ids: set[str]) -> list[CandidateRule]:
    """Candidates whose ``rule_id`` never appears in ``observed_rule_ids``."""
    return [c for c in candidates if c.rule_id not in observed_rule_ids]


# ---------------------------------------------------------------------------
# Section (d) -- proposed YAML edits
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "term"


def _casing_variant_proposal(cluster: dict) -> dict:
    old, new, count = cluster["old"], cluster["new"], cluster["count"]
    yaml_snippet = f'casing:\n  casing_variants:\n    {old.lower()}: "{new}"'
    rationale = (
        f'Editors consistently committed "{new}" for the pipeline\'s "{old}" ({count} occurrences) -- '
        "looks like a casing/abbreviation convention, not a wording change."
    )
    return {"kind": "casing_variants", "cluster": cluster, "yaml": yaml_snippet, "rationale": rationale}


def _forbidden_phrase_proposal(cluster: dict) -> dict:
    old, new, count = cluster["old"], cluster["new"], cluster["count"]
    rule_id = _slug(old)
    yaml_snippet = (
        "voice:\n"
        "  forbidden_phrases:\n"
        f'    - {{id: {rule_id}, match: "{old}", category: editor_pattern, tier: flag, severity: warning}}'
    )
    rationale = (
        f'Editors replaced "{old}" with "{new}" {count} times -- flag "{old}" for editorial review rather '
        "than auto-fixing (wording swaps need editorial judgment, not a blind substitution)."
    )
    return {"kind": "forbidden_phrases", "cluster": cluster, "yaml": yaml_snippet, "rationale": rationale}


def propose_edits(clusters: list[dict], threshold: int = PROPOSAL_THRESHOLD) -> list[dict]:
    """Heuristic PROPOSED house_style.yaml edits for correction clusters
    meeting the evidence ``threshold`` (default ``PROPOSAL_THRESHOLD``).

    Two proposal shapes only, per the feedback-loop spec:
    - ``old``/``new`` differ only by case -> a ``casing.casing_variants``
      addition.
    - Otherwise -> a ``voice.forbidden_phrases`` addition flagging ``old``
      for review (never a silent substitution -- wording swaps need
      editorial judgment).

    Pure insertions/deletions (``old`` or ``new`` empty) are skipped -- they
    don't map cleanly onto either proposal shape. Never returns a
    proposal below ``threshold`` occurrences (2 occurrences -> no
    proposal; 3 -> a proposal, at the default threshold).
    """
    proposals: list[dict] = []
    for cluster in clusters:
        if cluster["count"] < threshold:
            continue
        old, new = cluster["old"], cluster["new"]
        if not old or not new:
            continue
        if old.lower() == new.lower() and old != new:
            proposals.append(_casing_variant_proposal(cluster))
        else:
            proposals.append(_forbidden_phrase_proposal(cluster))
    return proposals


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _render_header(window: dict, total: int, n_violation: int, n_correction: int) -> list[str]:
    lines = ["# Style Feedback Report", "", PROPOSAL_BANNER, ""]
    lines.append(f"- Window: {window.get('since') or '(all time)'} .. {window.get('until') or '(open)'}")
    lines.append(f"- App version filter: {window.get('app_version') or '(all)'}")
    lines.append(f"- Source DB: `{window.get('db_path', '?')}`")
    lines.append(f"- Generated: {window.get('generated_at', '?')}")
    lines.append("")
    lines.append(f"- Total events in window: **{total}** ({n_violation} style_violation, {n_correction} editor_correction)")
    return lines


def _render_violations_summary(summary: list[dict]) -> list[str]:
    lines = ["## Violations summary", ""]
    if not summary:
        lines.append("_No style_violation events in this window._")
        return lines
    lines += [
        "| rule_id | phase | enforce | flagged | shadow | fixed | unknown | total | by model | by app_version |",
        "|---|---|--:|--:|--:|--:|--:|--:|---|---|",
    ]
    for row in summary:
        models = ", ".join(f"{k}×{v}" for k, v in row["by_model"].items()) or "—"
        versions = ", ".join(f"{k}×{v}" for k, v in row["by_app_version"].items()) or "—"
        lines.append(
            f"| {row['rule_id']} | {row['phase']} | {row['enforce']} | {row['flagged']} | "
            f"{row['shadow']} | {row['fixed']} | {row['unknown']} | {row['total']} | {models} | {versions} |"
        )
    return lines


def _render_correction_patterns(records: list[dict]) -> list[str]:
    lines = ["## Correction patterns", "", "### Recurring replacements (pipeline value → committed value)", ""]
    clusters = cluster_corrections(records)
    if not clusters:
        lines.append("_No correction clusters found (no editor_correction events with a non-null pipeline_value)._")
    else:
        lines += ["| pipeline said | editor committed | count | example |", "|---|---|--:|---|"]
        for c in clusters:
            example = c["examples"][0] if c["examples"] else {}
            ex_str = f"field={example.get('field')} job={example.get('job_id')}" if example else "—"
            old_disp = c["old"] or "(nothing)"
            new_disp = c["new"] or "(removed)"
            lines.append(f"| `{old_disp}` | `{new_disp}` | {c['count']} | {ex_str} |")

    lines += ["", "### Corrections with no recoverable pipeline value (by field)", ""]
    null_counts = null_pipeline_correction_counts(records)
    if not null_counts:
        lines.append("_None in this window._")
    else:
        lines += ["| field | count |", "|---|--:|"]
        for field, count in null_counts.items():
            lines.append(f"| {field} | {count} |")
    return lines


def _render_zero_hit(rules_raw: dict, observed_rule_ids: set[str]) -> list[str]:
    lines = ["## Zero-hit rules (retirement/review candidates)", ""]
    candidates = build_candidate_rules(rules_raw)
    if not candidates:
        lines.append("_No enforce/flag-tier substitution or forbidden-phrase entries found in the rules file._")
        return lines

    lines.append(
        "Note: enforce-tier substitutions apply as deterministic fixes; each applied fix is logged as "
        "a `style_violation` event with `action: \"fixed\"` (see `_apply_style_post` in "
        "`api/services/worker.py`), so a substitution that actually fired in the window shows up as a "
        "hit here under its `formatter.substitution.<slug>` rule_id -- a zero-hit result means it "
        "genuinely never fired. Forbidden-phrase entries that share a `category` also share one event "
        "`rule_id`, so a hit on any entry in the group marks the whole group as hit."
    )
    lines.append("")

    zero = zero_hit_rules(candidates, observed_rule_ids)
    if not zero:
        lines.append("_Every candidate rule had at least one hit in this window._")
        return lines

    lines += ["| rule_id | tier | kind | entries | note |", "|---|---|---|---|---|"]
    for c in zero:
        lines.append(f"| {c.rule_id} | {c.tier} | {c.kind} | {', '.join(c.labels)} | {c.note or '—'} |")
    return lines


def _render_proposals(clusters: list[dict]) -> list[str]:
    lines = ["## Proposed YAML edits", "", PROPOSAL_BANNER, ""]
    proposals = propose_edits(clusters)
    if not proposals:
        lines.append(
            f"_No correction cluster in this window reached the evidence threshold (>= {PROPOSAL_THRESHOLD} occurrences)._"
        )
        return lines

    for p in proposals:
        c = p["cluster"]
        lines.append(f"### `{c['old']}` → `{c['new']}` ({c['count']} occurrences)")
        lines.append("")
        lines.append(p["rationale"])
        lines.append("")
        lines.append("```yaml")
        lines.append(p["yaml"])
        lines.append("```")
        lines.append("")
        lines.append(PROPOSAL_BANNER)
        lines.append("")
    return lines


def build_report(events: list[dict], rules_raw: dict, window: dict) -> str:
    """Pure report builder -- given already-fetched event rows (mixed
    style_violation/editor_correction), a parsed house-style rules dict,
    and a window-metadata dict, returns the full markdown report as a
    string. Factored out from ``main()`` so tests can drive it directly
    against synthetic data with no DB or filesystem involved."""
    violation_rows = [e for e in events if e.get("event_type") == "style_violation"]
    correction_rows = [e for e in events if e.get("event_type") == "editor_correction"]

    violation_records = [normalize_violation_record(r) for r in violation_rows]
    correction_records = [normalize_correction_record(r) for r in correction_rows]

    lines = _render_header(window, len(events), len(violation_rows), len(correction_rows))
    lines.append("")
    lines += _render_violations_summary(summarize_violations(violation_records))
    lines.append("")
    lines += _render_correction_patterns(correction_records)
    lines.append("")
    observed_rule_ids = {r["rule_id"] for r in violation_records}
    lines += _render_zero_hit(rules_raw or {}, observed_rule_ids)
    lines.append("")
    lines += _render_proposals(cluster_corrections(correction_records))

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# DB fetch + CLI
# ---------------------------------------------------------------------------


def fetch_events(db_path: Path, since: str | None, until: str | None, app_version: str | None) -> list[dict]:
    """Read-only fetch of ``style_violation``/``editor_correction`` rows
    from ``session_stats`` in the SQLite file at ``db_path``.

    Returns ``[]`` (never raises) when the DB file doesn't exist yet --
    proves the CLI path works against a fresh/empty deployment. ``since``/
    ``until`` are ``YYYY-MM-DD`` strings compared lexicographically against
    the stored ISO-ish timestamp column, matching the same
    lexicographic-string-comparison convention ``claim_next_job`` uses
    elsewhere in this codebase (see api/services/database.py).
    """
    if not db_path.exists():
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT id, job_id, timestamp, event_type, data, app_version FROM session_stats WHERE event_type IN (?, ?)"
        params: list[str] = list(EVENT_TYPES)
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        if until:
            query += " AND timestamp <= ?"
            params.append(f"{until} 23:59:59" if len(until) == 10 else until)
        if app_version:
            query += " AND app_version = ?"
            params.append(app_version)
        query += " ORDER BY timestamp"
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    events: list[dict] = []
    for row in rows:
        raw_data = row["data"]
        try:
            data = json.loads(raw_data) if raw_data else {}
        except json.JSONDecodeError:
            data = {}
        events.append(
            {
                "id": row["id"],
                "job_id": row["job_id"],
                "timestamp": row["timestamp"],
                "event_type": row["event_type"],
                "data": data,
                "app_version": row["app_version"],
            }
        )
    return events


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--db", type=Path, default=DEFAULT_DB_PATH, help="Path to the SQLite DB (default: $DATABASE_PATH or dashboard.db)."
    )
    ap.add_argument("--since", default=None, help="YYYY-MM-DD -- only events at/after this date.")
    ap.add_argument("--until", default=None, help="YYYY-MM-DD -- only events at/before this date.")
    ap.add_argument("--app-version", default=None, help="Filter to one app_version (e.g. v4.2).")
    ap.add_argument(
        "--rules-file", type=Path, default=DEFAULT_RULES_PATH, help="House-style rules YAML (default: config/house_style.yaml)."
    )
    ap.add_argument("--out", type=Path, default=None, help="Write report here instead of stdout.")
    args = ap.parse_args(argv)

    events = fetch_events(args.db, args.since, args.until, args.app_version)

    rules_raw: dict = {}
    if args.rules_file.exists():
        try:
            loaded = yaml.safe_load(args.rules_file.read_text())
            if isinstance(loaded, dict):
                rules_raw = loaded
        except (OSError, yaml.YAMLError) as exc:
            print(f"WARNING: could not load rules file {args.rules_file}: {exc}", file=sys.stderr)
    else:
        print(f"WARNING: rules file {args.rules_file} not found -- zero-hit-rules section will be empty", file=sys.stderr)

    window = {
        "since": args.since,
        "until": args.until,
        "app_version": args.app_version,
        "db_path": str(args.db),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    report = build_report(events, rules_raw, window)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report)
        print(f"Report: {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
