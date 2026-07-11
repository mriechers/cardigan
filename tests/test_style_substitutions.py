"""Tests for api.services.style_engine.substitutions -- the formatter
enforce-tier text primitives (task 3a).

Pure, synthetic-rules only -- never depends on config/house_style.yaml.
Mirrors the fixture/helper style of tests/test_style_casing_entities.py.

These primitives did NOT exist before task 3a (contrary to the task brief's
assumption that a "Stage 0.5" had already landed them) -- see the task-3a
report for the full explanation. Built here from the brief's own detailed
spec: enforce-tier substitutions are word-boundary lexical regex find/replace
pairs (as authored in config/house_style.yaml's phases.formatter.substitutions
"enforce" tier), guarded so they never touch fenced code blocks or URLs, and
a same-case-fold match is additionally guarded against firing sentence-
initially (documented behavior for the "Liberals"/"Conservatives" house-style
pair, generically derived -- never hardcoded to those literal words).
normalize_speaker_turns is a separate, whitespace-only primitive (trailing
spaces on speaker-label lines, blank-line count between speaker turns).
"""

from __future__ import annotations

from api.services.style_engine.substitutions import (
    apply_substitutions,
    apply_substitutions_with_fixes,
    normalize_speaker_turns,
)

# ---------------------------------------------------------------------------
# apply_substitutions -- enforce-tier find/replace pairs
# ---------------------------------------------------------------------------

OKAY_SUB = {"find": r"\b[Oo]kay\b", "replace": "OK", "tier": "enforce"}
PARTIZAN_SUB = {"find": r"\b[Pp]artizan\b", "replace": "partisan", "tier": "enforce"}
SENATOR_SUB = {"find": r"\bSenator\b", "replace": "Sen.", "tier": "enforce"}
ATTY_GEN_SUB = {"find": r"\bAttorney General\b", "replace": "Atty. Gen.", "tier": "enforce"}
LIBERALS_SUB = {
    "find": r"\bLiberals\b",
    "replace": "liberals",
    "tier": "enforce",
    "note": "engine applies non-sentence-initial guard before matching",
}
CONSERVATIVE_SUB = {
    "find": r"\bConservative\b",
    "replace": "conservative",
    "tier": "enforce",
    "note": "adjective form; engine applies non-sentence-initial guard before matching",
}
DE_ITALICIZE_SUB = {
    "id": "de_italicize_program_names",
    "find": r"\*(Here & Now|Wisconsin Life)\*",
    "replace": r"\1",
    "tier": "enforce",
}
# A flag-tier entry (detect-only) must never be treated as a rewrite pair.
OXFORD_COMMA_DETECT = {"id": "oxford_comma", "detect": r",\s+and\b", "tier": "flag", "severity": "warning"}


class TestApplySubstitutionsBasic:
    def test_lowercase_and_titlecase_both_normalize(self):
        assert apply_substitutions("that's okay with me", [OKAY_SUB]) == "that's OK with me"
        assert apply_substitutions("Okay, let's begin", [OKAY_SUB]) == "OK, let's begin"

    def test_already_correct_form_untouched(self):
        assert apply_substitutions("that's OK with me", [OKAY_SUB]) == "that's OK with me"

    def test_misspelling_fix(self):
        assert apply_substitutions("a partizan vote", [PARTIZAN_SUB]) == "a partisan vote"

    def test_honorific_abbreviation(self):
        assert apply_substitutions("Senator Smith spoke", [SENATOR_SUB]) == "Sen. Smith spoke"

    def test_multiword_pair(self):
        assert (
            apply_substitutions("the Attorney General said", [ATTY_GEN_SUB]) == "the Atty. Gen. said"
        )

    def test_backreference_replace_strips_italics(self):
        assert (
            apply_substitutions("watch *Here & Now* tonight", [DE_ITALICIZE_SUB])
            == "watch Here & Now tonight"
        )

    def test_multiple_pairs_applied_in_sequence(self):
        text = "Senator Smith said that's okay"
        result = apply_substitutions(text, [OKAY_SUB, SENATOR_SUB])
        assert result == "Sen. Smith said that's OK"

    def test_empty_text_returns_unchanged(self):
        assert apply_substitutions("", [OKAY_SUB]) == ""

    def test_empty_substitutions_returns_unchanged(self):
        text = "that's okay"
        assert apply_substitutions(text, []) == text

    def test_flag_tier_detect_entry_never_rewrites(self):
        text = "red, and blue, and green"
        assert apply_substitutions(text, [OXFORD_COMMA_DETECT]) == text

    def test_unknown_extra_keys_on_entry_tolerated(self):
        sub = dict(OKAY_SUB)
        sub["some_future_field"] = {"nested": True}
        assert apply_substitutions("okay then", [sub]) == "OK then"

    def test_malformed_entry_missing_find_or_replace_skipped_not_raised(self):
        assert apply_substitutions("okay then", [{"tier": "enforce"}]) == "okay then"
        assert apply_substitutions("okay then", [{"find": r"\bokay\b"}]) == "okay then"


class TestApplySubstitutionsConvergence:
    def test_okay_variants_converge_to_identical_text(self):
        variant_lower = apply_substitutions("that's okay", [OKAY_SUB])
        variant_title = apply_substitutions("that's Okay", [OKAY_SUB])
        variant_already = apply_substitutions("that's OK", [OKAY_SUB])
        assert variant_lower == variant_title == variant_already == "that's OK"


class TestApplySubstitutionsGuards:
    def test_fenced_code_block_untouched(self):
        text = "Say okay.\n\n```\nokay = True\n```\n"
        result = apply_substitutions(text, [OKAY_SUB])
        assert result == "Say OK.\n\n```\nokay = True\n```\n"

    def test_url_untouched(self):
        text = "Visit https://example.com/okay-page for okay details."
        result = apply_substitutions(text, [OKAY_SUB])
        assert result == "Visit https://example.com/okay-page for OK details."

    def test_sentence_initial_downcasing_sub_skipped_at_start_of_text(self):
        text = "Liberals gathered downtown today."
        assert apply_substitutions(text, [LIBERALS_SUB]) == text

    def test_sentence_initial_downcasing_sub_skipped_after_terminal_punctuation(self):
        text = "The vote passed. Liberals celebrated the outcome."
        assert apply_substitutions(text, [LIBERALS_SUB]) == text

    def test_sentence_initial_downcasing_sub_skipped_at_start_of_line(self):
        text = "Some context.\nLiberals reacted quickly."
        assert apply_substitutions(text, [LIBERALS_SUB]) == text

    def test_mid_sentence_downcasing_sub_applied(self):
        text = "Reporters said the Liberals gathered downtown."
        assert apply_substitutions(text, [LIBERALS_SUB]) == "Reporters said the liberals gathered downtown."

    def test_mid_sentence_adjective_form_applied(self):
        text = "It was a Conservative viewpoint on the issue."
        assert apply_substitutions(text, [CONSERVATIVE_SUB]) == "It was a conservative viewpoint on the issue."

    def test_non_downcasing_sub_applies_even_sentence_initially(self):
        # Senator -> Sen. is not a bare case-fold, so no sentence-initial
        # guard applies -- AP style permits an abbreviation to open a
        # sentence.
        text = "Senator Smith spoke first."
        assert apply_substitutions(text, [SENATOR_SUB]) == "Sen. Smith spoke first."

    def test_possessive_apostrophe_not_treated_as_sentence_terminal(self):
        # Bare apostrophes (possessives like "workers'") should NOT count as
        # sentence-terminal. Only apostrophes/quotes that immediately follow
        # real terminal punctuation (.!?) should count.
        text = "Reporters said the workers' Liberals rally drew a crowd."
        result = apply_substitutions(text, [LIBERALS_SUB])
        assert result == "Reporters said the workers' liberals rally drew a crowd."

    def test_quote_after_terminal_punctuation_is_sentence_terminal(self):
        # A closing quote after terminal punctuation (.!?) IS sentence-terminal,
        # so the word following should not be downcased.
        text = 'He said "Stop!" Liberals cheered.'
        result = apply_substitutions(text, [LIBERALS_SUB])
        assert result == 'He said "Stop!" Liberals cheered.'


class TestApplySubstitutionsIdempotence:
    def test_double_application_is_a_no_op(self):
        subs = [OKAY_SUB, SENATOR_SUB, LIBERALS_SUB, DE_ITALICIZE_SUB]
        text = "Senator Smith said the Liberals were okay with *Here & Now* coverage."
        once = apply_substitutions(text, subs)
        twice = apply_substitutions(once, subs)
        assert once == twice


# ---------------------------------------------------------------------------
# apply_substitutions_with_fixes -- wrapper reporting AppliedFix entries
# ---------------------------------------------------------------------------


class TestApplySubstitutionsWithFixes:
    def test_returns_text_and_fixes(self):
        text, fixes = apply_substitutions_with_fixes("that's okay", [OKAY_SUB])
        assert text == "that's OK"
        assert len(fixes) == 1
        fix = fixes[0]
        assert fix.before == "okay"
        assert fix.after == "OK"
        assert fix.count == 1

    def test_no_match_yields_no_fixes(self):
        text, fixes = apply_substitutions_with_fixes("nothing to change here", [OKAY_SUB])
        assert text == "nothing to change here"
        assert fixes == []

    def test_multiple_matches_of_same_pair_counted_together(self):
        text, fixes = apply_substitutions_with_fixes("okay, okay, that's Okay", [OKAY_SUB])
        assert text == "OK, OK, that's OK"
        assert len(fixes) == 1
        assert fixes[0].count == 3

    def test_multiple_pairs_produce_separate_fix_entries(self):
        text, fixes = apply_substitutions_with_fixes(
            "Senator Smith said okay", [OKAY_SUB, SENATOR_SUB]
        )
        assert text == "Sen. Smith said OK"
        assert len(fixes) == 2
        rule_ids = {f.rule_id for f in fixes}
        assert len(rule_ids) == 2

    def test_guarded_match_not_counted_as_a_fix(self):
        text, fixes = apply_substitutions_with_fixes("Liberals gathered today.", [LIBERALS_SUB])
        assert text == "Liberals gathered today."
        assert fixes == []

    def test_flag_tier_entry_produces_no_fixes(self):
        text, fixes = apply_substitutions_with_fixes("red, and blue", [OXFORD_COMMA_DETECT])
        assert text == "red, and blue"
        assert fixes == []

    def test_id_field_used_in_rule_id_when_present(self):
        _, fixes = apply_substitutions_with_fixes("watch *Here & Now* tonight", [DE_ITALICIZE_SUB])
        assert len(fixes) == 1
        assert "de_italicize_program_names" in fixes[0].rule_id


# ---------------------------------------------------------------------------
# normalize_speaker_turns -- whitespace-only
# ---------------------------------------------------------------------------

SPEC = {
    "pattern": r"^\*\*[A-Z][\w.'-]+(?: [A-Z][\w.'-]+)+:\*\*",
    "trailing_spaces": 2,
    "blank_lines_between_turns": 1,
    "no_honorifics": True,
}


class TestNormalizeSpeakerTurnsTrailingSpaces:
    def test_missing_trailing_spaces_added(self):
        text = "**Nick Hoffman:**\nThanks for joining us today.\n"
        result = normalize_speaker_turns(text, SPEC)
        assert result.startswith("**Nick Hoffman:**  \n")

    def test_excess_trailing_spaces_trimmed_to_spec(self):
        text = "**Nick Hoffman:**     \nThanks for joining us today.\n"
        result = normalize_speaker_turns(text, SPEC)
        assert result.startswith("**Nick Hoffman:**  \n")

    def test_already_correct_trailing_spaces_untouched(self):
        text = "**Nick Hoffman:**  \nThanks for joining us today.\n"
        assert normalize_speaker_turns(text, SPEC) == text

    def test_dialogue_line_trailing_whitespace_not_touched(self):
        # Only label lines get trailing-space normalization -- dialogue
        # content is never touched by this whitespace-only primitive.
        text = "**Nick Hoffman:**  \nThanks for joining us today.   \n"
        result = normalize_speaker_turns(text, SPEC)
        assert "Thanks for joining us today.   \n" in result


class TestNormalizeSpeakerTurnsBlankLines:
    def test_missing_blank_line_between_turns_inserted(self):
        text = "**Nick Hoffman:**  \nStatement one.\n**Angela Fitzgerald:**  \nStatement two.\n"
        result = normalize_speaker_turns(text, SPEC)
        assert "Statement one.\n\n**Angela Fitzgerald:**" in result

    def test_extra_blank_lines_between_turns_collapsed(self):
        text = "**Nick Hoffman:**  \nStatement one.\n\n\n\n**Angela Fitzgerald:**  \nStatement two.\n"
        result = normalize_speaker_turns(text, SPEC)
        assert "Statement one.\n\n**Angela Fitzgerald:**" in result
        assert "Statement one.\n\n\n**Angela Fitzgerald:**" not in result

    def test_already_correct_blank_line_count_untouched(self):
        text = "**Nick Hoffman:**  \nStatement one.\n\n**Angela Fitzgerald:**  \nStatement two.\n"
        assert normalize_speaker_turns(text, SPEC) == text

    def test_first_speaker_label_preceding_whitespace_not_touched(self):
        # Only gaps BETWEEN two speaker turns are normalized -- the
        # document-header spacing before the first label is out of scope
        # for a per-turn whitespace primitive.
        text = "# Formatted Transcript\n---\n\n\n\n**Nick Hoffman:**  \nStatement one.\n"
        result = normalize_speaker_turns(text, SPEC)
        assert result.startswith("# Formatted Transcript\n---\n\n\n\n**Nick Hoffman:**")


class TestNormalizeSpeakerTurnsBodyScoping:
    def test_two_word_bold_header_field_not_treated_as_speaker_label(self):
        # The real formatter template's header has "**Date Processed:**" --
        # two capitalized words, same bold-colon shape as a first+last-name
        # speaker label. Body-scoping (between the first and last "---")
        # must keep this from being "fixed" into a fake speaker turn.
        text = (
            "# Formatted Transcript\n"
            "**Project:** 2WLI1234HD\n"
            "**Date Processed:** 2026-07-10\n"
            "---\n\n"
            "**Nick Hoffman:**\n"
            "Statement one.\n\n"
            "**Angela Fitzgerald:**\n"
            "Statement two.\n"
            "---\n\n"
            "**Status:** ready_for_editing\n"
        )
        result = normalize_speaker_turns(text, SPEC)
        assert "**Date Processed:**  \n" not in result
        assert "**Date Processed:** 2026-07-10\n" in result
        assert "**Status:**  \n" not in result
        assert "**Status:** ready_for_editing\n" in result
        # The real speaker turns inside the body are still normalized.
        assert "**Nick Hoffman:**  \nStatement one.\n\n**Angela Fitzgerald:**  \n" in result

    def test_header_to_first_label_gap_untouched_with_two_hr_document(self):
        text = (
            "# Formatted Transcript\n---\n\n\n\n**Nick Hoffman:**\nStatement one.\n\n---\n\n**Status:** x\n"
        )
        result = normalize_speaker_turns(text, SPEC)
        assert result.startswith("# Formatted Transcript\n---\n\n\n\n**Nick Hoffman:**")


class TestNormalizeSpeakerTurnsIdempotence:
    def test_double_normalization_is_a_no_op(self):
        text = "**Nick Hoffman:**\nStatement one.\n\n\n**Angela Fitzgerald:**   \nStatement two.\n"
        once = normalize_speaker_turns(text, SPEC)
        twice = normalize_speaker_turns(once, SPEC)
        assert once == twice


class TestNormalizeSpeakerTurnsGraceful:
    def test_empty_text_returns_unchanged(self):
        assert normalize_speaker_turns("", SPEC) == ""

    def test_missing_pattern_key_is_a_no_op(self):
        text = "**Nick Hoffman:**\nStatement one.\n"
        assert normalize_speaker_turns(text, {"trailing_spaces": 2}) == text

    def test_missing_trailing_spaces_key_skips_that_dimension(self):
        text = "**Nick Hoffman:**\nStatement one.\n\n\n**Angela Fitzgerald:**\nStatement two.\n"
        spec = {"pattern": SPEC["pattern"], "blank_lines_between_turns": 1}
        result = normalize_speaker_turns(text, spec)
        # Trailing spaces untouched (no key for that dimension)...
        assert result.startswith("**Nick Hoffman:**\n")
        # ...but blank-line count between turns is still normalized.
        assert "Statement one.\n\n**Angela Fitzgerald:**" in result

    def test_missing_blank_lines_key_skips_that_dimension(self):
        text = "**Nick Hoffman:**\nStatement one.\n\n\n**Angela Fitzgerald:**\nStatement two.\n"
        spec = {"pattern": SPEC["pattern"], "trailing_spaces": 2}
        result = normalize_speaker_turns(text, spec)
        assert result.startswith("**Nick Hoffman:**  \n")
        # Blank-line run between turns left as-authored (3 newlines / 2 blanks).
        assert "Statement one.\n\n\n**Angela Fitzgerald:**" in result

    def test_unknown_extra_spec_keys_tolerated(self):
        text = "**Nick Hoffman:**\nStatement one.\n"
        spec = dict(SPEC)
        spec["some_future_field"] = "whatever"
        result = normalize_speaker_turns(text, spec)
        assert result.startswith("**Nick Hoffman:**  \n")


class TestNormalizeSpeakerTurnsSpecialCharacters:
    def test_speaker_label_with_apostrophe_preserved(self):
        # Names with apostrophes (O'Brien) should be preserved exactly,
        # whitespace normalization applied without touching the name itself.
        text = "**Sean O'Brien:**\nStatement here.\n"
        result = normalize_speaker_turns(text, SPEC)
        assert result.startswith("**Sean O'Brien:**  \n")
        assert "**Sean O'Brien:**" in result

    def test_speaker_label_with_hyphen_preserved(self):
        # Names with hyphens (Jean-Paul) should be preserved exactly,
        # whitespace normalization applied without touching the name itself.
        text = "**Jean-Paul Smith:**\nStatement here.\n"
        result = normalize_speaker_turns(text, SPEC)
        assert result.startswith("**Jean-Paul Smith:**  \n")
        assert "**Jean-Paul Smith:**" in result
