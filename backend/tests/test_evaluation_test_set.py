"""The test set as the planner reads it: 30 requests, a category per product, frozen.

A category is the one product term the query planner may use, so a changed category changes every
query built from it. The hash lock makes that change deliberate; the other tests keep a brand or
product name out of the categories (the names are pseudonyms no search engine can resolve, V7).
"""

import re

import yaml

from app.evaluation.test_set import (
    CATEGORY_LOCK,
    TEST_SET,
    categories,
    categories_hash,
    plan_requests,
)


def test_every_product_has_a_category_and_every_request_a_plan_request():
    assert len(categories()) == 20
    reqs = plan_requests()
    assert list(reqs) == [f"R{i:02d}" for i in range(1, 31)]
    assert sum(1 for r in reqs.values() if r.regulated) == 3  # B07's three requests


def test_the_categories_are_frozen_by_their_lock():
    locked = CATEGORY_LOCK.read_text(encoding="utf-8").split()[0]
    assert categories_hash() == locked, (
        "a category changed: every query built from it changes. If deliberate, update "
        "evaluation/test_set.categories.lock and record the change and its date"
    )


def test_no_category_contains_a_brand_or_product_name():
    """No full name, and no name's first word (its distinctive token), appears in a category. Later
    words may be generic ("Shaving" in a brand's name is also what the product is)."""
    data = yaml.safe_load(TEST_SET.read_text(encoding="utf-8"))
    for brand in data["brands"]:
        for key, product in brand["products"].items():
            category = product["category"].casefold()
            category_words = set(re.findall(r"[a-z0-9']+", category))
            names = [brand["pseudonym"], brand["real_brand"]]
            names += [product["pseudonym"], product["real_name"]]
            for name in names:
                first = re.findall(r"[a-z0-9']+", name.casefold())[0]
                assert name.casefold() not in category, f"{brand['id']}.{key}: {name!r} in category"
                assert first in {"the"} or first not in category_words, (
                    f"{brand['id']}.{key}: {product['category']!r} reuses {first!r}"
                )


def test_the_regulated_request_is_the_whey_protein_not_a_plant_protein():
    # R19 launches a whey isolate + concentrate blend; a different category would return a different
    # evidence base (owner, 2026-10-28)
    assert plan_requests()["R19"].category == "whey protein powder"
    assert plan_requests()["R19"].regulated is True


def test_novel_and_repeat_tags_follow_the_shared_research_evidence():
    from collections import Counter

    from app.evaluation.test_set import evidence_tags

    tags = evidence_tags()
    assert Counter(t["kind"] for t in tags.values()) == {"novel": 19, "repeat": 11}
    assert Counter(t["stage"] for t in tags.values()) == {
        "cold_start": 10,
        "warm_novel": 9,
        "repeat": 11,
    }
    # every position-1 request is novel (and cold) by construction
    assert all(
        tags[f"R{i:02d}"]["stage"] == "cold_start" for i in (1, 4, 7, 10, 13, 16, 19, 22, 25, 28)
    )
    # B06's hazelnut flavour (product B) is the one repeat on a DIFFERENT product
    different = [r for r, t in tags.items() if t["subtype"] == "different_product"]
    assert different == ["R17"]
    assert tags["R18"]["subtype"] == "same_product" and tags["R03"]["subtype"] == "same_product"


def test_identical_queries_collapse_to_one_cache_entry_so_153_searches_are_recorded():
    from app.memory.scoring import load_scoring
    from app.research.planner import plan_queries
    from app.research.search_cache import cache_key

    cfg = load_scoring().research
    declared = tuple(sorted(load_scoring().research.declared_engines or ()))
    assert declared == ("duckduckgo web", "yahoo", "yandex", "yep")  # the declared set
    slots = 0
    seen: set[str] = set()
    for req in plan_requests().values():
        plan = plan_queries(
            req,
            queries_per_facet=cfg.planner_queries_per_facet,
            locale=cfg.planner_locale,
            max_pages_per_query=None,
        )
        for q in plan.queries:
            slots += 1
            seen.add(cache_key(q.text, declared))
            seen.add(cache_key("  " + q.text.upper() + " ", declared))  # case/space do not matter
    assert slots == 240 and len(seen) == 153
    assert cache_key("a b", declared) != cache_key("a b", ("yep",))  # the engine set still matters
