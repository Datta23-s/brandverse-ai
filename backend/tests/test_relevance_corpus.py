"""The relevance corpus is memory-shaped, and the eight unanswerable queries stay unanswerable
(docs/evaluation-plan.md §7).

Offline and cheap. Two things here are mechanical stand-ins for a reading that was also done by
hand when the corpus was frozen (`corpus.lock`): the size bound uses a word count (the encoder's
own count was checked against `max_item_tokens` when freezing), and the unanswerable guard looks
for the words an answer would need, not for meaning. Neither replaces the other.

The corpus was drafted blind, by a session that had only `brief.md`. A query it happens to answer
poorly or not at all is a result about the query, reported in the plan, not a reason to edit the
corpus: nothing here asserts that any answerable query has an answer.
"""

import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

from app.evaluation.relevance import verify_frozen
from app.memory.scoring import load_scoring
from app.memory.writeback import content_screens

RELEVANCE = Path(__file__).resolve().parents[2] / "evaluation" / "relevance"
ITEMS = yaml.safe_load((RELEVANCE / "corpus.yaml").read_text(encoding="utf-8"))
KINDS = {"persona", "competitor", "insight", "claim", "creative", "sector"}
BRANDS = {"mettlepan", "zarvik", "nookwell"}

# Query -> a pattern an item would need to contain to answer it. A hit is a stop-and-read: every
# hit in the frozen corpus was read in full on 2026-10-25 and judged NOT to answer the query. The
# judgement is the reason beside the id. A hit not listed fails (default-deny); a listed id that
# no longer hits fails too (a stale exemption).
ANSWER_WORDS = {
    "q43": r"discount|% off|diwali|promo|\bsales?\b",
    "q44": r"go live|goes live|launch|schedule|campaign date",
    "q45": r"wholesale|trade price|dealer",
    "q46": r"spring|collection",
    "q47": r"\bspend\b|\bspent\b|\bpaid\b|creator budget",
    "q48": r"conversion|\bconvert|top creator|best creator",
    "q49": r"\breturns?\b|refund|exchange policy|\bpolicy\b",
    "q50": r"\bpune\b|units sold|bundles sold|sales figure",
}
REVIEWED = {
    "q43": {"mp-08": "Cookhaus's frequent sales, a competitor's; no discount on Mettlepan's set"},
    "q44": {"nw-36": "how Nookwell shows seasonal prints; not a Zarvik campaign date"},
    "q47": {
        "zv-09": "customers asking whether the extra spend over PlantFuel is justified",
        "zv-39": "creators must disclose paid partnerships; no amount, no creator",
    },
    "q48": {
        "mp-30": "Mettlepan recipe creators 'convert better' than studio films: unquantified,"
        " not fitness creators, no ranking"
    },  # fmt: skip
    "q49": {
        "mp-40": "accurate marketplace listings avoid returns; states no returns policy",
        "nw-31": "true-colour images avoid returns; states no returns policy",
        "nw-39": "accurate sizes avoid returns; states no returns policy",
    },
}


def test_the_corpus_is_three_brands_of_forty() -> None:
    assert len(ITEMS) == 120
    assert Counter(i["brand"] for i in ITEMS) == {"mettlepan": 40, "zarvik": 40, "nookwell": 40}


def test_every_item_is_well_formed_and_ids_are_unique() -> None:
    assert len({i["id"] for i in ITEMS}) == len(ITEMS)
    for i in ITEMS:
        assert set(i) == {"id", "brand", "kind", "text"}, i["id"]
        assert i["brand"] in BRANDS and i["kind"] in KINDS, i["id"]
        assert i["id"].startswith({"mettlepan": "mp", "zarvik": "zv", "nookwell": "nw"}[i["brand"]])


def test_no_item_is_ephemera_or_personal_data() -> None:
    """Memory.md §6.2, by the same screens write-back applies."""
    flagged = {i["id"]: content_screens(i["text"]) for i in ITEMS if content_screens(i["text"])}
    assert flagged == {}


def test_no_item_is_longer_than_one_fact_can_be() -> None:
    cap = load_scoring().writeback.max_item_tokens
    assert max(len(i["text"].split()) for i in ITEMS) < cap // 2


@pytest.mark.parametrize("qid", sorted(ANSWER_WORDS))
def test_no_unreviewed_item_contains_what_an_answer_to_an_unanswerable_query_needs(
    qid: str,
) -> None:
    hits = {i["id"] for i in ITEMS if re.search(ANSWER_WORDS[qid], i["text"], re.IGNORECASE)}
    reviewed = set(REVIEWED.get(qid, {}))
    assert hits - reviewed == set(), f"{qid}: read {sorted(hits - reviewed)} before freezing"
    assert reviewed - hits == set(), f"{qid}: reviewed items that no longer match: stale"


def test_the_corpus_matches_its_lock() -> None:
    verify_frozen(RELEVANCE / "corpus.lock", corpus=RELEVANCE / "corpus.yaml")
