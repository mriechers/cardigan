"""Tests for api.services.whisper_prompt.build_initial_prompt."""

from api.services.whisper_prompt import build_initial_prompt


def test_empty_inputs_yield_station_prefix():
    assert build_initial_prompt() == "PBS Wisconsin."


def test_speakers_and_terms_composed():
    prompt = build_initial_prompt(
        speakers=["Frederica Freyberg", "Josh Kaul"],
        context_terms=["Act 10"],
        glossary_terms=["Waukesha"],
    )
    assert prompt == "PBS Wisconsin. Speakers: Frederica Freyberg, Josh Kaul. Key terms: Act 10, Waukesha."


def test_priority_order_speakers_context_glossary():
    prompt = build_initial_prompt(
        speakers=["A Person"],
        context_terms=["Context Term"],
        glossary_terms=["Glossary Term"],
    )
    assert prompt.index("A Person") < prompt.index("Context Term") < prompt.index("Glossary Term")


def test_dedupe_across_inputs_case_insensitive():
    prompt = build_initial_prompt(
        speakers=["Frederica Freyberg"],
        context_terms=["frederica freyberg", "Act 10"],
        glossary_terms=["ACT 10", "Waukesha"],
    )
    assert prompt.count("Freyberg") == 1
    assert prompt.lower().count("act 10") == 1
    assert "Waukesha" in prompt


def test_budget_truncates_at_term_boundary():
    glossary = [f"Glossaryterm{i:02d}" for i in range(100)]  # 14 chars each
    prompt = build_initial_prompt(speakers=["Host Name"], glossary_terms=glossary, budget=120)
    assert len(prompt) <= 120
    # No term is cut mid-word: every kept term appears in full
    tail = prompt.split("Key terms: ")[1].rstrip(".")
    for term in tail.split(", "):
        assert term in glossary


def test_glossary_dropped_before_context_terms():
    context = ["ContextA", "ContextB"]
    glossary = [f"Glossary{i}" for i in range(50)]
    prompt = build_initial_prompt(context_terms=context, glossary_terms=glossary, budget=60)
    assert "ContextA" in prompt


def test_speakers_kept_even_over_budget():
    speakers = [f"Speaker Name {i}" for i in range(20)]
    prompt = build_initial_prompt(speakers=speakers, budget=50)
    for name in speakers:
        assert name in prompt


def test_whitespace_normalized():
    prompt = build_initial_prompt(speakers=["  Frederica   Freyberg  "])
    assert "Frederica Freyberg" in prompt
    assert "  " not in prompt
