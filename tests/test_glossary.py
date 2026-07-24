"""Tests for api.services.glossary — shared glossary read/write helpers."""

from pathlib import Path

import pytest

from api.services import glossary

SAMPLE = """# PBS Wisconsin Transcript Glossary

Reference for transcript processing agents.

## Whisper Prompt Terms

Terms merged into the WhisperX initial_prompt for audio transcription jobs.
Only lines beginning with `- ` are injected into prompts.

- PBS Wisconsin
- Frederica Freyberg
- Waukesha

## Place Names

| Correct | Common Misspellings |
|---------|-------------------|
| Manitowoc | Manitowac |
| Waukesha | Wakesha |

## Editor Corrections

Names corrected during human review.

| Correct | Model Tendency | Context |
|---------|---------------|---------|
| Sean Duffy | Shawn Duffy | Former WI congressman |

## Name Disambiguation

| Name | Role | Do NOT confuse with |
|------|------|-------------------|
| Shawn Johnson | IWP host | Sean Duffy |
"""


@pytest.fixture
def glossary_file(tmp_path: Path) -> Path:
    path = tmp_path / "glossary.md"
    path.write_text(SAMPLE)
    return path


class TestGetWhisperTerms:
    def test_reads_bullets_only(self, glossary_file: Path):
        terms = glossary.get_whisper_terms(glossary_file)
        assert terms == ["PBS Wisconsin", "Frederica Freyberg", "Waukesha"]

    def test_prose_lines_are_skipped(self, glossary_file: Path):
        terms = glossary.get_whisper_terms(glossary_file)
        assert not any("injected" in t for t in terms)

    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert glossary.get_whisper_terms(tmp_path / "nope.md") == []

    def test_missing_section_returns_empty(self, tmp_path: Path):
        path = tmp_path / "glossary.md"
        path.write_text("# Glossary\n\n## Place Names\n\n| Correct | Misspellings |\n")
        assert glossary.get_whisper_terms(path) == []


class TestAddWhisperTerms:
    def test_appends_new_terms(self, glossary_file: Path):
        added = glossary.add_whisper_terms(["Janet Protasiewicz", "Oconomowoc"], glossary_file)
        assert added == 2
        terms = glossary.get_whisper_terms(glossary_file)
        assert terms[-2:] == ["Janet Protasiewicz", "Oconomowoc"]

    def test_idempotent_double_add(self, glossary_file: Path):
        assert glossary.add_whisper_terms(["Oshkosh"], glossary_file) == 1
        assert glossary.add_whisper_terms(["Oshkosh"], glossary_file) == 0
        assert glossary.get_whisper_terms(glossary_file).count("Oshkosh") == 1

    def test_dedupe_is_case_insensitive(self, glossary_file: Path):
        assert glossary.add_whisper_terms(["waukesha"], glossary_file) == 0

    def test_dedupe_against_table_correct_column(self, glossary_file: Path):
        # Manitowoc exists only in the Place Names table, not the bullets
        assert glossary.add_whisper_terms(["Manitowoc"], glossary_file) == 0

    def test_dedupe_within_batch(self, glossary_file: Path):
        assert glossary.add_whisper_terms(["Wausau", "Wausau", " wausau "], glossary_file) == 1

    def test_whitespace_normalized(self, glossary_file: Path):
        glossary.add_whisper_terms(["  Eau   Claire  "], glossary_file)
        assert "Eau Claire" in glossary.get_whisper_terms(glossary_file)

    def test_creates_section_when_missing(self, tmp_path: Path):
        path = tmp_path / "glossary.md"
        path.write_text("# Glossary\n\n## Place Names\n\n| Correct | Misspellings |\n|---|---|\n| Wausau | Wasau |\n")
        added = glossary.add_whisper_terms(["Kenosha"], path)
        assert added == 1
        assert glossary.get_whisper_terms(path) == ["Kenosha"]

    def test_tables_survive_append(self, glossary_file: Path):
        glossary.add_whisper_terms(["La Crosse"], glossary_file)
        text = glossary_file.read_text()
        assert "| Manitowoc | Manitowac |" in text
        assert "| Sean Duffy | Shawn Duffy | Former WI congressman |" in text

    def test_missing_file_returns_zero(self, tmp_path: Path):
        assert glossary.add_whisper_terms(["Kenosha"], tmp_path / "nope.md") == 0


class TestAddCorrections:
    def test_appends_to_editor_corrections_table(self, glossary_file: Path):
        added = glossary.add_corrections([("Jill Karofsky", "Jill Karovsky", "Supreme Court")], glossary_file)
        assert added == 1
        text = glossary_file.read_text()
        assert "| Jill Karofsky | Jill Karovsky | Supreme Court |" in text
        # Row must land inside the Editor Corrections section
        section = text.split("## Editor Corrections")[1].split("## Name Disambiguation")[0]
        assert "Jill Karofsky" in section

    def test_correct_form_also_added_to_whisper_terms(self, glossary_file: Path):
        glossary.add_corrections([("Jill Karofsky", "Jill Karovsky", "")], glossary_file)
        assert "Jill Karofsky" in glossary.get_whisper_terms(glossary_file)

    def test_skips_already_known_pair(self, glossary_file: Path):
        added = glossary.add_corrections([("Sean Duffy", "Shawn Duffy", "dup")], glossary_file)
        assert added == 0

    def test_skips_empty_forms(self, glossary_file: Path):
        assert glossary.add_corrections([("", "Wrong", ""), ("Right", "", "")], glossary_file) == 0

    def test_missing_file_returns_zero(self, tmp_path: Path):
        assert glossary.add_corrections([("A", "B", "")], tmp_path / "nope.md") == 0


class TestReadGlossarySummary:
    def test_summary_counts(self, glossary_file: Path):
        summary = glossary.read_glossary_summary(glossary_file)
        assert summary["whisper_terms"] == ["PBS Wisconsin", "Frederica Freyberg", "Waukesha"]
        assert summary["whisper_term_count"] == 3
        assert summary["correction_count"] == 1

    def test_missing_file(self, tmp_path: Path):
        summary = glossary.read_glossary_summary(tmp_path / "nope.md")
        assert summary == {"whisper_terms": [], "whisper_term_count": 0, "correction_count": 0}


class TestKnowledgeDirEnv:
    def test_path_resolves_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))
        assert glossary.get_glossary_path() == tmp_path / "glossary.md"
