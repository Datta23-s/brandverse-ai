"""The echo measure (app/evaluation/echo.py): its definitions, on cases checkable by hand."""

import pytest

from app.evaluation.echo import (
    bigram_overlap,
    content_terms,
    coverage,
    echo_for_query,
    paired_difference,
    summarise,
)
from app.evaluation.relevance import Item, Query


def test_content_terms_drop_frame_words_and_a_plural_s() -> None:
    assert content_terms("What do we say to a couple setting up their first kitchen") == [
        "couple",
        "setting",
        "up",
        "their",
        "first",
        "kitchen",
    ]
    assert content_terms("Pans and glass") == ["pan", "glass"]  # 'glass' keeps its 'ss'


def test_coverage_is_the_share_of_query_terms_the_document_has() -> None:
    q = content_terms("non-stick pans cost Cookhaus")  # non, stick, pan, cost, cookhau
    assert coverage(q, content_terms("a non-stick pan")) == pytest.approx(3 / 5)
    assert coverage([], ["x"]) == 0.0


def test_bigram_overlap_needs_the_phrase_not_just_the_words() -> None:
    q = content_terms("first kitchen starter set")
    assert bigram_overlap(q, content_terms("the first kitchen starter set")) == 1.0
    assert bigram_overlap(q, content_terms("kitchen first, set starter")) == 0.0


def test_echo_for_query_over_a_pool_and_the_whole_corpus() -> None:
    items = {
        "a": Item("a", "The first kitchen starter set suits couples"),
        "b": Item("b", "Cast iron needs a thin coat of oil"),
        "c": Item("c", "Starter kitchen"),
    }
    out = echo_for_query(Query("q", "first kitchen starter set"), items, ["a", "b"])
    assert out["corpus_best"] == 1.0  # item a has every content term
    assert out["pool_coverage"] == pytest.approx((1.0 + 0.0) / 2)  # a: all four; b: none
    assert out["pool_bigram"] == pytest.approx((1.0 + 0.0) / 2)


def test_summarise_and_the_paired_bootstrap() -> None:
    first = {q: {"pool_coverage": 0.6, "pool_bigram": 0.2, "corpus_best": 1.0} for q in "abcd"}
    second = {q: {"pool_coverage": 0.4, "pool_bigram": 0.1, "corpus_best": 0.8} for q in "abcd"}
    assert summarise(first, list("abcd"))["pool_coverage"] == pytest.approx(0.6)
    d = paired_difference(first, second, list("abcd"), "pool_coverage", resamples=200, seed=1)
    assert d == pytest.approx({"mean": 0.2, "low": 0.2, "high": 0.2})  # no spread: a point
