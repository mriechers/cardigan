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
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import api.services.llm as llm_mod
from api.services.completeness import check_completeness
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


def _write_report(out_dir: Path, label: str, transcript_name: str, results: list,
                  baseline: dict, completeness: dict | None) -> Path:
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
    ap.add_argument("--phases", default=",".join(PHASE_ORDER),
                    help="Comma list in dependency order.")
    ap.add_argument("--content-type", default="full", choices=["full", "short"])
    ap.add_argument("--label", default=None, help="Run label (e.g. model id).")
    ap.add_argument("--baseline-manifest", default=None)
    ap.add_argument("--context-dir", default=None,
                    help="Pre-load {phase}_output.md files from this dir as FIXED upstream "
                         "context (e.g. OUTPUT/eval/baseline_20). Lets you sweep a single "
                         "phase's model on identical inputs, decoupled from upstream quality.")
    ap.add_argument("--out", default="OUTPUT/eval")
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
                print(f"     ok  in={r['input_tokens']} out={r['output_tokens']} "
                      f"{r['wall_s']}s ({r['words']} words)")
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

    metrics = {
        "label": label,
        "transcript": str(tpath),
        "backend": args.backend,
        "phases": [{k: v for k, v in r.items() if k != "content"} for r in results],
        "completeness": completeness,
        "baseline_manifest": args.baseline_manifest,
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    report = _write_report(out_dir, label, tpath.name, results, baseline, completeness)

    print(f"\nMetrics: {out_dir}/metrics.json")
    print(f"Report:  {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
