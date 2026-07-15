"""Tests for api.services.transcript_diff.mine_corrections."""

from api.services.transcript_diff import mine_corrections


def test_recurring_proper_noun_fix_detected():
    pairs = [
        ("Justice Janet Protasavich ruled today.", "Justice Janet Protasiewicz ruled today."),
        ("According to Protasavich, the case is closed.", "According to Protasiewicz, the case is closed."),
    ]
    entries = mine_corrections(pairs)
    assert ("Protasiewicz", "Protasavich", "Transcript review correction") in entries


def test_single_occurrence_rejected_without_known_term():
    pairs = [("We spoke with Wakesha officials.", "We spoke with Waukesha officials.")]
    assert mine_corrections(pairs) == []


def test_single_occurrence_accepted_for_known_intake_term():
    pairs = [("Attorney General Josh Call spoke.", "Attorney General Josh Kaul spoke.")]
    entries = mine_corrections(pairs, known_terms=["Josh Kaul"])
    assert entries == []  # replacement is 'Kaul' alone, not the full name

    pairs = [("We heard from Josh Call today.", "We heard from Josh Kaul today.")]
    entries = mine_corrections(pairs, known_terms=["Kaul"])
    assert ("Kaul", "Call", "Transcript review correction") in entries


def test_multiword_name_fix():
    pairs = [
        ("Host Shawn Duffy opened the show.", "Host Sean Duffy opened the show."),
        ("Shawn Duffy then asked about the budget.", "Sean Duffy then asked about the budget."),
    ]
    entries = mine_corrections(pairs)
    assert any(e[0] == "Sean" and e[1] == "Shawn" for e in entries)


def test_rewording_not_mined():
    pairs = [
        (
            "The committee decided to postpone the vote until next week.",
            "The committee chose to delay the vote until the following week.",
        ),
        (
            "The committee decided to postpone the vote until next week.",
            "The committee chose to delay the vote until the following week.",
        ),
    ]
    # Lowercase rewordings carry no capitalized token -> not corrections
    assert mine_corrections(pairs) == []


def test_long_replacements_skipped():
    pairs = [
        ("He said the Plan A was fine.", "He said the entirely different Proposal B framework was fine."),
        ("He said the Plan A was fine.", "He said the entirely different Proposal B framework was fine."),
    ]
    assert mine_corrections(pairs) == []


def test_case_only_changes_ignored():
    pairs = [
        ("we visited waukesha county.", "We visited Waukesha county."),
        ("we visited waukesha county.", "We visited Waukesha county."),
    ]
    # Same word, different case — SequenceMatcher runs on lowercased tokens
    assert mine_corrections(pairs) == []


def test_unchanged_segments_are_free():
    pairs = [("Same text.", "Same text.")] * 10
    assert mine_corrections(pairs) == []
