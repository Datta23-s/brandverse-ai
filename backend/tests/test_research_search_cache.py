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
from app.memory.scoring import load_scoring
from app.research.search import OfflineSearch, SearchResult
from app.research.search_cache import CachedSearch, cache_key

pytestmark = pytest.mark.anyio

QUERY = "kitchen starter set reviews"
RESULTS = [
    SearchResult(f"https://example.test/{n}", f"Result {n}", f"Snippet {n}") for n in range(12)
]
TTL = 3600


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
    mode: str = "use",
    inner: OfflineSearch | None = None,
) -> tuple[CachedSearch, FakeKV, Clock, OfflineSearch]:
    kv, clock = kv or FakeKV(), clock or Clock()
    inner = inner or OfflineSearch({QUERY: RESULTS})
    return CachedSearch(inner, kv, ttl_s=ttl_s, mode=mode, clock=clock), kv, clock, inner  # type: ignore[arg-type]


# ── The TTL is a parameter ───────────────────────────────────────────────────────────────────


def test_the_ttl_comes_from_scoring_config_and_the_cache_has_no_default_for_it() -> None:
    assert load_scoring().research.search_cache_ttl_s >= 1
    with pytest.raises(TypeError, match="ttl_s"):
        CachedSearch(OfflineSearch(), FakeKV())  # type: ignore[call-arg, arg-type]
    with pytest.raises(ValueError, match="ttl_s"):
        CachedSearch(OfflineSearch(), FakeKV(), ttl_s=0)  # type: ignore[arg-type]


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
    assert await search.search(QUERY, limit=5) == RESULTS[:5]
    clock.now += 125.5
    assert await search.search(QUERY, limit=5) == RESULTS[:5]
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
    assert await search.search(QUERY, limit=10) == RESULTS[:10]  # refetched with the larger limit
    assert await search.search(QUERY, limit=4) == RESULTS[:4]  # now answered from the entry
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
    assert await search.search(QUERY, limit=2) == RESULTS[:2]
    assert await search.search(QUERY, limit=2) == RESULTS[:2]
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
    assert set(entry) == {"query", "fetched_at", "limit", "results"}
    assert all(
        set(r) == {"url", "title", "snippet", "engine", "published"} for r in entry["results"]
    )


# ── A real Redis ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.redis
async def test_against_a_real_redis_the_key_expires_with_the_ttl_and_is_reused(
    redis_settings: object,
) -> None:
    redis = Redis.from_url(str(redis_settings.redis_url))  # type: ignore[attr-defined]
    try:
        search = CachedSearch(OfflineSearch({QUERY: RESULTS}), redis, ttl_s=120)
        await search.search(QUERY, limit=3)
        assert 0 < await redis.ttl(cache_key(QUERY)) <= 120
        assert await search.search(QUERY, limit=3) == RESULTS[:3]
        report = search.report()
        assert (report.fetched, report.reused, report.cache_errors) == (1, 1, 0)
        assert report.records[1].age_s is not None and report.records[1].age_s >= 0
    finally:
        await redis.aclose()
