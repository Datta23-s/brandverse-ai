"""The relevance corpus is memory-shaped, and the eight unanswerable queries stay unanswerable
(docs/evaluation-plan.md §7).

Offline and cheap. Two things here are mechanical stand-ins for a reading that was also done by
hand when the corpus was frozen (`corpus.lock`): the token cap uses a word count (the encoder's
own count, 67 at most, was checked against `max_item_tokens` when freezing), and the
unanswerable guard looks for the words an answer would need, not for meaning. Neither replaces the
other.
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

# Query -> a pattern an item would need to contain to answer it. A match is a stop-and-read.
ANSWER_WORDS = {
    "q43": r"discount|% off|diwali",
    "q44": r"go live|goes live|launch|campaign date|schedule",
    "q45": r"wholesale|trade price|dealer",
    "q46": r"spring|collection|season.s sales",
    "q47": r"\bspend\b|\bspent\b|fee paid|\bpaid\b|creator budget",
    "q48": r"conversion|best-performing creator|top creator",
    "q49": r"\breturn|refund|exchange policy|\bpolicy\b",
    "q50": r"\bpune\b|units sold|bundles sold|sales figure",
}


def test_the_corpus_is_three_brands_of_about_forty() -> None:
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
def test_no_item_contains_what_an_answer_to_an_unanswerable_query_needs(qid: str) -> None:
    hits = [i["id"] for i in ITEMS if re.search(ANSWER_WORDS[qid], i["text"], re.IGNORECASE)]
    assert hits == [], f"{qid}: read {hits} before freezing"


def test_the_claim_sensitive_queries_have_material_and_a_trap_that_would_overstate() -> None:
    """q27 (plant protein and recovery) and q28 (magnesium and sleep): items that bear on them,
    including ones that would overstate if cited loosely, so the claim gate has work to do."""
    text = {i["id"]: i["text"] for i in ITEMS}
    # Would overstate q27 if cited for 'recovery': the study measured lean mass and strength.
    assert "did not measure recovery" in text["zv-26"]
    # Would overstate q28 if cited for 'sleep': the approved wording is about tiredness.
    assert "not about sleep" in text["zv-30"]
    # The rules and the past rejections that say how far each can be taken.
    assert "recover faster" in text["zv-28"] and "knocks you out" in text["zv-32"]


def test_the_corpus_matches_its_lock() -> None:
    verify_frozen(RELEVANCE / "corpus.lock", corpus=RELEVANCE / "corpus.yaml")
