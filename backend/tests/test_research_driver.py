"""The research driver: planned queries in, scored attributed findings out, partial on failure.

What must hold because the evaluation rests on it: depth selects a prefix of the one list (D0
searches nothing); a URL returned by several queries is fetched once; the per-query cap is honoured;
a throttled search stops the rest instead of hammering the engine; every failure is counted by
reason and none crashes the run; low-scored pages never reach an agent; the counts that SRR and LR
are computed from are exactly the requests made; no raw body leaves the driver; and it runs against
any search provider and page source, which is what lets the same code be recorded live and
replayed.
"""

import asyncio
from datetime import date

import pytest

from app.errors import (
    FetchFailed,
    FetchRefused,
    FetchUnsupported,
    SearchRateLimited,
    SearchUnavailable,
)
from app.memory.scoring import load_scoring
from app.research.driver import Mode, RequestAborted, ResearchOutcome, canonical_url, research
from app.research.extraction import Extractor
from app.research.fetch import FetchedPage
from app.research.planner import PlanRequest, QueryPlan, plan_queries
from app.research.search import EngineFailure, OfflineSearch, SearchResponse, SearchResult

pytestmark = pytest.mark.anyio
CFG = load_scoring()
TODAY = date(2026, 10, 28)
REQ = PlanRequest(
    sector="consumer electronics",
    regulated=False,
    category="neckband earphones",
    audience="College students",
)


def plan(cap: int | None = None) -> QueryPlan:
    return plan_queries(REQ, queries_per_facet=2, locale="India", max_pages_per_query=cap)


def result(url: str, **kw: object) -> SearchResult:
    return SearchResult(url=url, title="t", snippet="s", engine="yep", **kw)  # type: ignore[arg-type]


def page(url: str, body: str) -> FetchedPage:
    return FetchedPage(url, url, 200, "text/html", body, 1.0, 0)


def raw_extract(html: str, url: str) -> dict[str, object] | None:
    # the "html" in these tests IS the main text: enough of it extracts, a stub of it does not
    title = "Neckband earphones guide" if "neckband" in html else "Unrelated"
    return {"title": title, "text": html, "date": None}


EXTRACTOR = Extractor(raw_extract, min_chars=20, max_chars=500)
GOOD = "neckband earphones buying guide with battery life and sound quality compared " * 3


class Pages:
    """A page source that serves from a dict, fails what it is told to, records every request."""

    def __init__(self, bodies: dict[str, str], failing: dict[str, Exception] | None = None) -> None:
        self.bodies, self.failing, self.requested = bodies, failing or {}, []
        self.in_flight = self.peak = 0

    async def fetch(self, url: str) -> FetchedPage:
        self.requested.append(url)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0)
            if url in self.failing:
                raise self.failing[url]
            return page(url, self.bodies.get(url, GOOD))
        finally:
            self.in_flight -= 1


class Searches:
    """Answers each planned query with the URLs given for it, or raises what it is told to."""

    def __init__(self, by_rank: dict[int, list[str]], raise_on: dict[int, Exception] | None = None):
        self.by_rank, self.raise_on, self.asked = by_rank, raise_on or {}, []
        self.texts = {q.text: q.rank for q in plan().queries}

    async def search(self, query: str, *, limit: int = 10) -> SearchResponse:
        rank = self.texts[query]
        self.asked.append(rank)
        if rank in self.raise_on:
            raise self.raise_on[rank]
        return SearchResponse([result(u) for u in self.by_rank.get(rank, [])][:limit])


async def run(
    searches: object,
    pages: object,
    depth: str = "D2",
    cap: int | None = None,
    concurrency: int = 8,
    mode: Mode = Mode.PRODUCTION,
) -> ResearchOutcome:
    return await research(
        plan(cap),
        depth,  # type: ignore[arg-type]
        mode=mode,
        search=searches,  # type: ignore[arg-type]
        pages=pages,  # type: ignore[arg-type]
        extractor=EXTRACTOR,
        source_cfg=CFG.source,
        today=TODAY,
        concurrency=concurrency,
    )


URLS = {r: [f"https://site{r}.example/a", f"https://site{r}.example/b"] for r in range(1, 9)}


async def test_d0_searches_nothing_d1_the_first_pass_d2_all_of_it():
    for depth, asked in (("D0", []), ("D1", [1, 2, 3, 4]), ("D2", [1, 2, 3, 4, 5, 6, 7, 8])):
        s, p = Searches(URLS), Pages({})
        out = await run(s, p, depth)
        assert s.asked == asked and out.searches_issued == len(asked)
        assert len(p.requested) == out.pages_fetched == 2 * len(asked)


async def test_a_url_returned_by_several_queries_is_fetched_once_and_credits_them_all():
    shared = "https://shared.example/guide"
    s = Searches({1: [shared, "https://one.example/x"], 5: [shared]})
    p = Pages({})
    out = await run(s, p)
    assert p.requested.count(shared) == 1 and out.distinct_urls == 2 and out.results_seen == 3
    finding = next(f for f in out.findings if f.page.url == shared)
    assert finding.queries == (1, 5) and finding.engines == ("yep",)


async def test_canonical_url_ignores_scheme_www_fragment_and_trailing_slash_not_the_query():
    a = canonical_url("http://www.Example.com/p/#top")
    assert a == canonical_url("https://example.com/p") == "example.com/p"
    assert canonical_url("https://example.com/p?id=1") != canonical_url(
        "https://example.com/p?id=2"
    )


async def test_the_per_query_cap_bounds_what_is_considered_and_fetched():
    s = Searches({1: [f"https://h{i}.example/p" for i in range(10)]})
    p = Pages({})
    out = await run(s, p, cap=3)
    assert out.queries[0].results == 10 and out.queries[0].considered == 3
    assert len(p.requested) == 3 == out.pages_fetched
    uncapped = await run(Searches({1: [f"https://h{i}.example/p" for i in range(10)]}), Pages({}))
    assert uncapped.pages_fetched == 10


async def test_a_throttled_search_stops_the_remaining_queries_and_the_run_is_partial():
    s = Searches(URLS, raise_on={3: SearchRateLimited("brave: too many requests")})
    out = await run(s, Pages({}))
    assert s.asked == [1, 2, 3]  # nothing asked after the refusal
    assert [q.outcome for q in out.queries] == ["ok", "ok", "rate_limited"] + ["not_attempted"] * 5
    assert out.partial and out.searches_issued == 3
    assert len(out.findings) == 4  # what the two good queries found is kept


async def test_an_unavailable_search_is_recorded_and_the_next_query_still_runs():
    s = Searches(URLS, raise_on={2: SearchUnavailable("down")})
    out = await run(s, Pages({}), "D1")
    assert s.asked == [1, 2, 3, 4]
    assert [q.outcome for q in out.queries] == ["ok", "unavailable", "ok", "ok"] and out.partial


async def test_page_failures_are_counted_by_reason_and_never_crash_the_run():
    s = Searches(
        {
            1: [
                "https://a.example/1",
                "https://b.example/2",
                "https://c.example/3",
                "https://d.example/4",
            ]
        }
    )
    p = Pages(
        {},
        failing={
            "https://a.example/1": FetchFailed("404", status=404, kind="http_status"),
            "https://b.example/2": FetchRefused("robots", kind="robots"),
            "https://c.example/3": FetchUnsupported("pdf", kind="pdf"),
        },
    )
    out = await run(s, p)
    assert out.pages_fetched == 4 and out.pages_served == 1 and len(out.findings) == 1
    assert out.failures_by_kind() == {
        "fetch:FetchFailed/http_status": 1,
        "fetch:FetchRefused/robots": 1,
        "fetch:FetchUnsupported/pdf": 1,
    }


async def test_a_page_with_too_little_main_text_is_counted_as_refused_not_as_a_failure_to_fetch():
    s = Searches({1: ["https://thin.example/p", "https://ok.example/p"]})
    out = await run(s, Pages({"https://thin.example/p": "login"}))
    assert out.pages_served == 2 and out.pages_extracted == 1 and out.extraction_refused == 1
    assert [f.page.url for f in out.findings] == ["https://ok.example/p"]


async def test_a_page_below_the_acceptance_threshold_is_discarded_before_it_reaches_an_agent():
    off_topic = "zzzz qqqq xxxx yyyy wwww vvvv uuuu tttt ssss rrrr " * 2
    s = Searches({1: ["https://good.example/p", "https://junk.example/p"]})
    out = await run(s, Pages({"https://junk.example/p": off_topic}))
    assert out.discarded_low_score == 1 and out.pages_extracted == 2
    assert all(f.score.accepted for f in out.findings)
    assert [f.page.url for f in out.findings] == ["https://good.example/p"]


async def test_the_counts_the_metrics_use_are_exactly_the_requests_made():
    s, p = Searches(URLS), Pages({})
    out = await run(s, p, "D1")
    assert out.searches_issued == len(s.asked) == 4
    assert out.pages_fetched == len(p.requested) == 8
    d2 = await run(Searches(URLS), Pages({}), "D2")
    assert (d2.searches_issued, d2.pages_fetched) == (8, 16)  # the SRR / LR inputs differ by depth


async def test_the_outcome_holds_extracted_text_and_never_a_raw_body():
    out = await run(Searches({1: ["https://a.example/p"]}), Pages({}))
    finding = out.findings[0]
    assert finding.page.text and not hasattr(finding.page, "html")
    assert not any(isinstance(v, str) and "<html" in v for v in vars(finding.page).values())


async def test_findings_are_ordered_best_score_first_and_deterministically():
    s = Searches({1: [f"https://h{i}.example/p" for i in range(6)]})
    a = await run(s, Pages({}))
    b = await run(Searches({1: [f"https://h{i}.example/p" for i in range(6)]}), Pages({}))
    assert [f.page.url for f in a.findings] == [f.page.url for f in b.findings]
    values = [f.score.value for f in a.findings]
    assert values == sorted(values, reverse=True)


async def test_concurrency_is_bounded():
    s = Searches({1: [f"https://h{i}.example/p" for i in range(20)]})
    p = Pages({})
    await run(s, p, concurrency=3)
    assert 1 < p.peak <= 3
    with pytest.raises(ValueError):
        await run(s, Pages({}), concurrency=0)


async def test_first_party_is_passed_through_unpopulated_and_never_read():
    out = await run(Searches({1: ["https://a.example/p"]}), Pages({}))
    assert out.findings[0].first_party is None


async def test_it_runs_unchanged_against_the_offline_provider_which_is_what_replay_relies_on():
    first = plan().queries[0].text
    offline = OfflineSearch({first: [result("https://a.example/p")]})
    out = await run(offline, Pages({}), "D1")
    assert out.searches_issued == 4 and len(out.findings) == 1
    assert out.plan_report["generation"] == "fixed templates; not adaptive"


# ── The two modes ────────────────────────────────────────────────────────────────────────────


async def test_the_mode_is_required_and_must_be_a_mode_not_a_flag():
    kwargs = dict(
        search=Searches(URLS),
        pages=Pages({}),
        extractor=EXTRACTOR,
        source_cfg=CFG.source,
        today=TODAY,
    )
    with pytest.raises(TypeError):  # no default
        await research(plan(), "D2", **kwargs)  # type: ignore[call-arg, arg-type]
    for wrong in (True, "record", None):
        with pytest.raises(TypeError, match="must be a Mode"):
            await research(plan(), "D2", mode=wrong, **kwargs)  # type: ignore[arg-type]


async def test_record_mode_aborts_the_request_at_once_when_a_search_is_throttled():
    s = Searches(URLS, raise_on={3: SearchRateLimited("brave: too many requests")})
    p = Pages({})
    with pytest.raises(RequestAborted) as caught:
        await run(s, p, mode=Mode.RECORD)
    assert s.asked == [1, 2, 3]  # nothing asked after the refusal
    assert p.requested == []  # and no page fetched for a request that will be discarded
    assert caught.value.outcome.queries[-1].outcome == "rate_limited"


async def test_record_mode_aborts_on_an_unavailable_search_too():
    s = Searches(URLS, raise_on={2: SearchUnavailable("down")})
    p = Pages({})
    with pytest.raises(RequestAborted):
        await run(s, p, mode=Mode.RECORD)
    assert s.asked == [1, 2] and p.requested == []


async def test_record_mode_aborts_on_a_degraded_search_because_it_is_never_recorded():
    class Degraded(Searches):
        async def search(self, query: str, *, limit: int = 10) -> SearchResponse:
            response = await super().search(query, limit=limit)
            if self.texts[query] == 2:
                return SearchResponse(
                    response.results, failed_engines=(EngineFailure("yep", "HTTP error"),)
                )
            return response

    s, p = Degraded(URLS), Pages({})
    with pytest.raises(RequestAborted, match="degraded"):
        await run(s, p, mode=Mode.RECORD)
    assert s.asked == [1, 2] and p.requested == []


async def test_record_mode_on_a_clean_request_returns_the_whole_outcome():
    out = await run(Searches(URLS), Pages({}), mode=Mode.RECORD)
    assert not out.partial and out.searches_issued == 8 and out.pages_fetched == 16


async def test_production_mode_degrades_to_partial_and_declares_the_gap():
    # rank 3 is the reference-facts primary query; the throttle takes that facet and the next
    s = Searches(URLS, raise_on={3: SearchRateLimited("too many requests")})
    out = await run(s, Pages({}), mode=Mode.PRODUCTION)
    assert out.partial and len(out.findings) == 4  # not nothing: facets 1 and 2 still produced
    assert out.uncovered_facets == ("reference_facts", "sector_rules")  # the declared gap
    clean = await run(Searches(URLS), Pages({}), mode=Mode.PRODUCTION)
    assert clean.uncovered_facets == ()


async def test_production_mode_carries_on_past_an_unavailable_search_and_covers_the_facet_later():
    # facet 2's primary (rank 2) is down but its second query (rank 6) answers: the facet is covered
    s = Searches(URLS, raise_on={2: SearchUnavailable("down")})
    out = await run(s, Pages({}), mode=Mode.PRODUCTION)
    assert out.partial and out.uncovered_facets == ()
