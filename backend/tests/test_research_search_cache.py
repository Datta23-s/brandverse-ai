"""The search-result cache (app/research/search_cache.py; Rules.md C7).

The TTL is a parameter: it is read from config/scoring.yaml, passed in, and recorded by every run
with the age of each entry reused. A fake key-value store and a hand-set clock make every age
exact; one test at the end runs against a real Redis.
"""

import json

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.errors import SearchUnavailable
from app.memory.scoring import Research, load_scoring
from app.research.search import EngineFailure, OfflineSearch, SearchResponse, SearchResult
from app.research.search_cache import CachedSearch, cache_key

pytestmark = pytest.mark.anyio

QUERY = "kitchen starter set reviews"
RESULTS = [
    SearchResult(f"https://example.test/{n}", f"Result {n}", f"Snippet {n}") for n in range(12)
]
TTL = 3600
DEGRADED_TTL = 600


class FakeKV:
    """Redis' get/set(ex=) in a dict. `broken` makes every command fail as a dead server would."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.expiries: dict[str, int] = {}
        self.commands = 0
        self.broken = False

    async def get(self, name: str, /) -> str | None:
        self.commands += 1
        if self.broken:
            raise RedisConnectionError("down")
        return self.data.get(name)

    async def set(self, name: str, value: str, /, *, ex: int) -> bool:
        self.commands += 1
        if self.broken:
            raise RedisConnectionError("down")
        self.data[name], self.expiries[name] = value, ex
        return True


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def cached(
    kv: FakeKV | None = None,
    clock: Clock | None = None,
    *,
    ttl_s: int = TTL,
    degraded_ttl_s: int | None = None,
    mode: str = "use",
    inner: OfflineSearch | None = None,
    declared: tuple[str, ...] | None = None,  # must equal what `inner` was built with
) -> tuple[CachedSearch, FakeKV, Clock, OfflineSearch]:
    kv, clock = kv or FakeKV(), clock or Clock()
    inner = inner or OfflineSearch({QUERY: RESULTS})
    short = degraded_ttl_s or min(DEGRADED_TTL, ttl_s)
    search = CachedSearch(
        inner,
        kv,
        ttl_s=ttl_s,
        degraded_ttl_s=short,
        declared_engines=declared,
        mode=mode,
        clock=clock,  # type: ignore[arg-type]
    )
    return search, kv, clock, inner


# ── The TTL is a parameter ───────────────────────────────────────────────────────────────────


def test_the_ttl_comes_from_scoring_config_and_the_cache_has_no_default_for_it() -> None:
    research = load_scoring().research
    assert 1 <= research.search_cache_degraded_ttl_s <= research.search_cache_ttl_s
    with pytest.raises(TypeError, match="ttl_s"):
        CachedSearch(OfflineSearch(), FakeKV())  # type: ignore[call-arg, arg-type]
    with pytest.raises(TypeError, match="degraded_ttl_s"):  # neither TTL has a default
        CachedSearch(OfflineSearch(), FakeKV(), ttl_s=60)  # type: ignore[call-arg, arg-type]
    with pytest.raises(ValueError, match="ttl_s"):
        CachedSearch(OfflineSearch(), FakeKV(), ttl_s=0, degraded_ttl_s=0, declared_engines=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must not exceed"):
        CachedSearch(OfflineSearch(), FakeKV(), ttl_s=60, degraded_ttl_s=61, declared_engines=None)  # type: ignore[arg-type]


async def test_the_entry_expires_in_redis_after_exactly_the_ttl_in_force() -> None:
    search, kv, _, _ = cached(ttl_s=900)
    await search.search(QUERY)
    assert kv.expiries == {cache_key(QUERY): 900}


async def test_an_entry_older_than_the_ttl_in_force_is_a_miss_even_if_redis_still_holds_it() -> (
    None
):
    kv, clock = FakeKV(), Clock()
    first, *_ = cached(kv, clock, ttl_s=7200)
    await first.search(QUERY)
    clock.now += 3000  # inside 7200 s, outside the 1800 s a later run is configured with
    stricter, _, _, inner = cached(kv, clock, ttl_s=1800)
    await stricter.search(QUERY)
    assert len(inner.calls) == 1  # the new run went to the engine
    assert stricter.report().reused == 0
    lenient, _, _, other = cached(kv, clock, ttl_s=7200)
    await lenient.search(QUERY)
    assert other.calls == []  # the entry the strict run just rewrote is fresh under either TTL


async def test_the_age_boundary_is_inclusive_and_a_future_dated_entry_is_not_trusted() -> None:
    kv, clock = FakeKV(), Clock()
    search, *_ = cached(kv, clock)
    await search.search(QUERY)
    clock.now += TTL
    later, _, _, inner = cached(kv, clock)
    await later.search(QUERY)
    assert inner.calls == [] and later.report().oldest_reused_age_s == TTL
    clock.now -= TTL + 60  # the clock stepped back past the write
    skewed, _, _, inner = cached(kv, clock)
    await skewed.search(QUERY)
    assert len(inner.calls) == 1


# ── Hits, misses and the ledger ──────────────────────────────────────────────────────────────


async def test_a_second_search_is_reused_with_its_age_recorded() -> None:
    search, kv, clock, inner = cached()
    assert (await search.search(QUERY, limit=5)).results == RESULTS[:5]
    clock.now += 125.5
    assert (await search.search(QUERY, limit=5)).results == RESULTS[:5]
    assert len(inner.calls) == 1
    first, second = search.report().records
    assert (first.outcome, first.age_s) == ("fetched", None)
    assert (second.outcome, second.age_s, second.results) == ("reused", 125.5, 5)


async def test_the_report_states_the_ttl_and_how_much_evidence_was_fetched_versus_reused() -> None:
    search, _, clock, _ = cached(inner=OfflineSearch({QUERY: RESULTS, "other": RESULTS[:2]}))
    await search.search(QUERY, limit=4)  # fetched, 4 results
    await search.search("other", limit=4)  # fetched, 2 results
    clock.now += 10
    await search.search(QUERY, limit=4)  # reused, 4
    clock.now += 20
    await search.search("OTHER", limit=4)  # reused, 2
    await search.search(QUERY, limit=4)  # reused, 4 (30 s old)
    report = search.report()
    assert (report.mode, report.ttl_s) == ("use", TTL)
    assert (report.searches, report.fetched, report.reused, report.failed) == (5, 2, 3, 0)
    assert (report.results_fetched, report.results_reused) == (6, 10)
    assert report.reused_share_of_searches == pytest.approx(3 / 5)
    assert report.reused_share_of_results == pytest.approx(10 / 16)
    assert report.oldest_reused_age_s == 30
    assert json.loads(report.model_dump_json())["ttl_s"] == TTL  # ready to attach to a run record


async def test_an_empty_ledger_reports_no_shares_rather_than_dividing_by_zero() -> None:
    search, *_ = cached()
    report = search.report()
    assert (report.searches, report.reused_share_of_searches) == (0, None)
    assert report.reused_share_of_results is None and report.oldest_reused_age_s is None


async def test_queries_differing_only_in_case_and_spacing_share_one_entry() -> None:
    search, kv, _, inner = cached()
    await search.search(QUERY)
    await search.search("  KITCHEN   starter set REVIEWS ")
    assert len(inner.calls) == 1 and len(kv.data) == 1


async def test_an_entry_fetched_with_a_smaller_limit_does_not_answer_a_larger_one() -> None:
    search, _, _, inner = cached()
    await search.search(QUERY, limit=3)
    assert (await search.search(QUERY, limit=10)).results == RESULTS[
        :10
    ]  # refetched with the larger limit
    assert (await search.search(QUERY, limit=4)).results == RESULTS[
        :4
    ]  # now answered from the entry
    assert [limit for _, limit in inner.calls] == [3, 10]


async def test_blank_queries_and_bad_limits_are_refused_before_any_cache_traffic() -> None:
    search, kv, *_ = cached()
    with pytest.raises(ValueError, match="blank"):
        await search.search("  ")
    with pytest.raises(ValueError, match="limit"):
        await search.search(QUERY, limit=0)
    assert kv.commands == 0 and search.report().searches == 0


# ── Failure ──────────────────────────────────────────────────────────────────────────────────


async def test_an_engine_failure_is_recorded_not_cached_and_propagates() -> None:
    kv, clock = FakeKV(), Clock()
    failing, *_ = cached(kv, clock, inner=OfflineSearch(fail_with=SearchUnavailable("down")))
    with pytest.raises(SearchUnavailable):
        await failing.search(QUERY)
    assert kv.data == {}
    assert [r.outcome for r in failing.report().records] == ["failed"]
    assert failing.report().failed == 1


async def test_a_dead_redis_degrades_to_fetching_and_is_counted() -> None:
    kv = FakeKV()
    kv.broken = True
    search, _, _, inner = cached(kv)
    assert (await search.search(QUERY, limit=2)).results == RESULTS[:2]
    assert (await search.search(QUERY, limit=2)).results == RESULTS[:2]
    assert len(inner.calls) == 2  # nothing could be reused
    report = search.report()
    assert (report.fetched, report.reused, report.cache_errors) == (2, 0, 2)


async def test_a_corrupt_entry_is_a_miss_and_is_replaced() -> None:
    kv = FakeKV()
    kv.data[cache_key(QUERY)] = "{not json"
    search, _, _, inner = cached(kv)
    await search.search(QUERY, limit=2)
    assert len(inner.calls) == 1
    kv.data[cache_key(QUERY)] = json.dumps({"fetched_at": 1, "limit": 2, "results": [{"x": 1}]})
    await search.search(QUERY, limit=2)  # parses as JSON, not as results: a miss again
    assert len(inner.calls) == 2
    await search.search(QUERY, limit=2)
    assert len(inner.calls) == 2  # the replacement is good


# ── Bypass, and what is stored ───────────────────────────────────────────────────────────────


async def test_bypass_never_touches_the_cache_and_still_records_every_fetch() -> None:
    kv = FakeKV()
    kv.data[cache_key(QUERY)] = "would be reused if read"
    search, _, _, inner = cached(kv, mode="bypass")
    await search.search(QUERY, limit=2)
    await search.search(QUERY, limit=2)
    assert kv.commands == 0 and len(inner.calls) == 2
    report = search.report()
    assert (report.mode, report.ttl_s, report.fetched, report.reused) == ("bypass", TTL, 2, 0)
    assert report.reused_share_of_searches == 0.0


async def test_only_search_results_are_stored_never_a_page_body() -> None:
    search, kv, _, _ = cached()
    await search.search(QUERY, limit=2)
    (stored,) = kv.data.values()
    entry = json.loads(stored)
    assert set(entry) == {
        "query",
        "fetched_at",
        "limit",
        "results",
        "failed_engines",
        "undeclared_failures",
        "declared_engines",
    }
    assert all(
        set(r) == {"url", "title", "snippet", "engine", "published", "engines", "first_party"}
        for r in entry["results"]
    )


# ── Degradation reaches the run record (owner, 2026-10-26) ───────────────────────────────────

BRAVE = SearchResult("https://example.test/b", "From brave", "", "brave", None, ("brave",))
WIKI = SearchResult("https://example.test/w", "From wiki", "", "wikipedia", None, ("wikipedia",))
DDG = SearchResult("https://example.test/d", "From ddg", "", "duckduckgo", None, ("duckduckgo",))
THROTTLED = [
    EngineFailure("duckduckgo", "CAPTCHA"),
    EngineFailure("wikipedia", "Suspended: too many requests"),
]


async def test_a_degraded_answer_is_recorded_and_stays_degraded_when_reused() -> None:
    inner = OfflineSearch({QUERY: [BRAVE]}, failed_engines=THROTTLED)
    search, _, clock, _ = cached(inner=inner)
    first = await search.search(QUERY)
    clock.now += 60
    second = await search.search(QUERY)  # from the cache: the failures were at fetch time
    assert first.failed_engines == second.failed_engines == tuple(THROTTLED)
    assert second.engines_seen == ("brave",)
    fetched, reused = search.report().records
    wanted = (("duckduckgo", "CAPTCHA"), ("wikipedia", "Suspended: too many requests"))
    assert fetched.failed_engines == reused.failed_engines == wanted
    assert (fetched.outcome, reused.outcome, reused.age_s) == ("fetched", "reused", 60)


async def test_the_report_tells_a_run_that_saw_one_engine_from_one_that_saw_three() -> None:
    clean = OfflineSearch({"clean": [BRAVE, WIKI, DDG]})
    degraded = OfflineSearch({"thin": [BRAVE]}, failed_engines=THROTTLED)
    search, *_ = cached(inner=clean)
    await search.search("clean")
    clean_report = search.report()
    assert clean_report.degraded_searches == 0 and clean_report.failed_engine_counts == {}
    assert clean_report.engines_seen == ("brave", "duckduckgo", "wikipedia")

    search, *_ = cached(inner=degraded)
    await search.search("thin")
    await search.search("thin")  # reused: still a degraded answer
    thin = search.report()
    assert thin.degraded_searches == 2 and thin.searches == 2
    assert thin.failed_engine_counts == {"duckduckgo": 2, "wikipedia": 2}
    assert thin.engines_seen == ("brave",)
    assert json.loads(thin.model_dump_json())["degraded_searches"] == 2  # ready for a run record


async def test_a_failed_search_records_no_engines_and_is_not_counted_as_degraded() -> None:
    down = OfflineSearch(fail_with=SearchUnavailable("down"))
    search, *_ = cached(inner=down)
    with pytest.raises(SearchUnavailable):
        await search.search(QUERY)
    (record,) = search.report().records
    assert (record.outcome, record.failed_engines, record.engines_seen) == ("failed", (), ())
    assert search.report().degraded_searches == 0


async def test_an_entry_without_the_failure_record_is_a_miss_never_a_clean_answer() -> None:
    search, kv, _, inner = cached()
    await search.search(QUERY, limit=2)
    (key,) = kv.data
    entry = json.loads(kv.data[key])
    del entry["failed_engines"]  # what an entry written before the field existed would look like
    kv.data[key] = json.dumps(entry)
    await search.search(QUERY, limit=2)
    assert len(inner.calls) == 2  # fetched again rather than trusted as undegraded


def test_entries_written_before_the_failure_record_are_under_another_key_version() -> None:
    assert cache_key(QUERY).startswith("search_cache:v3:")  # v3 also records the declared set


# ── A degraded answer is cached for its own, shorter TTL (owner, 2026-10-26) ─────────────────


async def test_a_degraded_answer_gets_the_short_expiry_and_a_clean_one_the_long() -> None:
    degraded = OfflineSearch({"thin": [BRAVE]}, failed_engines=THROTTLED)
    clean = OfflineSearch({"full": [BRAVE, WIKI, DDG]})
    search, kv, *_ = cached(inner=degraded)
    await search.search("thin")
    (key,) = kv.expiries
    assert kv.expiries[key] == DEGRADED_TTL  # stored with the short expiry, not the day's
    search, kv, *_ = cached(inner=clean)
    await search.search("full")
    (key,) = kv.expiries
    assert kv.expiries[key] == TTL


async def test_one_bad_minute_does_not_poison_the_rest_of_the_day() -> None:
    clock = Clock()
    state = {"failed": list(THROTTLED)}

    class Recovering:
        calls = 0

        async def search(self, query: str, *, limit: int = 10) -> SearchResponse:
            self.calls += 1
            return SearchResponse([BRAVE, WIKI, DDG][:limit], tuple(state["failed"]))

    inner = Recovering()
    search = CachedSearch(
        inner, FakeKV(), ttl_s=TTL, degraded_ttl_s=DEGRADED_TTL, declared_engines=None, clock=clock
    )  # type: ignore[arg-type]
    assert (await search.search(QUERY)).degraded
    clock.now += DEGRADED_TTL - 1
    assert (await search.search(QUERY)).degraded and inner.calls == 1  # still within the short TTL
    state["failed"] = []  # the engines are back
    clock.now += 2  # past it, though far short of a day
    assert not (await search.search(QUERY)).degraded and inner.calls == 2
    clock.now += TTL - 10  # the clean answer is kept the long TTL
    assert not (await search.search(QUERY)).degraded and inner.calls == 2


async def test_a_degraded_entry_older_than_the_short_ttl_is_a_miss_even_if_redis_holds_it() -> None:
    inner = OfflineSearch({QUERY: [BRAVE]}, failed_engines=THROTTLED)
    search, _, clock, _ = cached(inner=inner)
    await search.search(QUERY)
    clock.now += DEGRADED_TTL + 1
    await search.search(QUERY)
    assert len(inner.calls) == 2


async def test_a_reused_degraded_answer_stays_marked_and_the_report_states_both_ttls() -> None:
    inner = OfflineSearch({QUERY: [BRAVE]}, failed_engines=THROTTLED)
    search, *_ = cached(inner=inner)
    await search.search(QUERY)
    reused = await search.search(QUERY)
    assert reused.degraded and len(inner.calls) == 1
    report = search.report()
    assert (report.ttl_s, report.degraded_ttl_s, report.degraded_searches) == (
        TTL,
        DEGRADED_TTL,
        2,
    )


# ── The declared engine set reaches the report and governs reuse (owner, 2026-10-26) ─────────

BRAVE_ONLY = ("brave",)
OTHERS_DOWN = [
    EngineFailure("duckduckgo", "CAPTCHA"),
    EngineFailure("wikipedia", "too many requests"),
]


def brave_only(kv: FakeKV | None = None, *, declared: tuple[str, ...] | None = BRAVE_ONLY):
    inner = OfflineSearch({QUERY: [BRAVE]}, failed_engines=OTHERS_DOWN, declared_engines=declared)
    return cached(kv, inner=inner, declared=declared)


async def test_a_brave_only_run_is_complete_and_the_report_says_which_set_it_is_complete_for() -> (
    None
):
    search, *_ = brave_only()
    got = await search.search(QUERY)
    assert not got.degraded
    report = search.report()
    assert report.declared_engines == BRAVE_ONLY
    assert report.degraded_searches == 0 and report.failed_engine_counts == {}
    assert report.undeclared_failure_counts == {"duckduckgo": 1, "wikipedia": 1}
    assert report.engines_seen == ("brave",) and report.undeclared_engines_seen == ()
    assert json.loads(report.model_dump_json())["declared_engines"] == ["brave"]  # in the record


async def test_an_undeclared_run_says_so_in_its_report() -> None:
    search, *_ = brave_only(declared=None)
    assert (await search.search(QUERY)).degraded  # nothing declared: every failure counts
    report = search.report()
    assert report.declared_engines is None and report.degraded_searches == 1
    assert json.loads(report.model_dump_json())["declared_engines"] is None


async def test_an_entry_fetched_under_another_declared_set_is_never_reused() -> None:
    kv = FakeKV()
    search, *_ = brave_only(kv)
    await search.search(QUERY)
    for other in (None, ("brave", "duckduckgo"), ("duckduckgo",)):
        inner = OfflineSearch({QUERY: [BRAVE]}, failed_engines=OTHERS_DOWN, declared_engines=other)
        again, _, _, spy = cached(kv, inner=inner, declared=other)
        await again.search(QUERY)
        assert len(spy.calls) == 1, f"reused an entry fetched under {BRAVE_ONLY} for {other}"
    same, _, _, spy = brave_only(kv)
    await same.search(QUERY)
    assert spy.calls == []  # under the same declared set it is reused


async def test_a_provider_declared_differently_from_the_run_is_refused() -> None:
    inner = OfflineSearch({QUERY: [BRAVE]}, declared_engines=["brave"])
    search, *_ = cached(inner=inner, declared=("brave", "duckduckgo"))
    with pytest.raises(ValueError, match="must be the same set"):
        await search.search(QUERY)


async def test_an_undeclared_engine_that_returns_a_result_is_reported() -> None:
    inner = OfflineSearch({QUERY: [BRAVE, WIKI]}, declared_engines=["brave"])
    search, *_ = cached(inner=inner, declared=("brave",))
    await search.search(QUERY)
    assert search.report().undeclared_engines_seen == ("wikipedia",)


def test_the_declared_set_is_the_four_engines_the_owner_declared_on_2026_10_28() -> None:
    # Declared from the 45-search run-to-completion hold: answered with rows in every search, no
    # failure of any kind (docs/evaluation-plan.md section 8). Changing it changes every cache key.
    assert load_scoring().research.declared_engines == ["yep", "yandex", "yahoo", "duckduckgo web"]


async def test_every_result_reports_both_the_declared_count_and_the_count_that_contributed() -> (
    None
):
    inner = OfflineSearch({QUERY: [BRAVE, WIKI]}, declared_engines=["brave"])
    search, *_ = cached(inner=inner, declared=("brave",))
    await search.search(QUERY)
    report = search.report()
    assert report.declared_count == 1 and report.contributing_count == 2
    assert report.undeclared_engines_seen == ("wikipedia",)
    assert report.engines_line == "1 declared; 2 contributed (undeclared: wikipedia)"
    record = report.records[0]
    assert record.contributing_count == 2 and record.undeclared_contributing_count == 1
    assert record.undeclared_seen == ("wikipedia",)  # and WHICH one


async def test_an_undeclared_run_reports_no_declared_count_but_still_the_contributing_one() -> None:
    search, *_ = brave_only(declared=None)
    await search.search(QUERY)
    report = search.report()
    assert report.declared_count is None and report.contributing_count == 1
    assert report.engines_line == "not declared; 1 contributed"


@pytest.mark.parametrize("bad", [[], [""], ["brave", "Brave"], ["brave", " "]])
def test_a_declared_set_in_config_must_be_null_or_a_real_set(bad: list[str]) -> None:
    raw = load_scoring().research.model_dump()
    with pytest.raises(ValueError, match="declared_engines"):
        Research.model_validate({**raw, "declared_engines": bad})
    Research.model_validate({**raw, "declared_engines": ["brave"]})


def test_declared_engines_is_a_required_key_not_a_default() -> None:
    raw = load_scoring().research.model_dump()
    del raw["declared_engines"]
    with pytest.raises(ValueError, match="declared_engines"):
        Research.model_validate(raw)


# ── A real Redis ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.redis
async def test_against_a_real_redis_the_key_expires_with_the_ttl_and_is_reused(
    redis_settings: object,
) -> None:
    redis = Redis.from_url(str(redis_settings.redis_url))  # type: ignore[attr-defined]
    try:
        search = CachedSearch(
            OfflineSearch({QUERY: RESULTS}),
            redis,
            ttl_s=120,
            degraded_ttl_s=30,
            declared_engines=None,
        )
        await search.search(QUERY, limit=3)
        assert 0 < await redis.ttl(cache_key(QUERY)) <= 120
        assert (await search.search(QUERY, limit=3)).results == RESULTS[:3]
        report = search.report()
        assert (report.fetched, report.reused, report.cache_errors) == (1, 1, 0)
        assert report.records[1].age_s is not None and report.records[1].age_s >= 0
    finally:
        await redis.aclose()


async def test_an_entry_that_records_another_declared_set_is_a_miss_even_under_this_key() -> None:
    kv = FakeKV()
    search, *_ = brave_only(kv)
    await search.search(QUERY)
    (key,) = kv.data
    entry = json.loads(kv.data[key])
    entry["declared_engines"] = ["duckduckgo"]  # the record disagrees with the key it sits under
    kv.data[key] = json.dumps(entry)
    again, _, _, spy = brave_only(kv)
    await again.search(QUERY)
    assert len(spy.calls) == 1  # refetched


def test_the_declared_set_is_part_of_the_cache_key() -> None:
    assert cache_key(QUERY) != cache_key(QUERY, ("brave",)) != cache_key(QUERY, ("brave", "x"))
    assert cache_key(QUERY, ("brave",)) == cache_key("  KITCHEN starter  set REVIEWS ", ("brave",))
