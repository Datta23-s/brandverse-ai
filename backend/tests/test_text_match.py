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
        ("bestinclass", "best in class", True),  # a closed compound (2026-10-18)
        ("best–in–class", "best in class", True),  # en dashes
        ("best-of-class", "best in class", False),  # other words in between
        ("curesall", "cures", False),  # whole words only
        ("bestow", "best", False),
        ("it cures", "cures", True),
        ("anything", "!!!", False),  # an empty phrase never matches
        ("", "cures", False),
    ],
)
def test_phrase_matches_whole_words_in_order(text: str, phrase: str, expected: bool) -> None:
    assert contains_phrase(text, phrase) is expected


@pytest.mark.parametrize("written", ["No.1", "No 1", "No. 1", "#1", "# 1", "nr 1", "Nr. 1", "no1"])
def test_number_abbreviations_read_alike(written: str) -> None:
    assert contains_phrase(f"the {written} choice", "no 1")


@pytest.mark.parametrize("text", ["no10", "No. 12 in the series", "Nr 15"])
def test_a_different_number_is_not_no_1(text: str) -> None:
    assert not contains_phrase(text, "no 1")


def test_a_declared_exception_suppresses_a_match() -> None:
    assert not contains_phrase("Best before 03/2027", "best", ["before"])
    assert not contains_phrase("bestbefore", "best", ["before"])  # not a whole word anyway
    assert contains_phrase("Best beforehand", "best", ["before"])  # "beforehand" is not "before"
    assert contains_phrase("The best. Best before 03/2027", "best", ["before"])


def test_regex_rules_are_case_insensitive() -> None:
    assert matches_rule("Guaranteed RESULTS", "regex", r"guaranteed\s+results")


def test_a_rule_that_cannot_compile_raises_rather_than_not_matching() -> None:
    with pytest.raises(RegistryRuleInvalid, match="does not compile"):
        matches_rule("text", "regex", "(unclosed")


def test_an_unknown_match_type_raises() -> None:
    with pytest.raises(RegistryRuleInvalid, match="unknown match_type"):
        matches_rule("text", "llm", "anything")
