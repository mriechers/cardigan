#!/usr/bin/env python3
"""Compare phase outputs across different model runs.

Usage:
    # List available versions for a job
    ./venv/bin/python scripts/compare_outputs.py list 168

    # Compare current vs previous for a phase
    ./venv/bin/python scripts/compare_outputs.py diff 168 formatter

    # Compare two specific versions
    ./venv/bin/python scripts/compare_outputs.py diff 168 formatter --v1 20260115_150000 --v2 current

    # Show unified diff (default is side-by-side summary)
    ./venv/bin/python scripts/compare_outputs.py diff 168 formatter --unified
"""

import argparse
import re
import sqlite3
from datetime import datetime
from difflib import SequenceMatcher, unified_diff
from pathlib import Path


def get_project_path(job_id: int) -> Path | None:
    """Get project path for a job from database."""
    db_path = Path(__file__).parent.parent / "dashboard.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT project_path, media_id FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    project_path = Path(__file__).parent.parent / row[0]
    return project_path if project_path.exists() else None


def parse_provenance(content: str) -> dict:
    """Extract provenance info from output header."""
    match = re.match(r"<!-- model: ([^|]+) \| tier: ([^|]+) \| cost: \$([^|]+) \| tokens: (\d+) -->", content)
    if match:
        return {
            "model": match.group(1).strip(),
            "tier": match.group(2).strip(),
            "cost": float(match.group(3)),
            "tokens": int(match.group(4)),
        }
    return {}


def list_versions(job_id: int):
    """List all available output versions for a job."""
    project_path = get_project_path(job_id)
    if not project_path:
        print(f"Job {job_id} not found or project path doesn't exist")
        return

    print(f"Outputs for Job {job_id} ({project_path.name})")
    print("=" * 60)

    phases = ["analyst", "formatter", "seo", "validator", "timestamp"]

    for phase in phases:
        current = project_path / f"{phase}_output.md"
        if not current.exists():
            continue

        print(f"\n{phase}:")

        # Current version
        content = current.read_text()
        prov = parse_provenance(content)
        mod_time = datetime.fromtimestamp(current.stat().st_mtime)

        if prov:
            print(f"  current: {mod_time:%Y-%m-%d %H:%M} | {prov.get('tier', '?')} | {prov.get('model', '?')}")
        else:
            print(f"  current: {mod_time:%Y-%m-%d %H:%M} | (no provenance header)")

        # Previous versions
        prev_files = sorted(project_path.glob(f"{phase}_output.*.prev.md"), reverse=True)
        for pf in prev_files[:5]:  # Show last 5
            timestamp = pf.stem.split(".")[1]
            content = pf.read_text()
            prov = parse_provenance(content)

            if prov:
                print(f"  {timestamp}: {prov.get('tier', '?')} | {prov.get('model', '?')}")
            else:
                print(f"  {timestamp}: (no provenance header)")

        if len(prev_files) > 5:
            print(f"  ... and {len(prev_files) - 5} more")


def compare_outputs(job_id: int, phase: str, v1: str = None, v2: str = "current", unified: bool = False):
    """Compare two versions of a phase output."""
    project_path = get_project_path(job_id)
    if not project_path:
        print(f"Job {job_id} not found or project path doesn't exist")
        return

    # Resolve file paths
    if v2 == "current":
        file2 = project_path / f"{phase}_output.md"
    else:
        file2 = project_path / f"{phase}_output.{v2}.prev.md"

    if v1 is None:
        # Find most recent previous version
        prev_files = sorted(project_path.glob(f"{phase}_output.*.prev.md"), reverse=True)
        if not prev_files:
            print(f"No previous versions found for {phase}")
            return
        file1 = prev_files[0]
        v1 = file1.stem.split(".")[1]
    else:
        file1 = project_path / f"{phase}_output.{v1}.prev.md"

    if not file1.exists():
        print(f"Version {v1} not found: {file1}")
        return
    if not file2.exists():
        print(f"Version {v2} not found: {file2}")
        return

    content1 = file1.read_text()
    content2 = file2.read_text()

    prov1 = parse_provenance(content1)
    prov2 = parse_provenance(content2)

    # Strip provenance headers for diff
    if content1.startswith("<!--"):
        content1 = content1.split("\n", 1)[1] if "\n" in content1 else content1
    if content2.startswith("<!--"):
        content2 = content2.split("\n", 1)[1] if "\n" in content2 else content2

    print(f"Comparing {phase} outputs for Job {job_id}")
    print("=" * 60)
    print(f"Version A ({v1}):")
    if prov1:
        print(f"  Model: {prov1.get('model')} | Tier: {prov1.get('tier')} | Cost: ${prov1.get('cost', 0):.4f}")
    else:
        print("  (no provenance)")

    print(f"Version B ({v2}):")
    if prov2:
        print(f"  Model: {prov2.get('model')} | Tier: {prov2.get('tier')} | Cost: ${prov2.get('cost', 0):.4f}")
    else:
        print("  (no provenance)")
    print()

    # Calculate similarity
    similarity = SequenceMatcher(None, content1, content2).ratio()
    print(f"Similarity: {similarity:.1%}")
    print()

    if unified:
        # Show unified diff
        diff = unified_diff(
            content1.splitlines(keepends=True),
            content2.splitlines(keepends=True),
            fromfile=f"A ({v1})",
            tofile=f"B ({v2})",
        )
        print("".join(diff))
    else:
        # Show summary of differences
        lines1 = content1.splitlines()
        lines2 = content2.splitlines()

        print(f"Lines: {len(lines1)} → {len(lines2)} ({len(lines2) - len(lines1):+d})")
        print(f"Chars: {len(content1)} → {len(content2)} ({len(content2) - len(content1):+d})")
        print()

        if similarity < 0.95:
            print("Key differences (use --unified for full diff):")
            # Show first few differing lines
            diff_count = 0
            for i, (l1, l2) in enumerate(zip(lines1, lines2)):
                if l1 != l2 and diff_count < 10:
                    print(f"  Line {i+1}:")
                    print(f"    A: {l1[:80]}{'...' if len(l1) > 80 else ''}")
                    print(f"    B: {l2[:80]}{'...' if len(l2) > 80 else ''}")
                    diff_count += 1
        else:
            print("Outputs are nearly identical")


def main():
    parser = argparse.ArgumentParser(description="Compare phase outputs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # List command
    list_parser = subparsers.add_parser("list", help="List available versions")
    list_parser.add_argument("job_id", type=int, help="Job ID")

    # Diff command
    diff_parser = subparsers.add_parser("diff", help="Compare two versions")
    diff_parser.add_argument("job_id", type=int, help="Job ID")
    diff_parser.add_argument("phase", help="Phase name (formatter, seo, etc.)")
    diff_parser.add_argument("--v1", help="First version timestamp (default: most recent prev)")
    diff_parser.add_argument("--v2", default="current", help="Second version (default: current)")
    diff_parser.add_argument("--unified", "-u", action="store_true", help="Show unified diff")

    args = parser.parse_args()

    if args.command == "list":
        list_versions(args.job_id)
    elif args.command == "diff":
        compare_outputs(args.job_id, args.phase, args.v1, args.v2, args.unified)


if __name__ == "__main__":
    main()
