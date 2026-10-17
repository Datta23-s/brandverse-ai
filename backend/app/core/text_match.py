"""Deterministic text matching for the truth and registry gates (Architecture.md §8.1: "Matching —
deterministic: case-insensitive phrase match or a stored regular expression. Never an LLM
judgement.").

A phrase matches when its normalised words appear, in order and adjacent, as whole words of the
normalised text: "Best in class" matches "the BEST-in-class kettle" but not "bestinclass" or
"best-of-class". Normalising folds case and Unicode compatibility forms (NFKC), and treats every
run of characters that is not a letter or digit as one space. That is the whole contract: no
stemming, no synonyms, no paraphrase. A paraphrase of a restricted claim is not caught here; that
gap is recorded as threat V2 in docs/evaluation-plan.md.
"""

import re
import unicodedata

from app.errors import RegistryRuleInvalid

_NON_WORD = re.compile(r"[\W_]+")


def normalise(text: str) -> str:
    return _NON_WORD.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()


def contains_phrase(text: str, phrase: str) -> bool:
    """True if `phrase`, normalised, occurs in `text` on whole-word boundaries. An empty phrase
    (nothing but punctuation) never matches."""
    needle = normalise(phrase)
    return bool(needle) and f" {needle} " in f" {normalise(text)} "


def matches_rule(text: str, match_type: str, pattern: str) -> bool:
    """A registry rule's pattern against `text`: 'phrase' as above, 'regex' case-insensitively.

    Raises RegistryRuleInvalid for an unknown match type or a pattern that does not compile; the
    gates treat that as a block (fail closed), never as "no match".
    """
    if match_type == "phrase":
        return contains_phrase(text, pattern)
    if match_type == "regex":
        try:
            return re.search(pattern, text, flags=re.IGNORECASE) is not None
        except re.error as e:
            raise RegistryRuleInvalid(f"pattern does not compile: {e}") from e
    raise RegistryRuleInvalid(f"unknown match_type {match_type!r}")
