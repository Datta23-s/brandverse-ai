"""A lexical check that a cited claim mentions what the statement says (threat V12).

The gate verifies that a citation RESOLVES, not that the claim entails the statement (V4). Under a
revision loop that is exploitable without anyone intending it: a statement flagged for citing no
claim can be made to pass by citing ANY active claim, and the model is only optimising against the
check it was given. The instance: "lightweight walking shoe" (R10, 2026-10-30) was reported with no
claim on draft 1, kept in draft 2 and cited to "Lace-up, low top, medium width, almond toe, low
arch", and passed.

This is a WORD-OVERLAP test, not entailment, and it has no model call and no word list of
properties. A statement is reduced to its content terms: lowercase tokens, lightly stemmed, with
function words and the words that merely NAME the product (its category, its name, "product",
"design") taken out. What is left is what the statement asserts about it.

- a SHORT statement (up to three such terms, the shape of a property span like "lightweight walking
  shoe") needs EVERY remaining term in the claim's own text; "lightweight" is not in a claim about
  lace-up construction, so it fails;
- a LONGER statement (a sentence) needs at least ONE remaining term in common with the claim, so a
  citation of a claim that shares nothing with it fails.

What it does NOT catch, and why it is only a partial check:
- paraphrase and synonymy in the other direction: a claim saying "light" does not contain
  "lightweight", so a correct citation can FAIL (a false block; the reviser can reword);
- a property word that happens to appear in the claim in another sense, or negated ("not
  lightweight"), passes;
- in a long statement, ONE shared incidental word ("shoe", "upper") is enough, so a mis-cited
  sentence that also names a real feature passes;
- anything the extractor never extracts (V11) is never checked at all.
The alternative, a model-based entailment check on the highest-volume path against a 20-a-day tier,
is refused.
"""

import re
from collections.abc import Collection, Iterable

SHORT_STATEMENT_TERMS = 3

# Function words only (no adjective, noun or verb that carries a property), plus the nouns that
# merely refer to the product. "not" is NOT here: negation changes what a statement says.
FUNCTION_WORDS: frozenset[str] = frozenset(
    "a an the and or but nor of to for with without in on at by from as into onto over under "
    "is are was were be been being am do does did has have had it its this that these those "
    "there here you your yours we our ours us they their them he she his her i me my "
    "will can could would should may might must so than then too very also just more most "
    "all any each every some such own same other another which who whom whose what when where "
    "why how if while about up out off per via "
    "product products item items design designs model models pair pairs one ones thing "
    "things".split()
)
# "up" above is a function word for phrases like "pick up", but "lace-up" must keep it; hyphenated
# compounds are split, then each part is judged on its own, so "up" being a function word costs
# nothing here: it simply never counts as a term to be matched.

_TOKEN = re.compile(r"[a-z0-9]+")


def stem(word: str) -> str:
    """Light suffix stripping so "shoes" matches "shoe" and "walking" matches "walk"."""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "xes", "ches", "shes", "zes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        return word[:-1]
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> list[str]:
    return [stem(t) for t in _TOKEN.findall(text.casefold())]


def neutral_stems(phrases: Iterable[str | None]) -> frozenset[str]:
    """The stems of the words that merely name the product: its category and its name."""
    return frozenset(t for p in phrases if p for t in tokens(p))


def content_terms(statement: str, neutral: Collection[str]) -> list[str]:
    seen: dict[str, None] = {}
    for t in tokens(statement):
        if t not in FUNCTION_WORDS and t not in neutral and len(t) > 1:
            seen.setdefault(t)
    return list(seen)


def missing_terms(statement: str, claim_text: str, neutral: Collection[str]) -> list[str]:
    """The terms of `statement` the cited claim does not support, or [] when the citation passes."""
    terms = content_terms(statement, neutral)
    if not terms:
        return []  # it says nothing beyond naming the product
    claim = set(tokens(claim_text)) - FUNCTION_WORDS
    if len(terms) <= SHORT_STATEMENT_TERMS:
        return [t for t in terms if t not in claim]
    return [] if any(t in claim for t in terms) else terms
