#!/usr/bin/env python3
"""Sprint 5 Staging Soak — End-of-Soak Report Generator.

Reads /tmp/sprint5-soak-log.jsonl and /tmp/sprint5-smoke-log.jsonl,
evaluates each Section D acceptance criterion, and writes a GO/NO-GO
markdown report to planning/sprint-5-soak-report.md.

Usage:
    python3 scripts/sprint5_soak_report.py \\
        [--soak-log /tmp/sprint5-soak-log.jsonl] \\
        [--smoke-log /tmp/sprint5-smoke-log.jsonl] \\
        [--baseline-count /tmp/sprint5-baseline-count.txt] \\
        [--output planning/sprint-5-soak-report.md] \\
        [--db dashboard.db]

Optional matplotlib support: if matplotlib is installed, latency/rate plots
are written to planning/sprint-5-plots/.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--soak-log", default="/tmp/sprint5-soak-log.jsonl")
    p.add_argument("--smoke-log", default="/tmp/sprint5-smoke-log.jsonl")
    p.add_argument("--baseline-count", default="/tmp/sprint5-baseline-count.txt")
    p.add_argument("--output", default="planning/sprint-5-soak-report.md")
    p.add_argument("--db", default="dashboard.db")
    p.add_argument("--no-plots", action="store_true", help="Skip matplotlib plots")
    return p.parse_args()


# ---------------------------------------------------------------------------
# JSONL loading
# ---------------------------------------------------------------------------


def load_jsonl(path: str) -> list[dict]:
    records: list[dict] = []
    p = Path(path)
    if not p.exists():
        print(f"WARNING: {path} not found — using empty dataset", file=sys.stderr)
        return records
    with p.open() as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"WARNING: skipping malformed line {i} in {path}: {exc}", file=sys.stderr)
    return records


# ---------------------------------------------------------------------------
# Section D criterion evaluators
# ---------------------------------------------------------------------------


# D1: Politeness
def eval_politeness(soak_records: list[dict]) -> dict[str, Any]:
    indexer_runs = [r for r in soak_records if r.get("event") == "indexer_run"]
    pause_events = [r for r in soak_records if r.get("event") == "pause_window"]

    if not indexer_runs:
        return {
            "pass": None,
            "reason": "No indexer_run events found — monitor may not have run.",
        }

    # Average req rate per 5-min window
    # Build (ts, req_total) pairs and bucket by 5-min intervals
    window_secs = 300  # 5 minutes
    ts_buckets: dict[int, int] = defaultdict(int)  # bucket_start -> req_count

    for run in indexer_runs:
        ts_str = run.get("ts", "")
        req_total = run.get("req_total", 0)
        if not ts_str:
            continue
        try:
            ts_epoch = int(datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp())
        except ValueError:
            continue
        bucket = (ts_epoch // window_secs) * window_secs
        ts_buckets[bucket] += req_total

    max_rate_5min = 0.0
    if ts_buckets:
        # max requests in any 5-min window / 300s = max avg rate
        max_rate_5min = max(count / window_secs for count in ts_buckets.values())

    # Peak inflight across all runs
    peak_inflight_observed = max((r.get("peak_inflight", 0) for r in indexer_runs), default=0)

    # Error rate
    total_reqs = sum(r.get("req_total", 0) for r in indexer_runs)
    total_errors = sum(r.get("req_5xx", 0) + r.get("req_errors", 0) for r in indexer_runs)
    error_rate = (total_errors / total_reqs) if total_reqs > 0 else 0.0

    # Pause window compliance: check that no indexer_run ts falls in 11:00–15:00 UTC
    pause_violations: list[str] = []
    for run in indexer_runs:
        ts_str = run.get("ts", "")
        if not ts_str:
            continue
        try:
            ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            t = ts_dt.time()
            from datetime import time as dtime

            in_window = dtime(11, 0) <= t <= dtime(15, 0)
            if in_window:
                pause_violations.append(ts_str)
        except ValueError:
            pass

    passes = {
        "avg_rate_5min": max_rate_5min <= 1.0,
        "peak_inflight": peak_inflight_observed <= 4,
        "error_rate": error_rate < 0.01,
        "pause_compliance": len(pause_violations) == 0,
    }

    overall = all(passes.values())

    return {
        "pass": overall,
        "max_avg_rate_5min_req_s": round(max_rate_5min, 4),
        "peak_inflight_observed": peak_inflight_observed,
        "total_requests": total_reqs,
        "total_errors": total_errors,
        "error_rate_pct": round(error_rate * 100, 3),
        "pause_window_violations": pause_violations,
        "pause_events_logged": len(pause_events),
        "sub_criteria": passes,
        "reason": (
            "All politeness criteria met." if overall else "FAIL: " + "; ".join(k for k, v in passes.items() if not v)
        ),
    }


# D2: FTS parity
def eval_parity(soak_records: list[dict]) -> dict[str, Any]:
    parity_events = [r for r in soak_records if r.get("event") == "parity_check"]
    # Also look at fts_parity_delta from indexer_run events
    indexer_parities = [
        {"ts": r.get("ts"), "fts_delta": r.get("fts_parity_delta")}
        for r in soak_records
        if r.get("event") == "indexer_run" and "fts_parity_delta" in r
    ]

    all_deltas: list[dict] = []
    for e in parity_events:
        all_deltas.append({"ts": e.get("ts", ""), "delta": e.get("fts_delta"), "source": "parity_check"})
    for e in indexer_parities:
        all_deltas.append({"ts": e["ts"] or "", "delta": e["fts_delta"], "source": "indexer_run"})

    failures = [d for d in all_deltas if d["delta"] is not None and d["delta"] != 0]

    return {
        "pass": len(failures) == 0 and len(all_deltas) > 0,
        "total_checks": len(all_deltas),
        "failures": failures,
        "reason": (
            "All parity checks returned 0 (FTS in sync)."
            if not failures
            else f"FAIL: {len(failures)} non-zero delta(s): "
            + "; ".join(f"ts={d['ts']} delta={d['delta']}" for d in failures[:3])
        ),
    }


# D3: Latency
def eval_latency(smoke_records: list[dict]) -> dict[str, Any]:
    search_calls = [r for r in smoke_records if r.get("event") == "smoke" and "latency_ms" in r]

    if len(search_calls) < 2:
        return {
            "pass": None,
            "reason": f"Insufficient smoke calls ({len(search_calls)}) — need ≥ 2 to evaluate.",
            "call_count": len(search_calls),
        }

    lats = sorted(r["latency_ms"] for r in search_calls if r.get("status") == 200)
    if not lats:
        return {
            "pass": False,
            "reason": "No successful smoke calls (status 200) found.",
            "call_count": len(search_calls),
        }

    def pct(p: float) -> float:
        idx = int(len(lats) * p / 100)
        return lats[min(idx, len(lats) - 1)]

    p50 = round(pct(50), 1)
    p95 = round(pct(95), 1)
    p99 = round(pct(99), 1)

    passes = p95 < 50 and p99 < 200

    return {
        "pass": passes,
        "call_count": len(search_calls),
        "successful_calls": len(lats),
        "p50_ms": p50,
        "p95_ms": p95,
        "p99_ms": p99,
        "min_ms": lats[0],
        "max_ms": lats[-1],
        "mean_ms": round(mean(lats), 1),
        "reason": (
            f"p95={p95}ms < 50ms, p99={p99}ms < 200ms — latency envelope met."
            if passes
            else f"FAIL: p95={p95}ms (threshold 50ms), p99={p99}ms (threshold 200ms)"
        ),
    }


# D4: Coverage
def eval_coverage(soak_records: list[dict], baseline_count: int, db_path: str) -> dict[str, Any]:
    # Prefer live DB count; fall back to max seen in soak log
    actual_mp4_count: int | None = None

    if Path(db_path).exists():
        try:
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT COUNT(*) FROM mmingest_files " "WHERE prefix LIKE '2WLI%' AND file_type = 'mp4'"
            ).fetchone()
            actual_mp4_count = row[0] if row else 0
            conn.close()
        except sqlite3.Error as exc:
            print(f"WARNING: DB query failed: {exc}", file=sys.stderr)

    if actual_mp4_count is None:
        # Fall back to log
        wli_counts = [r.get("mmingest_files_count_2wli", 0) for r in soak_records if r.get("event") == "parity_check"]
        actual_mp4_count = max(wli_counts, default=0)

    if baseline_count == 0:
        return {
            "pass": None,
            "reason": "Baseline count is 0 — no baseline was captured (see Section A6).",
            "actual_mp4_count": actual_mp4_count,
            "baseline_count": 0,
        }

    diff_pct = abs(actual_mp4_count - baseline_count) / baseline_count
    passes = diff_pct <= 0.05

    return {
        "pass": passes,
        "actual_mp4_count": actual_mp4_count,
        "baseline_count": baseline_count,
        "diff_pct": round(diff_pct * 100, 2),
        "reason": (
            f"Coverage {round((actual_mp4_count / baseline_count) * 100, 1)}% "
            f"(actual={actual_mp4_count} baseline={baseline_count} diff={round(diff_pct*100,2)}%)"
            if passes
            else f"FAIL: diff={round(diff_pct*100,2)}% > 5% threshold "
            f"(actual={actual_mp4_count} baseline={baseline_count})"
        ),
    }


# D5: No regression
def eval_regression(smoke_records: list[dict]) -> dict[str, Any]:
    regression_checks = [r for r in smoke_records if r.get("event") == "regression_check"]

    if not regression_checks:
        return {
            "pass": None,
            "reason": "No regression check events found — smoke script may not have run.",
        }

    failures = [r for r in regression_checks if r.get("outcome") != "pass"]

    endpoint_summary: dict[str, dict] = {}
    for r in regression_checks:
        ep = r.get("endpoint", "?")
        if ep not in endpoint_summary:
            endpoint_summary[ep] = {"checks": 0, "failures": 0, "last_status": None}
        endpoint_summary[ep]["checks"] += 1
        endpoint_summary[ep]["last_status"] = r.get("status")
        if r.get("outcome") != "pass":
            endpoint_summary[ep]["failures"] += 1

    passes = len(failures) == 0

    return {
        "pass": passes,
        "total_checks": len(regression_checks),
        "failed_checks": len(failures),
        "endpoints": endpoint_summary,
        "reason": (
            "All legacy endpoints returned 200 throughout the soak."
            if passes
            else f"FAIL: {len(failures)} failed regression checks — "
            + ", ".join(f"{r.get('endpoint')} status={r.get('status')}" for r in failures[:3])
        ),
    }


# ---------------------------------------------------------------------------
# Tunable defaults snapshot
# ---------------------------------------------------------------------------


def compute_tunables(soak_records: list[dict]) -> dict[str, Any]:
    indexer_runs = [r for r in soak_records if r.get("event") == "indexer_run"]

    if not indexer_runs:
        return {}

    all_reqs = sum(r.get("req_total", 0) for r in indexer_runs)
    all_elapsed = sum(r.get("elapsed_s", 0) for r in indexer_runs)
    avg_rate = (all_reqs / all_elapsed) if all_elapsed > 0 else 0.0

    peak_inflight = max((r.get("peak_inflight", 0) for r in indexer_runs), default=0)

    # Recommend max_concurrent: floor to nearest power of 2, cap at observed peak
    recommended_concurrent = 1
    for p2 in [4, 2, 1]:
        if peak_inflight >= p2:
            recommended_concurrent = p2
            break

    # Recommend rate: 80% of observed max instantaneous rate
    # Use the max single-run req rate as proxy
    per_run_rates = [r.get("req_total", 0) / r.get("elapsed_s", 1) for r in indexer_runs if r.get("elapsed_s", 0) > 0]
    max_burst_rate = max(per_run_rates, default=0.0)
    recommended_rate = round(min(max_burst_rate * 0.8, 1.0), 2)  # never exceed 1.0 for safety

    return {
        "observed_avg_req_rate_s": round(avg_rate, 4),
        "observed_peak_inflight": peak_inflight,
        "recommended_max_concurrent": recommended_concurrent,
        "recommended_rate_per_second": recommended_rate,
        "total_indexer_runs": len(indexer_runs),
        "total_requests_to_mmingest": all_reqs,
    }


# ---------------------------------------------------------------------------
# Markdown report builder
# ---------------------------------------------------------------------------

PASS_ICON = "GO"
FAIL_ICON = "NO-GO"
WARN_ICON = "WARN"


def criterion_header(name: str, result: dict[str, Any]) -> str:
    p = result.get("pass")
    if p is True:
        icon = PASS_ICON
    elif p is False:
        icon = FAIL_ICON
    else:
        icon = WARN_ICON
    return f"### [{icon}] {name}"


def build_report(
    soak_records: list[dict],
    smoke_records: list[dict],
    politeness: dict,
    parity: dict,
    latency: dict,
    coverage: dict,
    regression: dict,
    tunables: dict,
    baseline_count: int,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Overall verdict
    results = [politeness, parity, latency, coverage, regression]
    all_pass = all(r.get("pass") is True for r in results)
    any_fail = any(r.get("pass") is False for r in results)

    if all_pass:
        verdict = "**OVERALL: GO**"
        verdict_detail = "All five acceptance criteria passed. Ready for production rollout."
    elif any_fail:
        verdict = "**OVERALL: NO-GO**"
        failing = []
        if politeness.get("pass") is False:
            failing.append("Politeness")
        if parity.get("pass") is False:
            failing.append("FTS Parity")
        if latency.get("pass") is False:
            failing.append("Latency")
        if coverage.get("pass") is False:
            failing.append("Coverage")
        if regression.get("pass") is False:
            failing.append("Regression")
        verdict_detail = f"Failing criteria: {', '.join(failing)}. See Section D in runbook for remediation steps."
    else:
        verdict = "**OVERALL: INCOMPLETE**"
        verdict_detail = "Some criteria could not be evaluated (missing data). Re-check after a full 24h soak."

    soak_start_event = next((r for r in soak_records if r.get("event") == "soak_start"), {})
    soak_end_event = next((r for r in soak_records if r.get("event") == "soak_end"), {})
    total_smoke_calls = sum(1 for r in smoke_records if r.get("event") == "smoke")
    total_indexer_runs = sum(1 for r in soak_records if r.get("event") == "indexer_run")

    lines = [
        "# Sprint 5 Staging Soak Report",
        "",
        f"Generated: {now}",
        "",
        f"## {verdict}",
        "",
        verdict_detail,
        "",
        "---",
        "",
        "## Soak Summary",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| Soak started | {soak_start_event.get('ts', 'unknown')} |",
        f"| Soak ended | {soak_end_event.get('ts', 'unknown')} |",
        "| Directory crawled | `/wisconsinlife/` depth 1 (prefix `2WLI`) |",
        f"| Total indexer runs | {total_indexer_runs} |",
        f"| Total smoke calls | {total_smoke_calls} |",
        f"| Baseline MP4 count | {baseline_count} |",
        "",
        "---",
        "",
        "## Section D — Acceptance Criteria",
        "",
    ]

    # D1 Politeness
    lines += [
        criterion_header("D1. Politeness", politeness),
        "",
        f"**Result:** {politeness.get('reason', '(no data)')}",
        "",
        "| Criterion | Threshold | Observed | Pass? |",
        "|-----------|-----------|----------|-------|",
        f"| Avg req rate (5-min window) | ≤ 1.0 req/s | "
        f"{politeness.get('max_avg_rate_5min_req_s', '?')} req/s | "
        f"{'yes' if politeness.get('sub_criteria', {}).get('avg_rate_5min') else 'NO'} |",
        f"| Peak in-flight concurrency | ≤ 4 | "
        f"{politeness.get('peak_inflight_observed', '?')} | "
        f"{'yes' if politeness.get('sub_criteria', {}).get('peak_inflight') else 'NO'} |",
        f"| Error rate | < 1% | "
        f"{politeness.get('error_rate_pct', '?')}% | "
        f"{'yes' if politeness.get('sub_criteria', {}).get('error_rate') else 'NO'} |",
        f"| Pause window compliance | Zero requests 11:00–15:00 UTC | "
        f"{len(politeness.get('pause_window_violations', []))} violation(s) | "
        f"{'yes' if politeness.get('sub_criteria', {}).get('pause_compliance') else 'NO'} |",
        "",
        f"Total requests to mmingest: {politeness.get('total_requests', '?')}  ",
        f"Total errors (5xx + connect): {politeness.get('total_errors', '?')}  ",
        f"Pause events logged: {politeness.get('pause_events_logged', '?')}",
        "",
    ]

    # D2 Parity
    lines += [
        criterion_header("D2. FTS Parity", parity),
        "",
        f"**Result:** {parity.get('reason', '(no data)')}",
        "",
        f"Total parity checks: {parity.get('total_checks', 0)}  ",
        f"Failed checks (non-zero delta): {len(parity.get('failures', []))}",
        "",
    ]
    if parity.get("failures"):
        lines += [
            "**Failed checks:**",
            "",
        ]
        for f in parity["failures"][:5]:
            lines.append(f"- `{f.get('ts')}` delta={f.get('delta')} (source: {f.get('source')})")
        lines.append("")

    # D3 Latency
    lines += [
        criterion_header("D3. Search Latency", latency),
        "",
        f"**Result:** {latency.get('reason', '(no data)')}",
        "",
        "| Metric | Threshold | Observed | Pass? |",
        "|--------|-----------|----------|-------|",
        f"| p95 latency | < 50ms | {latency.get('p95_ms', '?')}ms | "
        f"{'yes' if latency.get('p95_ms', 9999) < 50 else 'NO'} |",
        f"| p99 latency | < 200ms | {latency.get('p99_ms', '?')}ms | "
        f"{'yes' if latency.get('p99_ms', 9999) < 200 else 'NO'} |",
        "",
        f"Successful smoke calls: {latency.get('successful_calls', 0)} of {latency.get('call_count', 0)}  ",
        f"p50: {latency.get('p50_ms', '?')}ms  |  "
        f"mean: {latency.get('mean_ms', '?')}ms  |  "
        f"min: {latency.get('min_ms', '?')}ms  |  "
        f"max: {latency.get('max_ms', '?')}ms",
        "",
    ]

    # D4 Coverage
    lines += [
        criterion_header("D4. Coverage", coverage),
        "",
        f"**Result:** {coverage.get('reason', '(no data)')}",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Baseline MP4 count (manual curl) | {coverage.get('baseline_count', '?')} |",
        f"| Observed MP4 count in `mmingest_files` (prefix=2WLI) | {coverage.get('actual_mp4_count', '?')} |",
        f"| Difference | {coverage.get('diff_pct', '?')}% |",
        "| Threshold | ≤ 5% |",
        "",
    ]

    # D5 Regression
    lines += [
        criterion_header("D5. No Regression", regression),
        "",
        f"**Result:** {regression.get('reason', '(no data)')}",
        "",
    ]
    if regression.get("endpoints"):
        lines += [
            "| Endpoint | Checks | Failures | Last Status |",
            "|----------|--------|----------|-------------|",
        ]
        for ep, data in regression["endpoints"].items():
            lines.append(f"| `{ep}` | {data['checks']} | {data['failures']} | {data['last_status']} |")
        lines.append("")

    # Tunable defaults
    lines += [
        "---",
        "",
        "## Tunable Defaults Snapshot",
        "",
        "Empirical values observed during the soak — use as production starting point.",
        "",
        "| Parameter | Soak Observed | Recommended for Production |",
        "|-----------|--------------|---------------------------|",
        f"| `max_concurrent` | {tunables.get('observed_peak_inflight', '?')} | {tunables.get('recommended_max_concurrent', '?')} |",
        f"| `rate_per_second` | {tunables.get('observed_avg_req_rate_s', '?')} avg | {tunables.get('recommended_rate_per_second', '?')} |",
        "| `pause_window` (UTC) | 11:00–15:00 | 11:00–15:00 (= 06:00–10:00 CDT broadcast peak) |",
        "| `directories` | `['/wisconsinlife/']` | `['/']` (full crawl for production) |",
        "| `delta_walk_interval_hours` | 1h (soak) | 1h (keep for prod; mmingest is stable) |",
        "",
        f"Total requests to mmingest during soak: {tunables.get('total_requests_to_mmingest', '?')}  ",
        f"Total indexer runs: {tunables.get('total_indexer_runs', '?')}",
        "",
        "---",
        "",
        "## Smoke Query Breakdown",
        "",
    ]

    # Smoke query summary
    by_query: dict[str, list[dict]] = defaultdict(list)
    for r in smoke_records:
        if r.get("event") == "smoke":
            q = r.get("query", "?")
            by_query[q].append(r)

    if by_query:
        lines += [
            "| Query | Calls | Pass | Warn | Fail | Avg Hits | p50 ms | p95 ms |",
            "|-------|-------|------|------|------|----------|--------|--------|",
        ]
        for q, calls in by_query.items():
            outcomes = [c.get("outcome", "?") for c in calls]
            n_pass = outcomes.count("pass")
            n_warn = outcomes.count("warn_no_hits")
            n_fail = outcomes.count("fail")
            lats = [c.get("latency_ms", 0) for c in calls if c.get("status") == 200]
            hits = [c.get("hits", 0) for c in calls if c.get("hits", -1) >= 0]
            avg_hits = round(mean(hits), 1) if hits else 0
            lats_s = sorted(lats)
            p50 = lats_s[len(lats_s) // 2] if lats_s else "?"
            p95 = lats_s[min(int(len(lats_s) * 0.95), len(lats_s) - 1)] if lats_s else "?"
            q_display = q[:40] + "..." if len(q) > 40 else q
            lines.append(
                f"| `{q_display}` | {len(calls)} | {n_pass} | {n_warn} | {n_fail} | {avg_hits} | {p50} | {p95} |"
            )
        lines.append("")
    else:
        lines += ["(No smoke query data found)", ""]

    # Next steps
    lines += [
        "---",
        "",
        "## Next Steps",
        "",
    ]
    if all_pass:
        lines += [
            "1. Update plan file: mark `[ ] Sprint 5 soak execution` as done.",
            "2. Configure production scheduler with the tunable defaults above.",
            "3. Wire `start_mmingest_scheduler()` into `api/main.py` lifespan.",
            "4. Deploy to production (Cardigan on LXC 103, `cardigan.riechers.co`).",
            "5. Run a first full-crawl pass from `/` and verify parity within 2h.",
            "",
        ]
    elif any_fail:
        lines += [
            "1. Address failing criteria (see Section F of the runbook for per-criterion remediation).",
            "2. Re-soak from scratch after fixes are verified in unit tests.",
            "3. Do NOT proceed to production until all five criteria pass.",
            "",
        ]
    else:
        lines += [
            "1. Ensure the full 24h soak ran (check `soak_start` and `soak_end` events in JSONL).",
            "2. Verify the smoke script ran every 30 min (48 smoke calls expected).",
            "3. Verify the parity check ran at T+0, T+6, T+12, T+18, T+24 (5 checks expected).",
            "4. Re-run this report after confirming all data is present.",
            "",
        ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Optional: matplotlib plots
# ---------------------------------------------------------------------------


def generate_plots(
    soak_records: list[dict],
    smoke_records: list[dict],
    output_dir: Path,
) -> list[str]:
    """Generate latency and rate plots. Returns list of generated file paths."""
    try:
        import matplotlib  # type: ignore[import]

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore[import]
    except ImportError:
        print("matplotlib not available — skipping plots", file=sys.stderr)
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []

    # Plot 1: Smoke search latency over time
    smoke_calls = [r for r in smoke_records if r.get("event") == "smoke" and r.get("status") == 200 and "ts" in r]
    if len(smoke_calls) >= 2:
        try:
            xs = [datetime.fromisoformat(r["ts"].replace("Z", "+00:00")) for r in smoke_calls]
            ys = [r["latency_ms"] for r in smoke_calls]
            fig, ax = plt.subplots(figsize=(12, 4))
            ax.scatter(xs, ys, s=10, alpha=0.7, label="Search latency")
            ax.axhline(50, color="orange", linestyle="--", label="p95 threshold (50ms)")
            ax.axhline(200, color="red", linestyle="--", label="p99 threshold (200ms)")
            ax.set_xlabel("Time (UTC)")
            ax.set_ylabel("Latency (ms)")
            ax.set_title("Sprint 5 Soak — /api/mmingest/search Latency")
            ax.legend()
            fig.autofmt_xdate()
            path = str(output_dir / "latency_over_time.png")
            fig.savefig(path, dpi=100, bbox_inches="tight")
            plt.close(fig)
            generated.append(path)
        except Exception as exc:
            print(f"WARNING: latency plot failed: {exc}", file=sys.stderr)

    # Plot 2: Request rate per indexer run
    indexer_runs = [
        r for r in soak_records if r.get("event") == "indexer_run" and "ts" in r and r.get("elapsed_s", 0) > 0
    ]
    if len(indexer_runs) >= 2:
        try:
            xs = [datetime.fromisoformat(r["ts"].replace("Z", "+00:00")) for r in indexer_runs]
            ys = [r.get("req_total", 0) / r.get("elapsed_s", 1) for r in indexer_runs]
            fig, ax = plt.subplots(figsize=(12, 4))
            ax.bar([x for x in range(len(xs))], ys, label="Req/s per run")
            ax.axhline(1.0, color="red", linestyle="--", label="Threshold (1.0 req/s)")
            ax.set_xlabel("Indexer run #")
            ax.set_ylabel("Avg req/s during run")
            ax.set_title("Sprint 5 Soak — mmingest Request Rate per Indexer Run")
            ax.legend()
            path = str(output_dir / "request_rate_per_run.png")
            fig.savefig(path, dpi=100, bbox_inches="tight")
            plt.close(fig)
            generated.append(path)
        except Exception as exc:
            print(f"WARNING: rate plot failed: {exc}", file=sys.stderr)

    return generated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()

    print(f"Loading soak log: {args.soak_log}")
    soak_records = load_jsonl(args.soak_log)

    print(f"Loading smoke log: {args.smoke_log}")
    smoke_records = load_jsonl(args.smoke_log)

    print(f"Soak events: {len(soak_records)}")
    print(f"Smoke events: {len(smoke_records)}")

    # Load baseline count
    baseline_count = 0
    bc_path = Path(args.baseline_count)
    if bc_path.exists():
        try:
            baseline_count = int(bc_path.read_text().strip())
            print(f"Baseline MP4 count: {baseline_count}")
        except ValueError:
            print(f"WARNING: invalid baseline count in {args.baseline_count}", file=sys.stderr)
    else:
        print(f"WARNING: baseline count file not found: {args.baseline_count}", file=sys.stderr)

    # Evaluate criteria
    print("\nEvaluating acceptance criteria...")
    politeness = eval_politeness(soak_records)
    parity = eval_parity(soak_records)
    latency = eval_latency(smoke_records)
    coverage = eval_coverage(soak_records, baseline_count, args.db)
    regression = eval_regression(smoke_records)
    tunables = compute_tunables(soak_records)

    # Print summary
    print(
        f"\nD1 Politeness:  {'PASS' if politeness.get('pass') else ('FAIL' if politeness.get('pass') is False else 'INCOMPLETE')}"
    )
    print(
        f"D2 Parity:      {'PASS' if parity.get('pass') else ('FAIL' if parity.get('pass') is False else 'INCOMPLETE')}"
    )
    print(
        f"D3 Latency:     {'PASS' if latency.get('pass') else ('FAIL' if latency.get('pass') is False else 'INCOMPLETE')}"
    )
    print(
        f"D4 Coverage:    {'PASS' if coverage.get('pass') else ('FAIL' if coverage.get('pass') is False else 'INCOMPLETE')}"
    )
    print(
        f"D5 Regression:  {'PASS' if regression.get('pass') else ('FAIL' if regression.get('pass') is False else 'INCOMPLETE')}"
    )

    # Build report
    report = build_report(
        soak_records,
        smoke_records,
        politeness,
        parity,
        latency,
        coverage,
        regression,
        tunables,
        baseline_count,
    )

    # Write report
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)
    print(f"\nReport written to: {output_path}")

    # Optional plots
    if not args.no_plots:
        plots_dir = output_path.parent / "sprint-5-plots"
        generated = generate_plots(soak_records, smoke_records, plots_dir)
        if generated:
            print(f"Plots written to: {plots_dir}/")
            for p in generated:
                print(f"  {p}")

    # Exit with non-zero if any criterion failed
    if any(r.get("pass") is False for r in [politeness, parity, latency, coverage, regression]):
        print("\nVERDICT: NO-GO — see report for details.")
        sys.exit(1)
    elif all(r.get("pass") is True for r in [politeness, parity, latency, coverage, regression]):
        print("\nVERDICT: GO — all criteria passed.")
        sys.exit(0)
    else:
        print("\nVERDICT: INCOMPLETE — some criteria could not be evaluated.")
        sys.exit(2)


if __name__ == "__main__":
    main()
