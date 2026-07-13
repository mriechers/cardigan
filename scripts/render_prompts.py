#!/usr/bin/env python3
"""CLI to render an agent prompt's {{style:KEY}} tokens for human inspection.

Reads `prompts/{phase}.md` raw, applies
`api.services.style_engine.render_prompt_blocks` with the given profile and
rules file, and prints the rendered result to stdout with a one-line header
comment listing which tokens were rendered (or noting none were found).
Pure argparse + style_engine import -- no worker import, so it can be run
standalone to preview what a prompt will look like once tokens are added,
without spinning up the DB-backed worker.

Usage (from project root, so the `api` package resolves):
    python -m scripts.render_prompts --phase seo [--profile full|slim] [--rules config/house_style.yaml]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from api.services.style_engine import PromptBlockError, render_prompt_blocks
from api.services.style_engine.prompt_blocks import TOKEN_RE

PROMPTS_DIR = Path("prompts")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render {{style:KEY}} tokens in an agent prompt file.")
    parser.add_argument("--phase", required=True, help="Phase name; reads prompts/{phase}.md")
    parser.add_argument(
        "--profile",
        default="full",
        choices=["full", "slim"],
        help="Prompt-block profile to render (default: full)",
    )
    parser.add_argument(
        "--rules",
        default="config/house_style.yaml",
        help="Path to the house-style rules YAML (default: config/house_style.yaml)",
    )
    args = parser.parse_args(argv)

    prompt_path = PROMPTS_DIR / f"{args.phase}.md"
    try:
        text = prompt_path.read_text()
    except OSError as exc:
        print(f"error: could not read {prompt_path}: {exc}", file=sys.stderr)
        return 1

    tokens = sorted(set(TOKEN_RE.findall(text)))

    try:
        rendered = render_prompt_blocks(text, profile=args.profile, rules_path=args.rules)
    except PromptBlockError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if tokens:
        header = f"<!-- rendered style tokens: {', '.join(tokens)} (profile={args.profile}) -->"
    else:
        header = "<!-- no style tokens found -->"

    print(header)
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
