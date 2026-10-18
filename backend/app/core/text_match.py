"""Deterministic text matching for the truth and registry gates (Architecture.md §8.1: "Matching —
deterministic: case-insensitive phrase match or a stored regular expression. Never an LLM
judgement.").

The contract, in full:

1. **Normalising** folds Unicode compatibility forms (NFKC) and case, reads number abbreviations
   alike — "#1", "Nr. 1", "No.1" and "No. 1" all become "no 1" — and treats every run of
   characters that is not a letter or digit as one space (so hyphens and dashes are spaces).
2. **A phrase matches** when its words appear in order as whole words, where **each gap between
   two of its words may be a space, a hyphen or dash, or nothing**: "market leading" matches
   "market leading", "market-leading", "market–leading" and "marketleading" (2026-10-18; closed
   compounds such as "bestselling" were missed before). Whole words: "best" does not match
   "bestow", and "no 1" does not match "no10".
3. **Exceptions** a phrase declares are honoured: "best" unless followed by "before" (a
   best-before date is not a superlative).

No stemming, no synonyms, no paraphrase. A paraphrase of a restricted claim is not caught here;
that gap is recorded as threat V2 in docs/evaluation-plan.md.
"""

import re
import unicodedata
from collections.abc import Sequence
from functools import lru_cache

from app.errors import RegistryRuleInvalid

_NON_WORD = re.compile(r"[\W_]+")
# "#1" and "Nr. 1" are read as "No. 1" (applied after casefolding, before punctuation goes).
_HASH_NUMBER = re.compile(r"#(?=\s*\d)")
_NR_NUMBER = re.compile(r"(?<!\w)nr\.?(?=\s*\d)")


def normalise(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = _NR_NUMBER.sub(" no ", _HASH_NUMBER.sub(" no ", folded))
    return _NON_WORD.sub(" ", folded).strip()


def _words(phrase: str) -> str:
    """A normalised phrase as a pattern whose word gaps may be one space or nothing (hyphens and
    dashes are already spaces after normalising)."""
    return " ?".join(re.escape(w) for w in normalise(phrase).split())


@lru_cache(maxsize=4096)
def phrase_regex(phrase: str, unless_followed_by: tuple[str, ...] = ()) -> re.Pattern[str] | None:
    """The compiled pattern that finds `phrase` in NORMALISED text, or None for an empty phrase
    (nothing but punctuation never matches)."""
    core = _words(phrase)
    if not core:
        return None
    exceptions = "".join(f"(?! ?{_words(f)}(?!\\w))" for f in unless_followed_by if _words(f))
    return re.compile(f"(?<!\\w){core}(?!\\w){exceptions}")


def contains_phrase(text: str, phrase: str, unless_followed_by: Sequence[str] = ()) -> bool:
    """True if `phrase` occurs in `text` under the contract in the module docstring."""
    pattern = phrase_regex(phrase, tuple(unless_followed_by))
    return pattern is not None and pattern.search(normalise(text)) is not None


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
