"""Deterministic matching for the truth and registry gates (app/core/text_match.py)."""

import pytest

from app.core.text_match import contains_phrase, matches_rule, normalise
from app.errors import RegistryRuleInvalid


def test_normalise_folds_case_punctuation_and_compatibility_forms() -> None:
    assert normalise("  BEST—in_class!!  ") == "best in class"
    assert normalise("ｆｕｌｌｗｉｄｔｈ") == "fullwidth"  # NFKC
    assert normalise("Straße") == "strasse"  # casefold, not lower


@pytest.mark.parametrize(
    ("text", "phrase", "expected"),
    [
        ("the BEST-in-class kettle", "best in class", True),
        ("“Clinically proven” formula", "clinically proven", True),
        ("bestinclass", "best in class", False),
        ("best-of-class", "best in class", False),
        ("curesall", "cures", False),  # whole words only
        ("it cures", "cures", True),
        ("anything", "!!!", False),  # an empty phrase never matches
        ("", "cures", False),
    ],
)
def test_phrase_matches_whole_words_in_order(text: str, phrase: str, expected: bool) -> None:
    assert contains_phrase(text, phrase) is expected


def test_regex_rules_are_case_insensitive() -> None:
    assert matches_rule("Guaranteed RESULTS", "regex", r"guaranteed\s+results")


def test_a_rule_that_cannot_compile_raises_rather_than_not_matching() -> None:
    with pytest.raises(RegistryRuleInvalid, match="does not compile"):
        matches_rule("text", "regex", "(unclosed")


def test_an_unknown_match_type_raises() -> None:
    with pytest.raises(RegistryRuleInvalid, match="unknown match_type"):
        matches_rule("text", "llm", "anything")
