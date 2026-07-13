#!/usr/bin/env python3
"""Compare eval_pipeline.py runs -- per-phase metrics, style violations, and
title-normalization convergence, across two or more run directories.

Each RUN_DIR is a directory written by ``scripts/eval_pipeline.py`` --
must contain a ``metrics.json`` (optionally alongside ``*_output.md`` /
``seo_output.normalized.md``, which this script does not need to read).
Pure stdlib (json, argparse, pathlib) -- no style_engine import, so this
works standalone against a folder of run artifacts without the rest of
the app installed.

Usage:
    python -m scripts.eval_compare RUN_DIR [RUN_DIR ...] [--baseline RUN_DIR] [--out FILE]

Tolerant of missing keys: an older ``metrics.json`` written before
``--style-report`` existed has no ``style`` key -- style/convergence
sections are skipped gracefully rather than raising.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def _load_metrics(run_dir: Path) -> dict:
    metrics_path = run_dir / "metrics.json"
    if not metrics_path.exists():
        raise SystemExit(f"ERROR: {metrics_path} not found -- not an eval_pipeline.py run dir?")
    try:
        return json.loads(metrics_path.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"ERROR: {metrics_path} is not valid JSON: {e}") from e


def _phase_index(metrics: dict) -> dict[str, dict]:
    return {p.get("phase"): p for p in metrics.get("phases", []) if p.get("phase")}


def _ordered_phase_names(all_metrics: list[dict]) -> list[str]:
    """Union of phase names across runs, first-seen order preserved."""
    seen: dict[str, None] = {}
    for metrics in all_metrics:
        for p in metrics.get("phases", []):
            name = p.get("phase")
            if name:
                seen.setdefault(name, None)
    return list(seen.keys())


def _pct_delta(new: float | None, old: float | None) -> str:
    if new is None or old is None:
        return "—"
    if old == 0:
        return "0.0%" if new == 0 else "n/a (baseline=0)"
    delta = (new - old) / old * 100
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.1f}%"


# ---------------------------------------------------------------------------
# Section 1 -- per-phase metrics table
# ---------------------------------------------------------------------------


def _phase_metrics_section(run_names: list[str], all_metrics: list[dict]) -> list[str]:
    phase_names = _ordered_phase_names(all_metrics)
    lines = [
        "## Per-phase metrics",
        "",
        "| Run | Phase | Model | Total tokens | Cost | Wall s | Output words | coverage_ratio |",
        "|---|---|---|--:|--:|--:|--:|--:|",
    ]
    for run_name, metrics in zip(run_names, all_metrics):
        phases = _phase_index(metrics)
        completeness = metrics.get("completeness") or {}
        for phase_name in phase_names:
            p = phases.get(phase_name)
            if p is None:
                continue
            if not p.get("ok", True):
                lines.append(f"| {run_name} | {phase_name} | **FAILED** | — | — | {p.get('wall_s', '—')} | — | — |")
                continue
            model = (p.get("model") or "—").split("/")[-1]
            cov = completeness.get("coverage_ratio") if phase_name == "formatter" else None
            lines.append(
                f"| {run_name} | {phase_name} | {model} | {p.get('total_tokens', '—')} | "
                f"{p.get('cost', '—')} | {p.get('wall_s', '—')} | {p.get('words', '—')} | "
                f"{cov if cov is not None else '—'} |"
            )
    return lines


# ---------------------------------------------------------------------------
# Section 2 -- style violations table
# ---------------------------------------------------------------------------


def _style_seo(metrics: dict) -> dict | None:
    return (metrics.get("style") or {}).get("seo")


def _style_violations_section(run_names: list[str], all_metrics: list[dict]) -> list[str]:
    runs_with_style = [(name, _style_seo(m)) for name, m in zip(run_names, all_metrics) if _style_seo(m) is not None]
    if not runs_with_style:
        return []

    lines = ["## Style violations (seo phase)", ""]

    any_rows = False
    lines += ["| Run | rule_id | pre | post |", "|---|---|--:|--:|"]
    for run_name, seo_style in runs_with_style:
        if seo_style.get("skipped"):
            lines.append(f"| {run_name} | *(skipped: {seo_style.get('reason', 'unknown')})* | — | — |")
            any_rows = True
            continue
        pre_counts = Counter(v["rule_id"] for v in seo_style.get("violations_pre", []))
        post_counts = Counter(v["rule_id"] for v in seo_style.get("violations_post", []))
        rule_ids = sorted(set(pre_counts) | set(post_counts))
        if not rule_ids:
            lines.append(f"| {run_name} | *(no violations)* | 0 | 0 |")
            any_rows = True
            continue
        for rid in rule_ids:
            lines.append(f"| {run_name} | {rid} | {pre_counts.get(rid, 0)} | {post_counts.get(rid, 0)} |")
            any_rows = True
    if not any_rows:
        lines.append("| — | — | — | — |")

    lines += ["", "### title_changed flags", "", "| Run | title_changed |", "|---|---|"]
    for run_name, seo_style in runs_with_style:
        flag = "—" if seo_style.get("skipped") else seo_style.get("title_changed")
        lines.append(f"| {run_name} | {flag} |")

    return lines


# ---------------------------------------------------------------------------
# Section 3 -- convergence
# ---------------------------------------------------------------------------


def _convergence_report(values: list[str]) -> list[str]:
    """``values`` are one title string per run that has this field. Returns
    markdown lines reporting the exact-match rate of the most common value,
    and the distinct values when not fully converged."""
    total = len(values)
    if total == 0:
        return ["- (no runs with this field)"]
    counts = Counter(values)
    _, top_count = counts.most_common(1)[0]
    lines = [f"- **{top_count} of {total} runs byte-identical.**"]
    distinct = sorted(counts.keys())
    if len(distinct) > 1:
        lines.append(f"- {len(distinct)} distinct value(s):")
        for v in distinct:
            lines.append(f"  - `{v}` ({counts[v]}x)")
    else:
        lines.append(f"- Value: `{distinct[0]}`")
    return lines


def _convergence_section(run_names: list[str], all_metrics: list[dict]) -> list[str]:
    normalized_titles = []
    raw_titles = []
    for name, m in zip(run_names, all_metrics):
        seo_style = _style_seo(m)
        if seo_style is None or seo_style.get("skipped"):
            continue
        if seo_style.get("title_normalized") is not None:
            normalized_titles.append(seo_style["title_normalized"])
        if seo_style.get("title_raw") is not None:
            raw_titles.append(seo_style["title_raw"])

    if not normalized_titles and not raw_titles:
        return []

    lines = ["## Convergence (seo title)", ""]
    lines += ["### Post-normalization (normalized title)", ""]
    lines += _convergence_report(normalized_titles)
    lines += ["", "### Pre-normalization (raw title) -- for delta visibility", ""]
    lines += _convergence_report(raw_titles)
    return lines


# ---------------------------------------------------------------------------
# Section 4 -- delta vs baseline
# ---------------------------------------------------------------------------


def _delta_section(
    run_names: list[str], all_metrics: list[dict], baseline_name: str, baseline_metrics: dict
) -> list[str]:
    phase_names = _ordered_phase_names([*all_metrics, baseline_metrics])
    baseline_phases = _phase_index(baseline_metrics)

    lines = [
        f"## Delta vs baseline (`{baseline_name}`)",
        "",
        "| Run | Phase | Δ tokens | Δ cost | Δ duration (wall s) |",
        "|---|---|--:|--:|--:|",
    ]
    any_rows = False
    for run_name, metrics in zip(run_names, all_metrics):
        if run_name == baseline_name:
            continue
        phases = _phase_index(metrics)
        for phase_name in phase_names:
            p = phases.get(phase_name)
            b = baseline_phases.get(phase_name)
            if p is None or b is None or not p.get("ok", True) or not b.get("ok", True):
                continue
            lines.append(
                f"| {run_name} | {phase_name} | "
                f"{_pct_delta(p.get('total_tokens'), b.get('total_tokens'))} | "
                f"{_pct_delta(p.get('cost'), b.get('cost'))} | "
                f"{_pct_delta(p.get('wall_s'), b.get('wall_s'))} |"
            )
            any_rows = True
    if not any_rows:
        return []
    return lines


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def build_report(run_dirs: list[Path], baseline_dir: Path | None = None) -> str:
    """Pure report builder -- given run directories (each already written by
    eval_pipeline.py), returns the full markdown comparison report as a
    string. Factored out from main() so tests can drive it directly against
    tmp_path-written metrics.json fixtures."""
    run_names = [d.name for d in run_dirs]
    all_metrics = [_load_metrics(d) for d in run_dirs]

    baseline_dir = baseline_dir or run_dirs[0]
    if baseline_dir in run_dirs:
        baseline_name = baseline_dir.name
        baseline_metrics = all_metrics[run_dirs.index(baseline_dir)]
    else:
        baseline_name = baseline_dir.name
        baseline_metrics = _load_metrics(baseline_dir)

    lines = [
        "# eval_compare report",
        "",
        f"- Runs: {', '.join(run_names)}",
        f"- Baseline: `{baseline_name}`",
        "",
    ]
    lines += _phase_metrics_section(run_names, all_metrics)
    lines.append("")
    lines += _style_violations_section(run_names, all_metrics)
    if lines[-1] != "":
        lines.append("")
    lines += _convergence_section(run_names, all_metrics)
    if lines[-1] != "":
        lines.append("")
    lines += _delta_section(run_names, all_metrics, baseline_name, baseline_metrics)

    # Collapse any run of 3+ blank lines left by empty optional sections.
    out_lines: list[str] = []
    for line in lines:
        if line == "" and len(out_lines) >= 2 and out_lines[-1] == "" and out_lines[-2] == "":
            continue
        out_lines.append(line)
    return "\n".join(out_lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dirs", nargs="+", type=Path, help="Run directories written by eval_pipeline.py.")
    ap.add_argument(
        "--baseline", type=Path, default=None, help="Baseline run dir for delta comparisons. Default: first RUN_DIR."
    )
    ap.add_argument("--out", type=Path, default=None, help="Write report here instead of stdout.")
    args = ap.parse_args(argv)

    for d in args.run_dirs:
        if not d.is_dir():
            print(f"ERROR: {d} is not a directory", file=sys.stderr)
            return 1

    report = build_report(args.run_dirs, args.baseline)

    if args.out:
        args.out.write_text(report)
        print(f"Report: {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
