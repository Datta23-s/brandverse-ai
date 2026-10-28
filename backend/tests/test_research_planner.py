"""The query planner: one ranked list from the request alone (app/research/planner.py).

What must hold because the evaluation rests on it: the list is a pure function of the request (no
arm, memory or earlier result can change it); depth is a PREFIX of it (D0 none, D1 the first pass,
D2 all);
the first pass covers every facet and any shorter prefix leaves one uncovered; no brand or product
name can reach a query; the fetch cap is reported and still unset.
"""

import pytest

from app.memory.scoring import load_scoring
from app.research.planner import (
    CONSUMER,
    PRIORITY,
    TEMPLATES,
    PlanRequest,
    clean,
    plan_queries,
)
from app.research.search import normalise_query

CFG = load_scoring().research


def request(**kw: object) -> PlanRequest:
    base = {
        "sector": "consumer electronics",
        "regulated": False,
        "category": "neckband earphones",
        "audience": "College students who commute and work out",
    }
    return PlanRequest(**{**base, **kw})


def plan(req: PlanRequest, per_facet: int = 2):
    return plan_queries(req, queries_per_facet=per_facet, locale="India", max_pages_per_query=None)


def test_four_facets_two_each_is_eight_queries_in_two_passes():
    p = plan(request())
    assert len(p.queries) == 8 and p.first_pass_size == 4
    assert [q.angle for q in p.queries] == [1, 1, 1, 1, 2, 2, 2, 2]
    assert [q.rank for q in p.queries] == list(range(1, 9))
    assert len({normalise_query(q.text) for q in p.queries}) == 8


def test_the_shipped_configuration_gives_q_of_eight_and_leaves_the_fetch_cap_unset():
    assert CFG.planner_queries_per_facet == 2
    assert CFG.max_pages_per_query is None  # open until the fetch-cost distribution is known


def test_depth_is_a_prefix_of_the_one_list():
    p = plan(request())
    assert p.for_depth("D0") == ()
    assert p.for_depth("D1") == p.queries[:4]
    assert p.for_depth("D2") == p.queries
    assert p.for_depth("D2")[: len(p.for_depth("D1"))] == p.for_depth("D1")


def test_the_first_pass_covers_every_facet_and_any_shorter_prefix_does_not():
    p = plan(request())
    assert p.uncovered(4) == frozenset()
    for k in (1, 2, 3):
        assert p.uncovered(k), f"a prefix of {k} cannot cover four facets"
    assert p.uncovered(0) == frozenset(p.facets)


def test_a_regulated_sector_leads_with_sector_rules_and_others_with_competitors():
    assert plan(request()).facets[0] == "competitor_positioning"
    reg = plan(request(sector="health supplements", regulated=True, category="whey protein"))
    assert reg.facets[0] == "sector_rules" and reg.queries[0].facet == "sector_rules"
    assert "labelling and health claims regulation" in reg.queries[0].text


def test_the_tail_is_what_a_cut_would_remove_the_second_query_of_the_lowest_priority_facet():
    p = plan(request())
    assert p.queries[-1].facet == p.facets[-1] and p.queries[-1].angle == 2
    assert p.uncovered(len(p.queries) - 1) == frozenset()  # a cut of the tail loses no facet


def test_the_plan_is_a_pure_function_of_the_request():
    a, b = plan(request()), plan(request())
    assert a == b
    assert [q.text for q in a.queries] == [q.text for q in b.queries]


def test_the_objective_is_carried_but_never_changes_a_query():
    a = plan(request(objective="Launch awareness for the neckband"))
    b = plan(request(objective="Festive-season gifting push"))
    assert [q.text for q in a.queries] == [q.text for q in b.queries]


def test_no_audience_uses_audience_free_wording_and_no_empty_slot_leaks():
    p = plan(request(audience=None))
    assert all("  " not in q.text and "{" not in q.text for q in p.queries)
    audience_q = [q for q in p.queries if q.facet == "audience_needs"][0]
    assert audience_q.text == "what buyers look for when buying neckband earphones India"


def test_one_query_per_facet_makes_depth_irrelevant_which_is_why_the_default_is_two():
    p = plan(request(), per_facet=1)
    assert p.for_depth("D1") == p.for_depth("D2") and len(p.queries) == 4


def test_a_third_query_per_facet_is_refused_because_no_third_template_exists():
    with pytest.raises(ValueError):
        plan(request(), per_facet=3)


def test_free_text_cannot_inject_search_operators():
    p = plan(request(category='"tea" (green) | herbal', audience='site:evil.example "x"'))
    for q in p.queries:
        assert not any(ch in q.text for ch in '"()|[]{}<>')
    assert clean('a "b" (c)|d') == "a b c d"


def test_the_request_cannot_carry_a_brand_or_product_name():
    with pytest.raises(ValueError):
        PlanRequest(sector="x", regulated=False, category="y", brand="Tidewell Audio")  # type: ignore[call-arg]


def test_a_blank_category_or_sector_is_refused():
    with pytest.raises(ValueError):
        plan(request(category='"" ()'))
    with pytest.raises(ValueError):
        PlanRequest(sector="", regulated=False, category="x")


def test_every_facet_has_a_named_consumer_and_two_templates_and_a_priority_slot():
    for order in PRIORITY.values():
        assert set(order) == set(CONSUMER) == set(TEMPLATES)
    assert all(len(t) == 2 for t in TEMPLATES.values())


def test_the_report_states_what_is_a_parameter_of_the_results():
    r = plan(request()).report()
    assert r["generation"] == "fixed templates; not adaptive"
    assert r["queries"] == 8 and r["d1_queries"] == 4
    assert r["queries_per_facet"] == 2 and r["max_pages_per_query"] is None
    assert r["template_version"] == "v1"
