"""The zero-retention page cache (app/research/page_cache.py; Architecture.md section 6).

A fake key-value store and a hand-set clock make every age exact; one test at the end runs against
a real Redis. Postgres is not involved anywhere: there is nothing here that could write to it.
"""

import json
import time

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.errors import FetchFailed, FetchRefused, FetchUnsupported
from app.memory.scoring import load_scoring
from app.research.fetch import FetchedPage
from app.research.page_cache import CachedPages, cache_key

pytestmark = pytest.mark.anyio

URL = "https://shop.example.test/products/kadai"
TTL = 3600
START = 1_000_000.0


def page(url: str = URL, *, fetched_at: float = START, html: str = "<p>kadai</p>") -> FetchedPage:
    return FetchedPage(url, url, 200, "text/html", html, fetched_at, 0)


class FakeKV:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.expiries: dict[str, int] = {}
        self.broken = False

    async def get(self, name: str, /) -> str | None:
        if self.broken:
            raise RedisConnectionError("down")
        return self.data.get(name)

    async def set(self, name: str, value: str, /, *, ex: int) -> bool:
        if self.broken:
            raise RedisConnectionError("down")
        self.data[name], self.expiries[name] = value, ex
        return True


class Clock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Source:
    """A PageSource: answers with the page for a URL, or raises what it was told to."""

    def __init__(self, pages: dict[str, FetchedPage] | None = None, error: Exception | None = None):
        self.pages, self.error, self.calls = pages or {URL: page()}, error, []

    async def fetch(self, url: str) -> FetchedPage:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.pages[url]


def cached(
    kv: FakeKV | None = None,
    clock: Clock | None = None,
    *,
    ttl_s: int = TTL,
    mode: str = "use",
    inner: Source | None = None,
) -> tuple[CachedPages, FakeKV, Clock, Source]:
    kv, clock, inner = kv or FakeKV(), clock or Clock(), inner or Source()
    return CachedPages(inner, kv, ttl_s=ttl_s, mode=mode, clock=clock), kv, clock, inner  # type: ignore[arg-type]


def test_the_ttl_comes_from_scoring_config_and_the_cache_has_no_default_for_it() -> None:
    assert load_scoring().research.page_cache_ttl_s >= 1
    with pytest.raises(TypeError):
        CachedPages(Source(), FakeKV())  # type: ignore[call-arg, arg-type]
    with pytest.raises(ValueError, match="at least 1"):
        CachedPages(Source(), FakeKV(), ttl_s=0)  # type: ignore[arg-type]


async def test_a_second_fetch_is_reused_with_its_age_recorded() -> None:
    cache, _, clock, inner = cached()
    first = await cache.fetch(URL)
    clock.now += 125.5
    second = await cache.fetch(URL)
    assert second == first and inner.calls == [URL]  # the web was asked once
    one, two = cache.report().records
    assert (one.outcome, one.age_s) == ("fetched", None)
    assert (two.outcome, two.age_s, two.bytes) == ("reused", 125.5, len(first.html))


async def test_an_entry_older_than_the_ttl_in_force_is_a_miss_even_if_redis_still_holds_it() -> (
    None
):
    cache, kv, clock, inner = cached()
    await cache.fetch(URL)
    clock.now += TTL + 1
    await cache.fetch(URL)
    assert len(inner.calls) == 2
    stricter, _, _, again = cached(kv, Clock(START + 100), ttl_s=50)  # same store, lower TTL
    await stricter.fetch(URL)
    assert len(again.calls) == 1  # 100 s old against a 50 s TTL


async def test_urls_differing_only_in_the_fragment_share_one_entry() -> None:
    cache, _, _, inner = cached()
    await cache.fetch(URL)
    await cache.fetch(URL + "#reviews")
    assert inner.calls == [URL]
    assert cache_key(URL) == cache_key("  " + URL + "#x ")


async def test_bypass_neither_reads_nor_writes() -> None:
    cache, kv, _, inner = cached(mode="bypass")
    await cache.fetch(URL)
    await cache.fetch(URL)
    assert len(inner.calls) == 2 and kv.data == {}
    report = cache.report()
    assert (report.mode, report.fetched, report.reused, report.reused_share) == (
        "bypass",
        2,
        0,
        0.0,
    )


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (FetchRefused("private"), "FetchRefused"),
        (FetchFailed("404", status=404), "FetchFailed"),
        (FetchUnsupported("pdf"), "FetchUnsupported"),
    ],
)
async def test_a_failure_is_recorded_by_kind_and_never_cached(error: Exception, kind: str) -> None:
    cache, kv, _, inner = cached(inner=Source(error=error))
    with pytest.raises(type(error)):
        await cache.fetch(URL)
    with pytest.raises(type(error)):
        await cache.fetch(URL)
    assert len(inner.calls) == 2 and kv.data == {}  # nothing stored, so the next try goes out
    report = cache.report()
    assert (report.failed, report.fetched, report.failures_by_kind) == (2, 0, {kind: 2})


async def test_a_dead_redis_degrades_to_fetching_and_is_counted() -> None:
    kv = FakeKV()
    kv.broken = True
    cache, _, _, inner = cached(kv)
    assert (await cache.fetch(URL)).html == "<p>kadai</p>"
    assert (await cache.fetch(URL)).html == "<p>kadai</p>"
    assert len(inner.calls) == 2
    assert cache.report().cache_errors == 2


async def test_a_corrupt_entry_is_a_miss_replaced_by_the_fetch() -> None:
    cache, kv, _, inner = cached()
    await cache.fetch(URL)
    (key,) = kv.data
    for corrupt in (
        "not json",
        "[]",
        json.dumps({"url": URL}),
        json.dumps({**json.loads(kv.data[key]), "extra": 1}),
    ):
        kv.data[key] = corrupt
        await cache.fetch(URL)
    assert len(inner.calls) == 5


async def test_the_report_states_the_ttl_and_how_much_was_fetched_versus_reused() -> None:
    other = "https://other.example.test/a"
    cache, _, clock, _ = cached(inner=Source({URL: page(), other: page(other)}))
    await cache.fetch(URL)
    await cache.fetch(other)
    clock.now += 10
    await cache.fetch(URL)
    clock.now += 20
    await cache.fetch(other)
    report = cache.report()
    assert (report.mode, report.ttl_s) == ("use", TTL)
    assert (report.fetches, report.fetched, report.reused, report.failed) == (4, 2, 2, 0)
    assert report.reused_share == 0.5 and report.oldest_reused_age_s == 30
    assert json.loads(report.model_dump_json())["ttl_s"] == TTL  # ready for a run record


async def test_an_empty_ledger_reports_no_share_rather_than_dividing_by_zero() -> None:
    report = cached()[0].report()
    assert (report.fetches, report.reused_share, report.oldest_reused_age_s) == (0, None, None)


async def test_the_entry_holds_the_page_and_its_fetch_metadata_and_nothing_derived() -> None:
    cache, kv, _, _ = cached()
    await cache.fetch(URL)
    (stored,) = kv.data.values()
    assert set(json.loads(stored)) == {
        "url",
        "final_url",
        "status",
        "content_type",
        "html",
        "fetched_at",
        "redirects",
        "first_party",
    }
    assert set(kv.expiries.values()) == {TTL}  # every entry expires; none is stored without a TTL


def test_the_page_cache_has_no_database_dependency() -> None:
    import app.research.fetch as fetch
    import app.research.page_cache as page_cache

    for module in (fetch, page_cache):
        source = open(module.__file__, encoding="utf-8").read()  # noqa: SIM115, PTH123
        assert "psycopg" not in source and "asyncpg" not in source and "sqlalchemy" not in source


@pytest.mark.redis
async def test_against_a_real_redis_the_key_expires_with_the_ttl_and_is_reused(
    redis_settings: object,
) -> None:
    redis = Redis.from_url(str(redis_settings.redis_url))  # type: ignore[attr-defined]
    try:
        inner = Source({URL: page(fetched_at=time.time())})  # the real clock is in use here
        cache = CachedPages(inner, redis, ttl_s=120)
        await cache.fetch(URL)
        assert 0 < await redis.ttl(cache_key(URL)) <= 120
        assert (await cache.fetch(URL)).html == "<p>kadai</p>" and len(inner.calls) == 1
    finally:
        await redis.delete(cache_key(URL))
        await redis.aclose()
