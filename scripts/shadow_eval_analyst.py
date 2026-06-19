#!/usr/bin/env python3
"""Shadow-eval the analyst phase across LLM backends.

Runs Cardigan's *real* analyst prompt (system prompt from prompts/analyst.md +
the worker's user-prompt scaffold) against two or more backends on the same
transcript, then reports cost/tokens/latency and writes each analysis to a file
for side-by-side comparison.

This is the "don't trust-flip" first experiment for the local-dougie backend:
compare its analyst output to the incumbent openrouter-cheapskate on a real
transcript before routing any production phase to it.

Usage:
    python scripts/shadow_eval_analyst.py \
        --transcript transcripts/examples/EXAMPLE_GlassTree_ForClaude.txt \
        --backends openrouter-cheapskate,local-dougie

Requires the target backends to be reachable (for local-dougie, the MLX server
must be up and DOUGIE_ENDPOINT/endpoint pointed at it). It does NOT touch the
jobs DB — LLM event logging and Langfuse tracing are stubbed out for the run.
"""

import argparse
import asyncio
import difflib
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import api.services.llm as llm_mod
from api.services.worker import JobWorker


async def _noop_log_event(*_args, **_kwargs):
    return None


class _NoLangfuse:
    def is_available(self) -> bool:
        return False


async def _run_one(worker: JobWorker, backend: str, messages: list) -> dict:
    """Run the analyst phase on one backend; never raise (capture errors)."""
    start = time.time()
    try:
        resp = await worker.llm.chat(messages=messages, backend=backend, phase="analyst")
        return {
            "backend": backend,
            "ok": True,
            "model": resp.model,
            "cost": resp.cost,
            "input_tokens": resp.input_tokens,
            "output_tokens": resp.output_tokens,
            "duration_s": round(time.time() - start, 1),
            "content": resp.content,
        }
    except Exception as e:  # noqa: BLE001 - we want every backend's verdict
        return {
            "backend": backend,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "duration_s": round(time.time() - start, 1),
        }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transcript",
        default="transcripts/examples/EXAMPLE_GlassTree_ForClaude.txt",
        help="Path to a transcript file to analyze.",
    )
    parser.add_argument(
        "--backends",
        default="openrouter-cheapskate,local-dougie",
        help="Comma-separated backend names from config/llm-config.json.",
    )
    parser.add_argument("--content-type", default="full", choices=["full", "short"])
    parser.add_argument("--out", default="OUTPUT/shadow_eval", help="Directory for per-backend analyses.")
    args = parser.parse_args()

    transcript_path = Path(args.transcript)
    if not transcript_path.exists():
        print(f"ERROR: transcript not found: {transcript_path}")
        return 1
    transcript = transcript_path.read_text()
    backends = [b.strip() for b in args.backends.split(",") if b.strip()]

    worker = JobWorker()
    context = {"transcript": transcript, "content_type": args.content_type}
    system_prompt = worker._load_agent_prompt("analyst")
    user_message = worker._build_phase_prompt("analyst", context)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    print(f"Transcript: {transcript_path} ({len(transcript):,} chars)")
    print(f"Backends:   {', '.join(backends)}")
    print(f"Prompt:     system={len(system_prompt):,} chars, user={len(user_message):,} chars\n")

    # Keep the run DB-free and trace-free: stub event logging + Langfuse.
    with ExitStack() as stack:
        stack.enter_context(patch.object(llm_mod, "log_event", _noop_log_event))
        stack.enter_context(patch.object(llm_mod, "get_langfuse_client", lambda: _NoLangfuse()))
        results = []
        for backend in backends:
            print(f"  → running analyst on {backend} ...", flush=True)
            results.append(await _run_one(worker, backend, messages))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("\n=== Summary ===")
    header = f"{'backend':<24} {'model':<32} {'cost':>9} {'in':>7} {'out':>7} {'sec':>6} {'chars':>7}"
    print(header)
    print("-" * len(header))
    for r in results:
        if not r["ok"]:
            print(f"{r['backend']:<24} FAILED ({r['duration_s']}s): {r['error']}")
            continue
        out_file = out_dir / f"analyst_{r['backend']}.md"
        out_file.write_text(r["content"])
        print(
            f"{r['backend']:<24} {r['model'][:32]:<32} ${r['cost']:>8.5f} "
            f"{r['input_tokens']:>7} {r['output_tokens']:>7} {r['duration_s']:>6} {len(r['content']):>7}"
        )

    ok = [r for r in results if r["ok"]]
    if len(ok) >= 2:
        a, b = ok[0], ok[1]
        ratio = difflib.SequenceMatcher(None, a["content"], b["content"]).ratio()
        print(f"\nLexical similarity ({a['backend']} vs {b['backend']}): {ratio:.1%}")
        print(f"Outputs written to {out_dir}/ — open them side by side for the qualitative read.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
