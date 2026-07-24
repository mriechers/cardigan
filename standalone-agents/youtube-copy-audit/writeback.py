#!/usr/bin/env python3
"""Guarded YouTube write-back for the copy-audit POC.

Thin wrapper over the pbswi workspace's existing write executor
(.claude/skills/content/youtube-post/scripts/write_ops.py), which enforces the
triple lock: dry-run by default, explicit --live --confirm for a real write,
brand-channel identity gate before any mutation, and a JSONL mutation log.

Usage:
    # 1. Author the op file (only title/description/tags are editable):
    #    runs/<videoId>/op.json:
    #    {
    #      "op": "update_metadata",
    #      "target": "<videoId>",
    #      "changes": {"title": "...", "description": "...", "tags": ["..."]}
    #    }

    # 2. Preview (no API call is made):
    python writeback.py runs/<videoId>/op.json

    # 3. Live write — ONLY after a human has approved the diff in the
    #    copy audit report. Both flags are required:
    python writeback.py runs/<videoId>/op.json --live --confirm

The executor merges changes into the CURRENT live snippet (categoryId etc. are
preserved) and refuses to run if the token doesn't authenticate as the PBS
Wisconsin brand channel. Mutations append to youtube-post's logs/mutations.jsonl
(override with $YOUTUBE_POST_LOG).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _import_write_ops():
    pbswi_root = Path(os.environ.get("PBSWI_ROOT", "~/Developer/pbswi")).expanduser()
    scripts_dir = pbswi_root / ".claude" / "skills" / "content" / "youtube-post" / "scripts"
    if not (scripts_dir / "write_ops.py").exists():
        sys.exit(
            f"write_ops.py not found under {scripts_dir}.\n"
            "Set PBSWI_ROOT to your pbswi workspace checkout — see README.md."
        )
    sys.path.insert(0, str(scripts_dir))
    import write_ops  # noqa: PLC0415

    return write_ops


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("op_file", help="Path to the op JSON (see docstring)")
    parser.add_argument("--live", action="store_true", help="Disable dry-run (still requires --confirm)")
    parser.add_argument("--confirm", action="store_true", help="Confirm the live write (still requires --live)")
    args = parser.parse_args(argv)

    op = json.loads(Path(args.op_file).read_text(encoding="utf-8"))
    if op.get("op") != "update_metadata":
        sys.exit(
            f"POC only supports op=update_metadata (got {op.get('op')!r}). "
            "Playlist/thumbnail ops are out of scope here."
        )
    disallowed = set(op.get("changes", {})) - {"title", "description", "tags"}
    if disallowed:
        sys.exit(f"Disallowed snippet fields in changes: {sorted(disallowed)}")

    write_ops = _import_write_ops()
    token_path = os.environ.get("YOUTUBE_TOKEN_PATH", write_ops.DEFAULT_TOKEN_PATH)
    result = write_ops.execute_operation(
        op,
        confirm=args.confirm,
        dry_run=not args.live,
        token_path=token_path,
    )
    print(json.dumps(result, indent=2))
    status = result.get("status")
    if status == "dry_run":
        print(
            "\nNo API call was made. To commit after human approval:\n"
            f"  python writeback.py {args.op_file} --live --confirm",
            file=sys.stderr,
        )
    # Known-good outcomes: a preview ("dry_run") or a real write ("committed").
    # Live failures already raise (identity gate / HTTP errors) and exit non-zero;
    # branching here also catches any future non-raising error status so callers
    # and CI can detect a failed write instead of a silent exit 0.
    return 0 if status in {"dry_run", "committed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
