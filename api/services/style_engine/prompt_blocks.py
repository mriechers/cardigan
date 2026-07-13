"""Prompt-block token renderer for house-style prompt injection.

Pure stdlib + the style_engine package (no worker imports). Substitutes
``{{style:KEY}}`` tokens in agent prompt text with rule text sourced from
the ``prompt_blocks`` section of the house-style rules YAML
(``config/house_style.yaml`` by default). This is the mechanism that lets
agent prompts consume rule text FROM the YAML instead of duplicating it
inline in ``prompts/*.md``.

Fail-fast semantic (a plan-mandated exception to the fail-open posture used
elsewhere in the style engine): if prompt text contains a
``{{style:...}}`` token that cannot be rendered -- missing key, missing
``prompt_blocks`` section, missing/malformed rules file -- this module
raises :class:`PromptBlockError`. It never silently strips or leaves the
token in place; a prompt that references rules must not run without them.

Text containing NO tokens is returned unchanged WITHOUT touching the rules
file at all (short-circuit on ``"{{style:" not in text``), so prompts that
don't use tokens can never fail here -- this is the no-op guarantee that
holds until a later task adds tokens to a prompt file.
"""

from __future__ import annotations

import re
from pathlib import Path

from api.services.style_engine.rules import (
    DEFAULT_RULES_PATH,
    StyleRules,
    StyleRulesError,
    load_rules,
)

TOKEN_RE = re.compile(r"\{\{style:([a-zA-Z0-9_.-]+)\}\}")
# Same token, plus an optional immediately-following newline -- used by the
# strip fallback so a token that sits alone on its line doesn't leave a stray
# blank line behind when removed.
_STRIP_RE = re.compile(TOKEN_RE.pattern + r"\n?")


class PromptBlockError(Exception):
    """Raised when a ``{{style:KEY}}`` token in prompt text cannot be rendered."""


def render_prompt_blocks(
    text: str,
    rules: StyleRules | None = None,
    profile: str = "full",
    rules_path: str | Path | None = None,
) -> str:
    """Substitute ``{{style:KEY}}`` tokens in ``text``.

    - No token in ``text`` -> return ``text`` unchanged WITHOUT touching
      ``rules``/``rules_path`` at all.
    - Tokens present: uses ``rules`` if given, else loads
      ``load_rules(rules_path or DEFAULT_RULES_PATH)``. Loading errors
      propagate as :class:`PromptBlockError` (chaining the original
      ``StyleRulesError``).
    - Each ``KEY`` looks up ``rules.raw["prompt_blocks"][KEY]``, a mapping
      of profile -> text (e.g. ``{"full": "...", "slim": "..."}``).
    - Requested ``profile`` missing on a block -> falls back to ``"full"``.
      Neither present, or ``KEY`` absent entirely, or the ``prompt_blocks``
      section is missing entirely -> :class:`PromptBlockError` naming the
      key and the rules source.
    - Rendered block text is inserted verbatim, with exactly one trailing
      newline stripped (to avoid doubled blank lines when the token itself
      sits on its own line). Tokens appearing inside rendered block text
      are NOT re-expanded -- substitution is a single pass over the
      original text, which also prevents recursion.
    """
    if "{{style:" not in text:
        return text

    if rules is not None:
        source_desc = "caller-supplied StyleRules"
    else:
        path = rules_path if rules_path is not None else DEFAULT_RULES_PATH
        source_desc = str(path)
        try:
            rules = load_rules(path)
        except StyleRulesError as exc:
            raise PromptBlockError(
                f"Could not load house style rules from {source_desc!r} to render prompt-block tokens: {exc}"
            ) from exc

    prompt_blocks = rules.raw.get("prompt_blocks")
    if not isinstance(prompt_blocks, dict):
        raise PromptBlockError(f"Prompt text contains style tokens but {source_desc} has no 'prompt_blocks' section")

    def _substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        block = prompt_blocks.get(key)
        if not isinstance(block, dict):
            raise PromptBlockError(f"Unknown style prompt block key {key!r} (no prompt_blocks.{key} in {source_desc})")
        rendered = block.get(profile, block.get("full"))
        if rendered is None:
            raise PromptBlockError(
                f"Style prompt block {key!r} in {source_desc} has neither profile {profile!r} " "nor a 'full' fallback"
            )
        rendered = str(rendered)
        if rendered.endswith("\n"):
            rendered = rendered[:-1]
        return rendered

    return TOKEN_RE.sub(_substitute, text)


def strip_style_tokens(text: str) -> str:
    """Remove every ``{{style:KEY}}`` token from ``text``.

    A token that sits alone on its own line also consumes the one trailing
    newline that follows it, so removal leaves no stray blank line; an inline
    token is removed in place. This is the graceful-degradation fallback the
    worker's ``_load_agent_prompt`` uses when :func:`render_prompt_blocks`
    cannot render (missing/corrupt rules file): the phase runs on the raw
    prompt WITHOUT literal ``{{style:...}}`` tokens leaking to the LLM, instead
    of failing the job. Contrast the fail-fast posture of
    :func:`render_prompt_blocks` itself -- the strip only happens after that
    raise is caught at the call site.
    """
    return _STRIP_RE.sub("", text)


def validate_prompt_blocks(
    prompt_dir: str | Path,
    rules_path: str | Path | None = None,
    profiles: tuple[str, ...] = ("full", "slim"),
) -> list[str]:
    """Render every token-bearing ``*.md`` prompt under ``prompt_dir``.

    For each prompt file that contains a ``{{style:...}}`` token, renders it
    against ``rules_path`` (default ``config/house_style.yaml``) for every
    profile in ``profiles``, raising the first :class:`PromptBlockError`
    encountered. This is the boot-time guard (wired into the app lifespan) that
    surfaces a missing/corrupt house-style YAML loudly at startup rather than
    letting it fail every job at runtime -- the deploy-time counterpart to
    ``_load_agent_prompt``'s runtime graceful fallback.

    Returns the sorted list of prompt file names that carried tokens and
    validated cleanly (token-free prompts are skipped, mirroring
    :func:`render_prompt_blocks`' short-circuit).
    """
    validated: list[str] = []
    for md in sorted(Path(prompt_dir).glob("*.md")):
        text = md.read_text()
        if "{{style:" not in text:
            continue
        for profile in profiles:
            render_prompt_blocks(text, profile=profile, rules_path=rules_path)
        validated.append(md.name)
    return validated


def resolve_prompt_profile(routing_cfg: dict, phase_name: str) -> str:
    """Resolve which prompt-block profile ("full" or "slim") a phase should render.

    ``routing_cfg`` is the ``routing.style_engine`` config dict (an empty
    dict when that block is absent from ``llm-config.json``, which is the
    case today -- see module docstring). Pure function; factored out of the
    worker so the profile-selection logic is testable without constructing
    a Worker or a DB connection.

    - Style engine disabled (``routing_cfg.get("enabled")`` falsy) -> "full".
    - Otherwise looks up ``routing_cfg["phases"][phase_name]``, reading the
      ``"lint"`` key for the ``validator`` phase and ``"post"`` for every
      other phase (default ``"off"``). Mode ``"enforce"`` -> "slim";
      anything else (``"off"``, ``"shadow"``, missing) -> "full".
    """
    if not routing_cfg.get("enabled"):
        return "full"
    phase_cfg = routing_cfg.get("phases", {}).get(phase_name, {})
    mode_key = "lint" if phase_name == "validator" else "post"
    mode = phase_cfg.get(mode_key, "off")
    return "slim" if mode == "enforce" else "full"
