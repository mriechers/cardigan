#!/usr/bin/env python3
"""Full-pipeline shadow eval on one backend, with baseline comparison.

Runs analyst -> formatter -> seo -> validator (-> timestamp) in order on a single
backend (e.g. ``local-llm`` / oMLX), threading each phase's output into the next
exactly like ``JobWorker.process_job`` does (``context["{phase}_output"]``), and
capturing per-phase model / tokens / duration / output. DB- and Langfuse-free
(mocks ``log_event`` + ``get_langfuse_client``), so it runs without the API,
worker, queue, or DB.

**Single-pass per phase (no chunking).** For long transcripts the *production*
formatter chunks (`routing.chunking`, threshold 3000 words); this harness sends
the whole transcript in one call. That's flagged in the report — use this for a
controlled per-phase local-vs-baseline read; drive a real worker job
(`LLM_CONFIG_PATH` scratch config, all `phase_backends` -> local-llm) when
chunking parity matters.

Usage:
    export LOCAL_LLM_API_KEY=… LOCAL_LLM_MODEL=<oMLX id> LOCAL_LLM_ENDPOINT=http://127.0.0.1:8000/v1
    PYTHONPATH=. ./venv/bin/python scripts/eval_pipeline.py \
        --transcript transcripts/6POL0201.srt \
        --backend local-llm --label gemma-4-26B \
        --baseline-manifest OUTPUT/eval/baseline_20/manifest.json
"""

import argparse
import asyncio
import json
import time
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import api.services.llm as llm_mod
from api.services.completeness import check_completeness
from api.services.style_engine import (
    StyleRulesError,
    build_canonical,
    check_field_limits,
    extract_proper_nouns,
    extract_seo_fields,
    load_rules,
    scan_forbidden,
    scan_person_voice,
    splice_seo_fields,
    to_down_style,
)
from api.services.worker import JobWorker

PHASE_ORDER = ["analyst", "formatter", "seo", "validator", "timestamp"]


async def _noop_log_event(*_a, **_k):
    return None


class _NoLangfuse:
    def is_available(self) -> bool:
        return False


def _words(text: str) -> int:
    return len(text.split())


async def _run_phase(worker: JobWorker, backend: str, phase: str, context: dict) -> dict:
    """Run one phase's real prompt on the backend; never raise."""
    system_prompt = worker._load_agent_prompt(phase)
    user_message = worker._build_phase_prompt(phase, context)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    start = time.time()
    try:
        resp = await worker.llm.chat(messages=messages, backend=backend, phase=phase)
        return {
            "phase": phase,
            "ok": True,
            "model": resp.model,
            "input_tokens": resp.input_tokens,
            "output_tokens": resp.output_tokens,
            "total_tokens": resp.total_tokens,
            "cost": resp.cost,
            "duration_ms": resp.duration_ms,
            "wall_s": round(time.time() - start, 1),
            "prompt_chars": len(system_prompt) + len(user_message),
            "chars": len(resp.content),
            "words": _words(resp.content),
            "content": resp.content,
        }
    except Exception as e:  # noqa: BLE001 — capture every phase's verdict
        return {
            "phase": phase,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "wall_s": round(time.time() - start, 1),
        }


def _load_baseline(path: str | None) -> dict:
    if not path:
        return {}
    m = json.loads(Path(path).read_text())
    phases = {}
    for p in m.get("phases", []):
        phases[p.get("phase") or p.get("name")] = p
    return {
        "phases": phases,
        "total_cost": m.get("total_cost"),
        "total_tokens": m.get("total_tokens"),
    }


def _run_style_checks(field_values: dict[str, str], rules) -> list:
    """PRE/POST check bundle: limits + forbidden phrases + person voice, run
    on each field VALUE individually (never on the whole report document --
    keyword-strategy prose legitimately mentions words like "discover")."""
    violations = list(check_field_limits(field_values, rules, phase="seo"))
    for field_name, value in field_values.items():
        violations += scan_forbidden(value, rules, "seo", field=field_name)
        violations += scan_person_voice(value, rules, "seo", field=field_name)
    return violations


def compute_style_report(seo_text: str, analyst_text: str | None, rules) -> dict:
    """Pure, LLM-free style-check computation for the seo phase's output.

    Extracts SEO fields from ``seo_text``, runs the deterministic PRE checks
    (``check_field_limits`` + ``scan_forbidden`` + ``scan_person_voice``) on
    each extracted field VALUE, builds the down-style canonical map from
    ``rules`` plus any proper nouns found in ``analyst_text``'s speaker
    table, normalizes the title (title-only -- that's the enforce-tier scope
    for seo), and re-runs the same checks POST-normalization with the
    normalized title substituted in.

    Returns the dict written under ``metrics.json``'s ``style.seo`` key: on
    success, ``{"fields_extracted", "violations_pre", "violations_post",
    "title_raw", "title_normalized", "title_changed", "proper_nouns_used"}``;
    when ``seo_text`` is empty or has no extractable title (nothing to
    normalize), ``{"skipped": True, "reason": "..."}`` instead -- this never
    raises.
    """
    if not seo_text or not seo_text.strip():
        return {"skipped": True, "reason": "seo output missing or empty"}

    fields = extract_seo_fields(seo_text)
    if fields.title is None:
        return {"skipped": True, "reason": "seo output unparseable: no title field found"}

    field_values: dict[str, str] = {"title": fields.title.value}
    if fields.short_description is not None:
        field_values["short_description"] = fields.short_description.value
    if fields.long_description is not None:
        field_values["long_description"] = fields.long_description.value
    fields_extracted = list(field_values.keys())

    violations_pre = _run_style_checks(field_values, rules)

    nouns = extract_proper_nouns(analyst_text or "")
    canonical = build_canonical(rules, nouns)
    title_raw = fields.title.value
    title_normalized = to_down_style(title_raw, canonical)

    post_values = dict(field_values)
    post_values["title"] = title_normalized
    violations_post = _run_style_checks(post_values, rules)

    return {
        "fields_extracted": fields_extracted,
        "violations_pre": [v.to_dict() for v in violations_pre],
        "violations_post": [v.to_dict() for v in violations_post],
        "title_raw": title_raw,
        "title_normalized": title_normalized,
        "title_changed": title_normalized != title_raw,
        "proper_nouns_used": nouns,
    }


def _style_report_md_lines(style_report: dict) -> list[str]:
    """Render the "## Style report" section for report.md from the dict
    produced by ``compute_style_report`` (keyed by phase, e.g. "seo")."""
    lines = ["", "## Style report"]
    for phase_name, phase_style in style_report.items():
        lines += ["", f"### {phase_name}"]
        if phase_style.get("skipped"):
            lines.append(f"- Skipped: {phase_style.get('reason', 'unknown')}")
            continue

        fields_extracted = ", ".join(phase_style.get("fields_extracted", [])) or "(none)"
        nouns = phase_style.get("proper_nouns_used") or []
        lines.append(f"- Fields extracted: {fields_extracted}")
        lines.append(f"- Proper nouns used: {', '.join(nouns) if nouns else '(none)'}")
        lines.append(f"- Title (raw): `{phase_style.get('title_raw')}`")
        lines.append(
            f"- Title (normalized): `{phase_style.get('title_normalized')}` "
            f"(changed: {phase_style.get('title_changed')})"
        )

        pre = phase_style.get("violations_pre", [])
        post = phase_style.get("violations_post", [])
        pre_counts = Counter(v["rule_id"] for v in pre)
        post_counts = Counter(v["rule_id"] for v in post)
        rule_ids = sorted(set(pre_counts) | set(post_counts))
        if rule_ids:
            lines += ["", "| rule_id | violations (pre) | violations (post) |", "|---|--:|--:|"]
            for rid in rule_ids:
                lines.append(f"| {rid} | {pre_counts.get(rid, 0)} | {post_counts.get(rid, 0)} |")
        else:
            lines.append("- No violations pre- or post-normalization.")
    return lines


def _write_report(
    out_dir: Path,
    label: str,
    transcript_name: str,
    results: list,
    baseline: dict,
    completeness: dict | None,
    style_report: dict | None = None,
) -> Path:
    bphases = baseline.get("phases", {})
    lines = [
        f"# Local pipeline eval — {label}",
        "",
        f"- Transcript: `{transcript_name}`",
        f"- Backend model: `{next((r['model'] for r in results if r.get('ok')), '?')}`",
        "- Local `cost` is $0 (self-hosted); compare **tokens** + **wall-clock**.",
        "- **Caveat:** single-pass per phase (no chunking); prod formatter chunks long transcripts.",
        "",
        "## Per-phase: local vs baseline",
        "",
        "| Phase | Local model | L in-tok | L out-tok | L wall s | L out words | Base model | Base tok | Base $ |",
        "|---|---|--:|--:|--:|--:|---|--:|--:|",
    ]
    for r in results:
        b = bphases.get(r["phase"], {})
        bmodel = (b.get("model") or "—").split("/")[-1]
        if r.get("ok"):
            lines.append(
                f"| {r['phase']} | {r['model'].split('/')[-1]} | {r['input_tokens']} | "
                f"{r['output_tokens']} | {r['wall_s']} | {r['words']} | {bmodel} | "
                f"{b.get('tokens','—')} | {b.get('cost','—')} |"
            )
        else:
            lines.append(
                f"| {r['phase']} | **FAILED** | — | — | {r['wall_s']} | — | {bmodel} | "
                f"{b.get('tokens','—')} | {b.get('cost','—')} | "
            )
            lines.append(f"| | `{r['error']}` | | | | | | | |")
    total_wall = round(sum(r.get("wall_s", 0) for r in results), 1)
    total_out = sum(r.get("output_tokens", 0) for r in results if r.get("ok"))
    lines += [
        "",
        f"- **Local total wall-clock:** {total_wall}s across {len([r for r in results if r.get('ok')])} phases; "
        f"local output tokens: {total_out}.",
        f"- **Baseline total:** {baseline.get('total_tokens','—')} tokens, ${baseline.get('total_cost','—')}.",
    ]
    if completeness:
        lines += [
            "",
            "## Formatter completeness (local output vs source)",
            f"- coverage_ratio: **{completeness.get('coverage_ratio')}** "
            f"(threshold {completeness.get('threshold')}); "
            f"source_words={completeness.get('source_word_count')}, "
            f"output_words={completeness.get('output_word_count')}; "
            f"complete={completeness.get('is_complete')}.",
        ]
    if style_report:
        lines += _style_report_md_lines(style_report)
    lines += [
        "",
        "## Outputs for qualitative (house-style) review",
        f"- Local: `{out_dir}/<phase>_output.md`",
        "- Baseline: `OUTPUT/eval/baseline_20/<phase>_output.md`",
    ]
    report = out_dir / "report.md"
    report.write_text("\n".join(lines) + "\n")
    return report


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--backend", default="local-llm")
    ap.add_argument("--phases", default=",".join(PHASE_ORDER), help="Comma list in dependency order.")
    ap.add_argument("--content-type", default="full", choices=["full", "short"])
    ap.add_argument("--label", default=None, help="Run label (e.g. model id).")
    ap.add_argument("--baseline-manifest", default=None)
    ap.add_argument(
        "--context-dir",
        default=None,
        help="Pre-load {phase}_output.md files from this dir as FIXED upstream "
        "context (e.g. OUTPUT/eval/baseline_20). Lets you sweep a single "
        "phase's model on identical inputs, decoupled from upstream quality.",
    )
    ap.add_argument("--out", default="OUTPUT/eval")
    ap.add_argument(
        "--style-report",
        action="store_true",
        help="Run the deterministic house-style checks (pre/post normalization) "
        "on the seo phase's output and record them under metrics.json's "
        "'style' key + a '## Style report' section in report.md.",
    )
    ap.add_argument(
        "--rules",
        default="config/house_style.yaml",
        help="House-style rules YAML path for --style-report/--emit-normalized.",
    )
    ap.add_argument(
        "--emit-normalized",
        action="store_true",
        help="Implies --style-report; also writes seo_output.normalized.md with "
        "the normalized title spliced back into the full seo report.",
    )
    args = ap.parse_args()

    tpath = Path(args.transcript)
    if not tpath.exists():
        print(f"ERROR: transcript not found: {tpath}")
        return 1
    transcript = tpath.read_text()
    is_srt = tpath.suffix.lower() == ".srt"
    phases = [p.strip() for p in args.phases.split(",") if p.strip()]

    worker = JobWorker()
    context: dict = {"transcript": transcript, "content_type": args.content_type}
    if is_srt:
        context["srt_content"] = transcript

    # Fixed upstream context for isolated per-phase sweeps: load prior phases'
    # outputs (e.g. the baseline's) so a swept phase sees identical inputs.
    if args.context_dir:
        cdir = Path(args.context_dir)
        for f in cdir.glob("*_output.md"):
            phase_name = f.name.replace("_output.md", "")
            text = f.read_text()
            # strip a leading provenance header line if present
            if text.startswith("<!--"):
                text = text.split("-->", 1)[-1].lstrip("\n")
            context[f"{phase_name}_output"] = text
            print(f"  (loaded fixed upstream: {phase_name}_output <- {f})")

    label = args.label or args.backend
    out_dir = Path(args.out) / f"local_{label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    baseline = _load_baseline(args.baseline_manifest)

    print(f"Transcript: {tpath} ({len(transcript):,} chars, srt={is_srt})")
    print(f"Backend:    {args.backend} | phases: {', '.join(phases)}\n")

    results = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(llm_mod, "log_event", _noop_log_event))
        stack.enter_context(patch.object(llm_mod, "get_langfuse_client", lambda: _NoLangfuse()))
        for phase in phases:
            print(f"  → {phase} on {args.backend} ...", flush=True)
            r = await _run_phase(worker, args.backend, phase, context)
            results.append(r)
            if r["ok"]:
                (out_dir / f"{phase}_output.md").write_text(r["content"])
                context[f"{phase}_output"] = r["content"]
                print(
                    f"     ok  in={r['input_tokens']} out={r['output_tokens']} " f"{r['wall_s']}s ({r['words']} words)"
                )
            else:
                print(f"     FAILED {r['wall_s']}s: {r['error']}")
                if phase in ("analyst", "formatter"):
                    print("     (downstream phases depend on this output — stopping chain)")
                    break

    completeness = None
    fmt = next((r for r in results if r["phase"] == "formatter" and r.get("ok")), None)
    if fmt:
        cr = check_completeness(fmt["content"], transcript, is_srt=is_srt)
        completeness = cr.to_dict()

    # --style-report / --emit-normalized: deterministic house-style checks on
    # the seo phase's output (--emit-normalized implies the same computation).
    style_report: dict | None = None
    if args.style_report or args.emit_normalized:
        seo_run = next((r for r in results if r["phase"] == "seo" and r.get("ok")), None)
        if seo_run is None:
            style_report = {"seo": {"skipped": True, "reason": "seo phase not present or failed in this run"}}
        else:
            try:
                style_rules = load_rules(args.rules)
            except StyleRulesError as e:
                style_report = {"seo": {"skipped": True, "reason": f"could not load rules ({args.rules}): {e}"}}
            else:
                # "context analyst output" -- context["analyst_output"] already
                # reflects this run's real analyst phase if it ran, or the
                # --context-dir preload if it didn't (see the loading loop above).
                analyst_text = context.get("analyst_output")
                style_report = {"seo": compute_style_report(seo_run["content"], analyst_text, style_rules)}

        if args.emit_normalized and seo_run is not None:
            seo_style = style_report["seo"]
            seo_fields = extract_seo_fields(seo_run["content"])
            title_normalized = None if seo_style.get("skipped") else seo_style.get("title_normalized")
            if seo_fields.title is not None and title_normalized is not None:
                normalized_doc = splice_seo_fields(seo_run["content"], seo_fields, {"title": title_normalized})
            else:
                # Nothing to splice (unparseable, or normalization was a no-op) --
                # still write the file, byte-identical, so downstream diffing
                # against seo_output.md stays uniform across runs.
                normalized_doc = seo_run["content"]
            norm_path = out_dir / "seo_output.normalized.md"
            norm_path.write_text(normalized_doc)
            print(f"Normalized SEO output: {norm_path}")

    metrics = {
        "label": label,
        "transcript": str(tpath),
        "backend": args.backend,
        "phases": [{k: v for k, v in r.items() if k != "content"} for r in results],
        "completeness": completeness,
        "baseline_manifest": args.baseline_manifest,
    }
    if style_report is not None:
        metrics["style"] = style_report
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    report = _write_report(out_dir, label, tpath.name, results, baseline, completeness, style_report)

    print(f"\nMetrics: {out_dir}/metrics.json")
    print(f"Report:  {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
