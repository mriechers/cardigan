#!/usr/bin/env python3
"""Verify that web/package.json and the latest git tag agree on the release line.

Source of truth: the latest annotated `vX.Y.Z` git tag.
- pyproject.toml declares version dynamic via setuptools_scm — no static value to check.
- web/package.json carries a static version string (npm requires it).
- The script enforces that package.json's MAJOR.MINOR matches the latest tag's MAJOR.MINOR.
  Patch-level drift is tolerated: package.json doesn't need to bump on every backend patch.

Run locally before opening a release PR:
    python3 scripts/check_versions.py

Exits non-zero if the release lines have diverged.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def latest_tag_version() -> str | None:
    """Return the most recent vMAJOR.MINOR.PATCH tag reachable from HEAD, or None."""
    try:
        out = subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        return None
    return out.lstrip("v") if out else None


def package_json_version() -> str:
    data = json.loads((REPO_ROOT / "web" / "package.json").read_text())
    return data["version"]


def major_minor(version: str) -> tuple[int, int]:
    match = re.match(r"(\d+)\.(\d+)", version)
    if not match:
        raise ValueError(f"Cannot parse MAJOR.MINOR from {version!r}")
    return int(match.group(1)), int(match.group(2))


def main() -> int:
    pkg = package_json_version()
    tag = latest_tag_version()

    print(f"web/package.json version: {pkg}")
    print(f"latest git tag:           {tag or '(none)'}")

    if tag is None:
        print("WARN: no git tag found — skipping consistency check.")
        return 0

    if major_minor(pkg) != major_minor(tag):
        print(
            f"ERROR: web/package.json ({pkg}) and git tag (v{tag}) " f"are on different MAJOR.MINOR lines.",
            file=sys.stderr,
        )
        print("Bump web/package.json to match the release line.", file=sys.stderr)
        return 1

    print("OK: release lines agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
