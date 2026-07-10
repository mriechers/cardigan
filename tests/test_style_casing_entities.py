"""Tests for the style_engine casing and entity-extraction primitives.

Covers api.services.style_engine.casing (build_canonical / to_down_style,
ported from scripts/poc_house_style_normalizer.py) and
api.services.style_engine.entities (extract_proper_nouns, same PoC lineage).
All rule data and sample text is synthetic -- built inline as StyleRules(raw=...)
or plain strings -- and does not depend on OUTPUT/ artifacts or the real
config/house_style.yaml. Mirrors the fixture/helper style of
tests/test_style_rules.py.

The convergence test in TestConvergence is the load-bearing test for this
task: it proves that arbitrarily-cased LLM output (over-capitalized vs.
over-lowercased) normalizes to a byte-identical result given the same
canonical map, which is the entire point of the down-style engine.
"""

from __future__ import annotations

from api.services.style_engine.casing import build_canonical, to_down_style
from api.services.style_engine.entities import extract_proper_nouns
from api.services.style_engine.rules import StyleRules

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _rules(**overrides) -> StyleRules:
    """A minimal StyleRules with a casing section resembling house_style.yaml."""
    raw = {
        "meta": {"version": 1},
        "casing": {
            "style": "down",
            "proper_nouns": ["Wisconsin", "Governor", "Madison"],
            "acronyms": ["PBS", "SCOTUS"],
            "casing_variants": {"gov": "Gov."},
            "surname_stoplist": ["van", "der", "de", "la", "the"],
        },
    }
    raw.update(overrides)
    return StyleRules(raw=raw)


# ---------------------------------------------------------------------------
# build_canonical
# ---------------------------------------------------------------------------


class TestBuildCanonical:
    def test_merges_seed(self):
        canonical = build_canonical(_rules())
        assert canonical["wisconsin"] == "Wisconsin"
        assert canonical["pbs"] == "PBS"
        assert canonical["gov"] == "Gov."

    def test_extra_nouns_added(self):
        canonical = build_canonical(_rules(), extra_nouns=["Tony Evers"])
        assert canonical["tony evers"] == "Tony Evers"

    def test_extra_nouns_win_on_collision(self):
        # Seed maps "wisconsin" -> "Wisconsin"; an extra_noun with the same
        # lowercase key must override it (per-job data wins over the seed).
        canonical = build_canonical(_rules(), extra_nouns=["WISCONSIN"])
        assert canonical["wisconsin"] == "WISCONSIN"

    def test_no_extra_nouns_is_default(self):
        canonical = build_canonical(_rules())
        assert "tony evers" not in canonical

    def test_does_not_mutate_rules_seed(self):
        rules = _rules()
        seed_before = rules.canonical_seed()
        build_canonical(rules, extra_nouns=["Tony Evers"])
        seed_after = rules.canonical_seed()
        assert seed_before == seed_after
        assert "tony evers" not in seed_after


# ---------------------------------------------------------------------------
# to_down_style -- basics
# ---------------------------------------------------------------------------


class TestToDownStyleBasics:
    def test_lowercases_non_canonical_words_and_caps_first_letter(self):
        canonical = build_canonical(_rules())
        result = to_down_style("THE BUDGET BILL PASSES", canonical)
        assert result == "The budget bill passes"

    def test_restores_seed_proper_noun(self):
        canonical = build_canonical(_rules())
        result = to_down_style("the wisconsin budget bill", canonical)
        assert result == "The Wisconsin budget bill"

    def test_first_char_capitalization_only_when_lower(self):
        canonical = build_canonical(_rules())
        # "PBS" is already uppercase after restoration; capitalization step
        # must not double up or otherwise corrupt it.
        result = to_down_style("PBS airs the special tonight", canonical)
        assert result == "PBS airs the special tonight"


# ---------------------------------------------------------------------------
# to_down_style -- longest-term-first
# ---------------------------------------------------------------------------


class TestLongestTermFirst:
    def test_multi_word_term_wins_over_contained_shorter_term(self):
        canonical = build_canonical(_rules(), extra_nouns=["Wisconsin Supreme Court"])
        result = to_down_style("wisconsin supreme court ruled today", canonical)
        assert result == "Wisconsin Supreme Court ruled today"

    def test_shorter_standalone_term_still_restored_elsewhere(self):
        canonical = build_canonical(_rules(), extra_nouns=["Wisconsin Supreme Court"])
        result = to_down_style(
            "wisconsin supreme court ruled, and wisconsin lawmakers reacted", canonical
        )
        assert result == "Wisconsin Supreme Court ruled, and Wisconsin lawmakers reacted"


# ---------------------------------------------------------------------------
# to_down_style -- word-boundary safety
# ---------------------------------------------------------------------------


class TestWordBoundarySafety:
    def test_does_not_touch_substring_inside_longer_word(self):
        canonical = build_canonical(_rules(), extra_nouns=["Court"])
        result = to_down_style("meet me in the courtyard near the court", canonical)
        assert result == "Meet me in the courtyard near the Court"
        assert "Courtyard" not in result

    def test_multi_word_term_matches_only_single_space_boundary(self):
        canonical = build_canonical(_rules(), extra_nouns=["Wisconsin Supreme Court"])
        # Different words entirely -- must not partially match.
        result = to_down_style("the wisconsin court system", canonical)
        assert result == "The Wisconsin court system"


# ---------------------------------------------------------------------------
# to_down_style -- idempotence
# ---------------------------------------------------------------------------


class TestIdempotence:
    def test_second_application_is_a_no_op(self):
        canonical = build_canonical(_rules(), extra_nouns=["Tony Evers"])
        text = "TONY EVERS AND THE WISCONSIN BUDGET BILL"
        once = to_down_style(text, canonical)
        twice = to_down_style(once, canonical)
        assert once == twice

    def test_idempotent_on_already_correct_text(self):
        canonical = build_canonical(_rules())
        text = "The Governor signs a Wisconsin budget bill"
        assert to_down_style(text, canonical) == to_down_style(
            to_down_style(text, canonical), canonical
        )


# ---------------------------------------------------------------------------
# to_down_style -- acronym restoration
# ---------------------------------------------------------------------------


class TestAcronymRestoration:
    def test_pbs_and_scotus_restored_uppercase(self):
        canonical = build_canonical(_rules())
        result = to_down_style("pbs covers the scotus ruling live", canonical)
        assert result == "PBS covers the SCOTUS ruling live"


# ---------------------------------------------------------------------------
# to_down_style -- casing_variants
# ---------------------------------------------------------------------------


class TestCasingVariants:
    def test_gov_becomes_gov_dot(self):
        canonical = build_canonical(_rules(), extra_nouns=["Tony Evers"])
        result = to_down_style("wisconsin gov tony evers spoke today", canonical)
        assert result == "Wisconsin Gov. Tony Evers spoke today"


# ---------------------------------------------------------------------------
# to_down_style -- period-bearing casing_variants must not double their
# period on restoration (regression: "gov" -> "Gov." colliding with a
# pre-existing "." in the source text produced "Gov..").
# ---------------------------------------------------------------------------


class TestCasingVariantPeriodNotDoubled:
    def test_convergence_with_variants_no_period_doubling(self):
        # Whether the source already carries the "Gov." period or not, the
        # restored fragment must be byte-identical -- exactly one period.
        canonical = build_canonical(_rules(), extra_nouns=["Tony Evers"])

        from_bare = to_down_style("gov tony evers signs bill", canonical)
        from_punctuated = to_down_style("Wisconsin Gov. Tony Evers signs bill", canonical)

        assert from_punctuated == "Wisconsin Gov. Tony Evers signs bill"

        fragment_bare = from_bare[from_bare.index("Gov.") :][: len("Gov. Tony Evers")]
        fragment_punctuated = from_punctuated[from_punctuated.index("Gov.") :][
            : len("Gov. Tony Evers")
        ]
        assert fragment_bare == fragment_punctuated == "Gov. Tony Evers"

    def test_idempotent_with_period_bearing_variant(self):
        canonical = build_canonical(_rules(), extra_nouns=["Tony Evers"])
        text = "gov tony evers spoke today"
        once = to_down_style(text, canonical)
        twice = to_down_style(once, canonical)
        assert once == twice
        assert once == "Gov. Tony Evers spoke today"

    def test_sentence_boundary_single_period_after_restoration(self):
        canonical = {"gov": "Gov."}
        result = to_down_style("meeting with the gov. next steps followed", canonical)
        assert result == "Meeting with the Gov. next steps followed"
        assert result.count(".") == 1


# ---------------------------------------------------------------------------
# CONVERGENCE TEST -- the load-bearing one for this task.
# ---------------------------------------------------------------------------


class TestConvergence:
    def test_over_capitalized_and_over_lowercased_converge_to_identical_bytes(self):
        rules = _rules()
        canonical = build_canonical(rules, extra_nouns=["Tony Evers"])

        over_capitalized = (
            "Campaign Attacks Heat Up In Wisconsin Governor Race Involving Tony Evers"
        )
        over_lowercased = (
            "campaign attacks heat up in wisconsin governor race involving tony evers"
        )

        result_from_caps = to_down_style(over_capitalized, canonical)
        result_from_lower = to_down_style(over_lowercased, canonical)

        # The load-bearing assertion: byte-identical regardless of source casing.
        assert result_from_caps == result_from_lower
        assert (
            result_from_caps
            == "Campaign attacks heat up in Wisconsin Governor race involving Tony Evers"
        )

    def test_convergence_with_mixed_case_third_variant(self):
        """A third, chaotically-mixed-case variant also converges to the same output."""
        rules = _rules()
        canonical = build_canonical(rules, extra_nouns=["Tony Evers"])

        mixed = "cAmPaIgN aTTacks heat UP in WISCONSIN governor RACE involving TONY evers"
        expected = "Campaign attacks heat up in Wisconsin Governor race involving Tony Evers"

        assert to_down_style(mixed, canonical) == expected


# ---------------------------------------------------------------------------
# entities.extract_proper_nouns -- header-row skipping
# ---------------------------------------------------------------------------


class TestExtractProperNounsHeaderSkip:
    def test_header_row_not_captured(self):
        md = (
            "| Speaker | Role/Title | Context | First Appearance |\n"
            "|---|---|---|---|\n"
            "| John Smith | Host | Intro | 0:00 |\n"
        )
        result = extract_proper_nouns(md)
        assert "Speaker" not in result
        assert "Role/Title" not in result
        assert "John Smith" in result

    def test_name_header_cell_also_skipped(self):
        md = "| Name | Role/Title | Context | First Appearance |\n| Jane Voss | Guest | - | - |\n"
        result = extract_proper_nouns(md)
        assert "Name" not in result
        assert "Jane Voss" in result


# ---------------------------------------------------------------------------
# entities.extract_proper_nouns -- surname registration
# ---------------------------------------------------------------------------


class TestExtractProperNounsSurname:
    def test_multi_word_name_registers_full_name_and_surname(self):
        # "Voss" (4 chars) clears the short-surname length filter -- see
        # TestExtractProperNounsShortSurname for the boundary itself.
        md = "| Robin Voss | Assembly Speaker | Budget debate | 1:20 |\n"
        result = extract_proper_nouns(md)
        assert "Robin Voss" in result
        assert "Voss" in result

    def test_single_word_candidate_never_matches(self):
        # The capture group requires at least 2 capitalized words; a lone
        # capitalized word in a table cell isn't a valid candidate.
        md = "| Governor | Title | Context | Time |\n"
        result = extract_proper_nouns(md)
        assert result == []


# ---------------------------------------------------------------------------
# entities.extract_proper_nouns -- stoplist particle skip
# ---------------------------------------------------------------------------


class TestExtractProperNounsStoplist:
    # NOTE: the real surname_stoplist entries ("van", "der", "de", "la",
    # "the") are all <=3 chars, so they'd be caught by the short-surname
    # length filter regardless of the stoplist. To isolate the stoplist
    # mechanism itself (independent of the length filter), these tests use
    # a synthetic 5-char stoplisted surname ("Vance") that would otherwise
    # pass the length check.

    def test_stoplisted_last_token_not_registered_as_surname(self):
        # "Vance" is the last (surname-position) token here, long enough to
        # pass the length filter on its own -- it must still be skipped
        # because it's in the stoplist, even though the full name is kept.
        md = "| Peter Vance | Senator | Budget debate | 2:00 |\n"
        result = extract_proper_nouns(md, stoplist={"vance"})
        assert "Peter Vance" in result
        assert "Vance" not in result

    def test_stoplist_is_case_insensitive(self):
        md = "| Peter Vance | Senator | Budget debate | 2:00 |\n"
        result = extract_proper_nouns(md, stoplist={"VANCE"})
        assert "Vance" not in result

    def test_no_stoplist_registers_surname_that_would_otherwise_be_stoplisted(self):
        # With no stoplist supplied at all, "Vance" (5 chars, passes the
        # length filter) IS registered -- proving the skip above was really
        # driven by the stoplist, not the length filter.
        md = "| Peter Vance | Senator | Budget debate | 2:00 |\n"
        result = extract_proper_nouns(md)
        assert "Vance" in result

    def test_particle_mid_name_never_promoted_to_standalone_surname(self):
        # A longer capitalized name where every word is title-cased: only
        # the real surname (last token) is ever considered for standalone
        # registration -- never a middle particle word, regardless of its
        # own length or stoplist membership.
        md = "| Willem Vanden Berg | Historian | Context | First |\n"
        result = extract_proper_nouns(md, stoplist={"van", "der", "de", "la", "the"})
        assert "Willem Vanden Berg" in result
        assert "Berg" in result
        assert "Vanden" not in result

    def test_default_stoplist_particles_never_registered_as_surname(self):
        # Literal brief scenario: a name containing "Van"/"Der" mid-name.
        # Both are 3 chars (caught by the length filter too) AND in the
        # default stoplist -- either way, only "Berg" (the actual last
        # token) is ever registered.
        md = "| Willem Van Der Berg | Historian | Context | First |\n"
        result = extract_proper_nouns(md, stoplist={"van", "der", "de", "la", "the"})
        assert "Willem Van Der Berg" in result
        assert "Berg" in result
        assert "Van" not in result
        assert "Der" not in result


# ---------------------------------------------------------------------------
# entities.extract_proper_nouns -- short-surname skip
# ---------------------------------------------------------------------------


class TestExtractProperNounsShortSurname:
    def test_three_char_surname_skipped(self):
        # PoC threshold: len(surname) > 3, so a 3-char surname is skipped.
        md = "| Jane Roe | Plaintiff | Context | Time |\n"
        result = extract_proper_nouns(md)
        assert "Jane Roe" in result
        assert "Roe" not in result

    def test_four_char_surname_registered(self):
        md = "| Jane Voss | Witness | Context | Time |\n"
        result = extract_proper_nouns(md)
        assert "Jane Voss" in result
        assert "Voss" in result


# ---------------------------------------------------------------------------
# entities.extract_proper_nouns -- provenance-comment tolerance
# ---------------------------------------------------------------------------


class TestExtractProperNounsProvenanceComment:
    def test_leading_html_comment_tolerated(self):
        md = (
            "<!-- Provenance: Generated by cardigan-v4 analyst phase -->\n\n"
            "## Speakers & Roles\n\n"
            "| Speaker | Role/Title | Context | First Appearance |\n"
            "|---|---|---|---|\n"
            "| Robin Voss | Assembly Speaker | Budget debate | 1:20 |\n"
        )
        result = extract_proper_nouns(md)
        assert "Robin Voss" in result
        assert "Voss" in result

    def test_multiline_provenance_comment_tolerated(self):
        md = (
            "<!--\n"
            "Provenance: Generated by cardigan-v4\n"
            "Review notes: none\n"
            "-->\n"
            "| Robin Voss | Assembly Speaker | Budget debate | 1:20 |\n"
        )
        result = extract_proper_nouns(md)
        assert "Robin Voss" in result


# ---------------------------------------------------------------------------
# entities.extract_proper_nouns -- dedup + order
# ---------------------------------------------------------------------------


class TestExtractProperNounsDedupOrder:
    def test_duplicate_rows_deduped_preserving_first_seen_order(self):
        md = (
            "| Robin Vos | Assembly Speaker | Budget debate | 1:20 |\n"
            "| Jane Voss | Witness | Testimony | 5:00 |\n"
            "| Robin Vos | Assembly Speaker | Closing remarks | 10:00 |\n"
        )
        result = extract_proper_nouns(md)
        assert result.count("Robin Vos") == 1
        assert result.index("Robin Vos") < result.index("Jane Voss")

    def test_full_name_and_surname_both_present_in_first_seen_order(self):
        md = "| Robin Voss | Assembly Speaker | Budget debate | 1:20 |\n"
        result = extract_proper_nouns(md)
        assert result.index("Robin Voss") < result.index("Voss")


# ---------------------------------------------------------------------------
# entities.extract_proper_nouns -- graceful empty
# ---------------------------------------------------------------------------


class TestExtractProperNounsGraceful:
    def test_none_input_returns_empty_list(self):
        assert extract_proper_nouns(None) == []

    def test_empty_string_returns_empty_list(self):
        assert extract_proper_nouns("") == []

    def test_no_table_rows_returns_empty_list(self):
        md = "## Speakers & Roles\n\nNo speakers identified in this transcript.\n"
        assert extract_proper_nouns(md) == []
