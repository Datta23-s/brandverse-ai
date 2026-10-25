"""The blind relevance-labelling task (app/evaluation/relevance.py; scripts/build_relevance_pool.py,
scripts/freeze_relevance_labels.py; docs/evaluation-plan.md §7, decided 2026-10-18).

The point of every test here is that the labels cannot be contaminated: nothing in the page or
its payload says what any system thinks, duplicates are unmarked, and a frozen set never moves.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path

import pytest

from app.evaluation.relevance import (
    DEFINITIONS,
    Item,
    Query,
    bm25_rank,
    build_pool,
    precision_at_10,
    resolve,
    self_consistency,
    validate_labels,
)
from scripts.build_relevance_pool import render_page, write_task
from scripts.freeze_relevance_labels import NotFreezable, freeze

ITEMS = [
    Item(f"m{n:02d}", text)
    for n, text in enumerate(
        [
            "Price-sensitive first-time buyers aged 22 to 30 find running shoes on Instagram.",
            "Competitor X positions its trail shoes on warranty length rather than price.",
            "Founder-story copy outperforms spec-sheet copy in the footwear category.",
            "Monsoon-season campaigns for waterproof earphones should lead with the IPX rating.",
            "Health supplement buyers respond to protein per serving, stated as a figure.",
            "Cast iron cookware shoppers worry about rust and seasoning upkeep.",
            "Mattress buyers compare firmness and trial periods before price.",
            "Students buy budget neckband earphones for long battery life.",
            "Hook: built for the monsoon scored well for the earphone launch.",
            "Gifting season lifts kitchenware sales in October and November.",
            "Collagen buyers ask about the source of the collagen before the dose.",
            "Trail runners value grip on wet rock above cushioning.",
        ]
    )
]
QUERIES = [
    Query("q1", "How do young buyers discover running shoes?"),
    Query("q2", "What should a monsoon earphone campaign lead with?"),
    Query("q3", "What do supplement buyers want to see?"),
    Query("q4", "How do competitors position trail shoes?"),
    Query("q5", "What worries cast iron shoppers?"),
    Query("q6", "What do mattress buyers compare?"),
]


def fake_encoder(texts: Sequence[str]) -> list[list[float]]:
    """Deterministic bag-of-words hashing into 32 dimensions (tests only)."""
    out = []
    for t in texts:
        v = [0.0] * 32
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            v[int(hashlib.sha256(w.encode()).hexdigest(), 16) % 32] += 1.0
        out.append(v)
    return out


class FakeEncoder:
    """fake_encoder behind the Encoder protocol; records which method embedded which text."""

    model, version, dimensions = "fake", "1", 32

    def __init__(self) -> None:
        self.as_query: list[str] = []
        self.as_document: list[str] = []

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.as_document += texts
        return fake_encoder(texts)

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        self.as_query += texts
        return fake_encoder(texts)


def pool(
    seed: int = 7, duplicates: int = 2, depth: int = 4
) -> tuple[dict[str, object], dict[str, object]]:
    return build_pool(QUERIES, ITEMS, FakeEncoder(), depth=depth, duplicates=duplicates, seed=seed)


def test_queries_are_embedded_as_queries_and_items_as_documents() -> None:
    encoder = FakeEncoder()
    build_pool(QUERIES, ITEMS, encoder, depth=4, duplicates=0, seed=1)
    assert encoder.as_query == [q.text for q in QUERIES]
    assert encoder.as_document == [i.text for i in ITEMS]


# ── Blind ────────────────────────────────────────────────────────────────────────────────────


def test_the_payload_carries_only_opaque_ids_and_text() -> None:
    payload, _ = pool()
    assert set(payload) == {"definitions", "queries", "pool_sha256"}
    for q in payload["queries"]:  # type: ignore[union-attr]
        assert set(q) == {"id", "text", "candidates"}
        for c in q["candidates"]:
            assert set(c) == {"id", "text"}  # no score, rank, retriever or source id
            assert c["id"] not in {i.id for i in ITEMS}  # opaque, not the item id


def test_nothing_on_the_page_marks_a_duplicate_or_a_ranking() -> None:
    # The page is the template plus the payload; the payload is checked above, so the template
    # must name nothing about how candidates were found or ordered, and no real id may leak.
    from scripts.build_relevance_pool import TEMPLATE

    template = TEMPLATE.read_text(encoding="utf-8").lower()
    for word in ("duplicate", "score", "rank", "bm25", "vector", "retriever", "similar"):
        assert word not in template, word
    page = render_page(pool()[0])
    assert not re.search(r'"(m\d\d|q\d)"', page)  # no item or query id


def test_the_definitions_are_shown_in_the_task() -> None:
    payload, _ = pool()
    assert payload["definitions"] == dict(DEFINITIONS)
    assert set(DEFINITIONS) == {"relevant", "partial", "not_relevant"}
    assert all(
        d.endswith(".") and len(d.split(".")) == 2 for d in DEFINITIONS.values()
    )  # one sentence


def test_text_cannot_break_out_of_the_page() -> None:
    query = {"id": "x", "text": "</script><b>", "candidates": []}
    payload = {"definitions": {}, "queries": [query], "pool_sha256": "0"}
    page = render_page(payload)
    assert "</script><b>" not in page
    assert page.count("</script>") == 2  # the page's own two script tags only


# ── Pool ─────────────────────────────────────────────────────────────────────────────────────


def test_each_pool_is_the_union_of_lexical_and_vector_tops() -> None:
    payload, key = pool(depth=4)
    items = {code: iid for code, iid in key["items"].items()}  # type: ignore[union-attr]
    vectors = dict(zip([i.id for i in ITEMS], fake_encoder([i.text for i in ITEMS]), strict=True))
    from app.evaluation.relevance import vector_rank

    for sitting in payload["queries"]:  # type: ignore[union-attr]
        qid = key["queries"][sitting["id"]]["query"]  # type: ignore[index]
        query = next(q for q in QUERIES if q.id == qid)
        expected = set(bm25_rank(query.text, ITEMS)[:4]) | set(
            vector_rank(fake_encoder([query.text])[0], vectors)[:4]
        )
        assert {items[c["id"]] for c in sitting["candidates"]} == expected


def test_the_hidden_duplicates_are_extra_sittings_of_existing_queries() -> None:
    payload, key = pool(duplicates=2)
    sittings = payload["queries"]
    assert len(sittings) == len(QUERIES) + 2  # type: ignore[arg-type]
    marked = [v for v in key["queries"].values() if v["duplicate"]]  # type: ignore[union-attr]
    assert len(marked) == 2
    for m in marked:
        same = [v for v in key["queries"].values() if v["query"] == m["query"]]  # type: ignore[union-attr]
        assert len(same) == 2  # the original and its unmarked repeat


def test_the_pool_is_reproducible_from_its_seed() -> None:
    assert pool(seed=11) == pool(seed=11)
    assert pool(seed=11)[0]["pool_sha256"] != pool(seed=12)[0]["pool_sha256"]


# ── Labels: validated, resolved, frozen ──────────────────────────────────────────────────────


def labels_for(payload: dict[str, object], judgment: str = "relevant") -> dict[str, object]:
    return {
        "pool_sha256": payload["pool_sha256"],
        "labeller": "Kundan",
        "started_at": "2026-10-18T10:00:00Z",
        "exported_at": "2026-10-18T12:00:00Z",
        "labels": {
            q["id"]: {c["id"]: {"judgment": judgment, "at": "2026-10-18T11:00:00Z"}
                      for c in q["candidates"]}
            for q in payload["queries"]  # type: ignore[union-attr]
        },
    }  # fmt: skip


def test_complete_labels_on_the_right_pool_validate() -> None:
    payload, _ = pool()
    assert validate_labels(payload, labels_for(payload)) == []


def test_incomplete_unnamed_or_foreign_labels_are_refused() -> None:
    payload, _ = pool()
    labels = labels_for(payload)
    first = next(iter(labels["labels"].values()))  # type: ignore[union-attr]
    first.pop(next(iter(first)))
    labels["labeller"] = " "
    labels["pool_sha256"] = "elsewhere"
    problems = validate_labels(payload, labels)
    assert any("different pool" in p for p in problems)
    assert any("not named" in p for p in problems)
    assert any("not judged" in p for p in problems)


def test_self_consistency_counts_agreement_on_the_hidden_duplicates() -> None:
    payload, key = pool(duplicates=2)
    labels = labels_for(payload)
    duplicate = next(c for c, v in key["queries"].items() if v["duplicate"])  # type: ignore[union-attr]
    flipped = next(iter(labels["labels"][duplicate]))  # type: ignore[index]
    labels["labels"][duplicate][flipped]["judgment"] = "not_relevant"  # type: ignore[index]
    result = self_consistency(resolve(key, labels))
    assert result.pairs > 0
    assert result.agreed == result.pairs - 1


def test_precision_counts_unjudged_items_as_not_relevant_and_says_how_many() -> None:
    judgments = {"a": "relevant", "b": "partial", "c": "not_relevant"}
    assert precision_at_10(["a", "b", "c", "x"], judgments) == (0.1, 1)
    assert precision_at_10(["a", "b", "c", "x"], judgments, partial_counts=True) == (0.2, 1)


def test_the_task_is_written_once_and_frozen_labels_are_never_overwritten(tmp_path: Path) -> None:
    summary = write_task(tmp_path, QUERIES, ITEMS, FakeEncoder(), seed=3, depth=4, duplicates=2)
    assert summary["sittings"] == len(QUERIES) + 2
    assert summary["encoder"] == {"model": "fake", "version": "1"}  # the pool depends on it
    assert (tmp_path / "label.html").exists() and (tmp_path / "pool_key.json").exists()
    with pytest.raises(FileExistsError, match="pool is fixed"):
        write_task(tmp_path, QUERIES, ITEMS, FakeEncoder(), seed=3)

    payload = json.loads((tmp_path / "pool_payload.json").read_text(encoding="utf-8"))
    export = tmp_path / "export.json"
    incomplete = labels_for(payload)
    incomplete["labels"] = {}
    export.write_text(json.dumps(incomplete), encoding="utf-8")
    with pytest.raises(NotFreezable, match="not judged"):
        freeze(export, tmp_path)

    export.write_text(json.dumps(labels_for(payload)), encoding="utf-8")
    record = freeze(export, tmp_path)
    assert record["labeller"] == "Kundan"
    assert record["labels_sha256"] == hashlib.sha256(export.read_bytes()).hexdigest()
    assert record["self_consistency"]["candidates_judged_twice"] > 0  # type: ignore[index]
    with pytest.raises(NotFreezable, match="never overwritten"):
        freeze(export, tmp_path)


def test_a_held_key_or_chord_cannot_record_a_judgment() -> None:
    # Keys 1/2/3 judge and advance in one action, so auto-repeat would label the rest of a sitting.
    from scripts.build_relevance_pool import TEMPLATE

    template = TEMPLATE.read_text(encoding="utf-8")
    guard = next(line for line in template.splitlines() if "e.repeat" in line)
    assert "return" in guard and all(k in guard for k in ("ctrlKey", "altKey", "metaKey"))
    assert template.index("e.repeat") < template.index('["1", "2", "3"].includes(e.key)')
