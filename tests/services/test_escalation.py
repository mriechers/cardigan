import pytest

from api.services.escalation import bump_family, parse_model_family


@pytest.mark.parametrize(
    "slug,expected",
    [
        ("anthropic/claude-4.5-haiku-20251001", "haiku"),
        ("anthropic/claude-4.6-sonnet-20260217", "sonnet"),
        ("anthropic/claude-sonnet-4.6", "sonnet"),  # word order varies
        ("anthropic/claude-opus-4-8", "opus"),
        ("openai/gpt-4o", None),
        (None, None),
        ("", None),
    ],
)
def test_parse_model_family(slug, expected):
    assert parse_model_family(slug) == expected


@pytest.mark.parametrize(
    "family,expected",
    [
        ("haiku", "sonnet"),
        ("sonnet", "opus"),
        ("opus", None),  # terminal
        (None, None),
        ("mystery", None),
    ],
)
def test_bump_family(family, expected):
    assert bump_family(family) == expected
