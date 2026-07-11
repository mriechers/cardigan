"""Drift test: config/house_style.yaml vs. the SST write-path ground truth.

Loads the REAL production `config/house_style.yaml` (not synthetic fixture
data — see tests/test_style_rules.py for the loader/accessor unit tests
against synthetic YAML) and asserts its per-field character limits match
`WRITABLE_FIELDS` in mcp_server/server.py, which is what actually gates
Airtable writes. If these ever drift apart, the YAML's limits are lying to
the deterministic rule engine while the MCP write path enforces something
else — this test exists to catch that split before it ships.

This test only READS from `load_rules(...)` results (via the loader's typed
accessors) — the accessors return structures that alias the cached
`StyleRules` object, so mutating them would corrupt the shared cache for
every other consumer of `load_rules()` in the same process/test session.
"""

import ast
import re
from pathlib import Path

import pytest

from api.services.style_engine.casing import build_canonical, to_down_style
from api.services.style_engine.rules import StyleRulesError, load_rules

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "house_style.yaml"
MCP_SERVER_PATH = REPO_ROOT / "mcp_server" / "server.py"

# YAML field key -> WRITABLE_FIELDS key. Same names today; kept as an
# explicit map (not identity) so a future rename on either side fails loudly
# here instead of silently no-op'ing.
YAML_TO_WRITABLE_FIELD = {
    "title": "title",
    "short_description": "short_description",
    "long_description": "long_description",
}


def _writable_fields_via_import() -> dict[str, tuple]:
    """Import WRITABLE_FIELDS directly from mcp_server.server.

    This is the precedent already used by tests/test_mcp_tools.py — a
    direct top-level import of the same name works cleanly under pytest
    (verified locally: no ASGI/DB/network side effects fire at import
    time beyond optional dotenv/keychain loading, both of which no-op
    gracefully when absent).
    """
    from mcp_server.server import WRITABLE_FIELDS

    return WRITABLE_FIELDS


def _writable_fields_via_ast() -> dict[str, tuple]:
    """Fallback: parse the WRITABLE_FIELDS dict literal out of the source
    file with `ast`, without importing/executing the module at all.

    Only used if the direct import raises at collection/call time.
    """
    tree = ast.parse(MCP_SERVER_PATH.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "WRITABLE_FIELDS":
            value = node.value
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "WRITABLE_FIELDS" for t in node.targets
        ):
            value = node.value
        else:
            continue
        return ast.literal_eval(value)
    raise AssertionError(f"WRITABLE_FIELDS assignment not found in {MCP_SERVER_PATH}")


def _load_writable_fields() -> tuple[dict[str, tuple], str]:
    """Return (WRITABLE_FIELDS, path_taken) — tries the real import first,
    falls back to AST parsing only if the import actually fails.
    """
    try:
        return _writable_fields_via_import(), "import"
    except Exception:
        return _writable_fields_via_ast(), "ast-fallback"


WRITABLE_FIELDS, WRITABLE_FIELDS_SOURCE = _load_writable_fields()


def test_writable_fields_load_path_is_import():
    """Documents which path was taken. Direct import is expected to work
    (see docstring above); if this ever flips to 'ast-fallback', the note
    in this test's failure output is a real signal something changed in
    mcp_server/server.py's import-time behavior — investigate, don't just
    accept the fallback silently.
    """
    assert WRITABLE_FIELDS_SOURCE == "import", (
        "WRITABLE_FIELDS import failed and the AST fallback was used instead — "
        "mcp_server.server likely gained an import-time side effect that breaks "
        "under pytest. Investigate before trusting this drift test."
    )


class TestConfigLoadsCleanly:
    def test_real_yaml_loads_without_error(self):
        rules = load_rules(CONFIG_PATH)
        assert rules.raw["meta"]["version"] == 1

    def test_real_yaml_does_not_raise_style_rules_error(self):
        try:
            load_rules(CONFIG_PATH)
        except StyleRulesError as exc:  # pragma: no cover - failure path
            pytest.fail(f"config/house_style.yaml failed to load: {exc}")


class TestCharLimitDrift:
    """The core drift assertion: YAML limits.fields[*].max == WRITABLE_FIELDS char_limit."""

    @pytest.mark.parametrize("yaml_key,writable_key", sorted(YAML_TO_WRITABLE_FIELD.items()))
    def test_field_max_matches_writable_fields(self, yaml_key, writable_key):
        rules = load_rules(CONFIG_PATH)
        limits = rules.limits_for()

        assert yaml_key in limits, f"limits.fields.{yaml_key} missing from house_style.yaml"
        yaml_max = limits[yaml_key].get("max")
        assert yaml_max is not None, f"limits.fields.{yaml_key} has no 'max' key"

        assert writable_key in WRITABLE_FIELDS, f"{writable_key} missing from WRITABLE_FIELDS"
        _, _, writable_char_limit = WRITABLE_FIELDS[writable_key]

        assert yaml_max == writable_char_limit, (
            f"Drift: house_style.yaml limits.fields.{yaml_key}.max={yaml_max} but "
            f"WRITABLE_FIELDS[{writable_key!r}] char_limit={writable_char_limit} "
            f"(mcp_server/server.py:100-109 is the ground truth — fix the YAML)"
        )

    def test_no_extra_char_limited_fields_silently_diverge(self):
        """Every WRITABLE_FIELDS entry that actually carries a char_limit
        should have a corresponding limits.fields entry in the YAML with a
        matching max — this guards against a new char-limited field being
        added to WRITABLE_FIELDS without the YAML ever being updated.
        """
        rules = load_rules(CONFIG_PATH)
        limits = rules.limits_for()
        writable_key_to_yaml_key = {v: k for k, v in YAML_TO_WRITABLE_FIELD.items()}

        for writable_key, (_, _, char_limit) in WRITABLE_FIELDS.items():
            if char_limit is None:
                continue
            yaml_key = writable_key_to_yaml_key.get(writable_key)
            assert yaml_key is not None, (
                f"WRITABLE_FIELDS[{writable_key!r}] has char_limit={char_limit} but "
                f"there is no YAML_TO_WRITABLE_FIELD mapping for it in this drift test "
                f"(and possibly no limits.fields entry in house_style.yaml either)"
            )
            assert limits[yaml_key]["max"] == char_limit


# Field label -> regex matching that label followed (same line, within a
# short window) by the char-limit number the block is quoting -- e.g. "Title
# ≤80 characters" or "title 80,". Deliberately pragmatic (per task 1c's
# brief): confined to the same line (`[^\d\n]` excludes newlines, so a label
# and number separated by a line break simply produce no match rather than a
# false one) and to title/short/long, the three fields with a single numeric
# max. `keywords` is a min/max pair and is out of scope.
_PROMPT_BLOCK_FIELD_LIMIT_RE = {
    "title": re.compile(r"\btitle\b[^\d\n]{0,15}?(\d{2,4})", re.IGNORECASE),
    "short_description": re.compile(r"\bshort description\b[^\d\n]{0,15}?(\d{2,4})", re.IGNORECASE),
    "long_description": re.compile(r"\blong description\b[^\d\n]{0,15}?(\d{2,4})", re.IGNORECASE),
}


class TestPromptBlockNumbersMatchCharLimits:
    """The prompt_blocks section's human-authored rule prose quotes hard
    character limits inline (e.g. "Title ≤80 characters") for the LLM
    reading the rendered prompt. Those numbers are NOT read programmatically
    -- limits.fields is the machine source of truth for the deterministic
    engine -- so nothing stops an editor updating one and forgetting the
    other. This closes that gap for title/short/long, the corrected-by-task-1c
    values (80/90/350).
    """

    @pytest.mark.parametrize("field", ["title", "short_description", "long_description"])
    def test_prompt_block_numbers_match_limits_fields(self, field):
        rules = load_rules(CONFIG_PATH)
        expected_max = rules.limits_for()[field]["max"]

        prompt_blocks = rules.raw.get("prompt_blocks", {}) or {}
        pattern = _PROMPT_BLOCK_FIELD_LIMIT_RE[field]

        found = []  # (block_key, profile_name, number)
        for block_key, profiles in prompt_blocks.items():
            if not isinstance(profiles, dict):
                continue
            for profile_name, text in profiles.items():
                if not isinstance(text, str):
                    continue
                for match in pattern.finditer(text):
                    found.append((block_key, profile_name, int(match.group(1))))

        assert found, (
            f"No prompt_blocks text mentions a {field} character limit -- expected at "
            f"least one (e.g. in seo.copy_rules or shared.char_budgets)"
        )
        for block_key, profile_name, number in found:
            assert number == expected_max, (
                f"Drift: prompt_blocks.{block_key!r}.{profile_name!r} quotes the {field} "
                f"limit as {number} but limits.fields.{field}.max={expected_max} -- "
                f"fix the prompt_blocks prose to match"
            )

    @pytest.mark.parametrize("field", ["title", "short_description", "long_description"])
    def test_validator_checklist_block_is_scanned_for_field(self, field):
        """Explicit coverage guard for task 2d's validator.checklist block --
        proves the generic scan above isn't passing vacuously off of
        seo.copy_rules/shared.char_budgets alone. If a future edit strips the
        numbers out of validator.checklist.full, this fails loudly instead of
        the generic parametrized test above just quietly finding fewer hits.
        """
        rules = load_rules(CONFIG_PATH)
        prompt_blocks = rules.raw.get("prompt_blocks", {}) or {}
        validator_block = prompt_blocks.get("validator.checklist")
        assert validator_block is not None, "prompt_blocks.'validator.checklist' is missing from house_style.yaml"

        full_text = validator_block.get("full", "")
        pattern = _PROMPT_BLOCK_FIELD_LIMIT_RE[field]
        matches = [int(m.group(1)) for m in pattern.finditer(full_text)]

        assert matches, f"validator.checklist.full does not quote a {field} character limit"
        expected_max = rules.limits_for()[field]["max"]
        for number in matches:
            assert number == expected_max

    @pytest.mark.parametrize("field", ["title", "short_description", "long_description"])
    def test_analyst_draft_guidance_block_is_scanned_for_field(self, field):
        """Explicit coverage guard for task 5's analyst.draft_guidance block --
        mirrors test_validator_checklist_block_is_scanned_for_field so a
        future edit that strips the numbers out of
        analyst.draft_guidance.full fails loudly instead of the generic
        parametrized test above just quietly finding fewer hits.
        """
        rules = load_rules(CONFIG_PATH)
        prompt_blocks = rules.raw.get("prompt_blocks", {}) or {}
        analyst_block = prompt_blocks.get("analyst.draft_guidance")
        assert analyst_block is not None, "prompt_blocks.'analyst.draft_guidance' is missing from house_style.yaml"

        full_text = analyst_block.get("full", "")
        pattern = _PROMPT_BLOCK_FIELD_LIMIT_RE[field]
        matches = [int(m.group(1)) for m in pattern.finditer(full_text)]

        assert matches, f"analyst.draft_guidance.full does not quote a {field} character limit"
        expected_max = rules.limits_for()[field]["max"]
        for number in matches:
            assert number == expected_max

    def test_analyst_draft_guidance_keyword_count_matches_limits(self):
        """The 15-20 keyword range quoted in analyst.draft_guidance.full must
        match limits.fields.keywords.count -- the same DECIDED-value guard
        as test_keyword_count_is_15_to_20_not_15_to_25, applied to this
        block's prose specifically.
        """
        rules = load_rules(CONFIG_PATH)
        prompt_blocks = rules.raw.get("prompt_blocks", {}) or {}
        full_text = prompt_blocks.get("analyst.draft_guidance", {}).get("full", "")

        expected = rules.limits_for()["keywords"]["count"]
        expected_range = f"{expected['min']}-{expected['max']}"

        assert f"{expected_range} total keywords" in full_text
        assert "15-25" not in full_text


class TestSmokeLevelCompleteness:
    """Non-empty smoke checks — the loader's accessors must return real
    rule data, not an empty/placeholder file. These only READ accessor
    results; the returned lists/dicts alias the cached StyleRules object's
    `raw` data, so nothing here mutates them.
    """

    def test_chapter_max_returns_values_across_durations(self):
        rules = load_rules(CONFIG_PATH)
        assert rules.chapter_max(3) > 0
        assert rules.chapter_max(10) > 0
        assert rules.chapter_max(20) > 0
        assert rules.chapter_max(45) > 0
        assert rules.chapter_max(90) > 0

    def test_forbidden_is_non_empty(self):
        rules = load_rules(CONFIG_PATH)
        forbidden = rules.forbidden()
        assert len(forbidden) > 0
        for entry in forbidden:
            assert entry.get("match")
            assert entry.get("category")
            assert entry.get("tier") == "flag"
            assert entry.get("severity") in ("error", "warning")

    def test_canonical_seed_is_non_empty(self):
        rules = load_rules(CONFIG_PATH)
        seed = rules.canonical_seed()
        assert len(seed) > 0
        assert "wisconsin" in seed
        assert seed["wisconsin"] == "Wisconsin"

    def test_enforce_substitutions_are_non_empty(self):
        rules = load_rules(CONFIG_PATH)
        enforce_subs = rules.substitutions(tier="enforce")
        assert len(enforce_subs) > 0
        for sub in enforce_subs:
            assert "find" in sub
            assert "replace" in sub

    def test_flag_substitutions_are_non_empty(self):
        rules = load_rules(CONFIG_PATH)
        flag_subs = rules.substitutions(tier="flag")
        assert len(flag_subs) > 0

    def test_keyword_count_is_15_to_20_not_15_to_25(self):
        """DECIDED value: keywords are 15-20 (analyst.md's 15-25 is superseded)."""
        rules = load_rules(CONFIG_PATH)
        limits = rules.limits_for()
        assert limits["keywords"]["count"] == {"min": 15, "max": 20}

    def test_short_content_type_override_is_5_to_10(self):
        rules = load_rules(CONFIG_PATH)
        limits = rules.limits_for(content_type="short")
        assert limits["keywords"]["count"] == {"min": 5, "max": 10}

    def test_program_rules_present_for_documented_programs(self):
        rules = load_rules(CONFIG_PATH)
        for program in ("Here & Now", "University Place", "Wisconsin Life", "The Look Back", "Digital Shorts"):
            assert rules.program_rules(program) != {}, f"programs.{program!r} missing or empty"

    def test_here_and_now_short_description_is_role_forward(self):
        """DECIDED value: role-forward '{role} {verb} {subject}.' — NOT the
        older EDITOR_AGENT_INSTRUCTIONS.md name-forward '[name] on [subject]'.
        """
        rules = load_rules(CONFIG_PATH)
        here_and_now = rules.program_rules("Here & Now")
        assert here_and_now["short_description_formula"] == "{role} {verb} {subject}."

    def test_analyst_required_sections_loadable_and_non_empty(self):
        """task 5: phases.analyst.required_sections backs
        post_stage's analyst.section_missing check -- must be loadable from
        the real config and match the headings actually named in
        prompts/analyst.md's output schema.
        """
        rules = load_rules(CONFIG_PATH)
        analyst_cfg = rules.raw.get("phases", {}).get("analyst", {})
        required_sections = analyst_cfg.get("required_sections")

        assert required_sections, "phases.analyst.required_sections is missing or empty"
        assert required_sections == [
            "Summary",
            "Key Themes",
            "Speakers & Roles",
            "Structural Breakdown",
            "SEO Keywords",
        ]


def _real_casing_variants() -> dict[str, str]:
    rules = load_rules(CONFIG_PATH)
    casing = rules.raw.get("casing", {}) or {}
    return dict(casing.get("casing_variants", {}) or {})


class TestCasingVariantsIdempotenceAgainstRealConfig:
    """Real-config guard against the "atty gen" -> "Atty. Gen." idempotence
    regression (see tests/test_style_casing_entities.py's
    TestMultiWordPunctuatedVariantIdempotence for the synthetic repro).

    This is the test that would have caught the original bug: it loads the
    REAL config/house_style.yaml casing.casing_variants and asserts every
    entry's fixed-point + single-pass-convergence properties hold, so a
    future editor adding a new internally-punctuated multi-word variant
    (another "atty gen"-shaped entry) fails CI instead of shipping a casing
    regression to production output.
    """

    @pytest.mark.parametrize("key,value", sorted(_real_casing_variants().items()))
    def test_casing_variant_value_is_fixed_point(self, key, value):
        """Already-canonical text (the casing_variant's own value) must be
        unchanged by a to_down_style pass -- it's already in restored form.
        """
        rules = load_rules(CONFIG_PATH)
        canonical = build_canonical(rules)
        assert to_down_style(value, canonical) == value

    @pytest.mark.parametrize("key,value", sorted(_real_casing_variants().items()))
    def test_casing_variant_bare_and_punctuated_converge(self, key, value):
        """The bare (lowercase, unpunctuated) key and the fully-punctuated
        canonical value must down-style to byte-identical output -- the
        single-pass convergence guarantee the down-style engine exists for.
        """
        rules = load_rules(CONFIG_PATH)
        canonical = build_canonical(rules)
        assert to_down_style(f"{key} follows", canonical) == to_down_style(f"{value} follows", canonical)
