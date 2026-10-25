"""The search interface (app/research/search.py; Phases.md Phase 5): one contract, two providers.

The contract tests run against both `OfflineSearch` and `SearxngSearch` (over an httpx mock
transport serving the same canned results), so the CI mock cannot drift from the real provider's
behaviour. Nothing here touches the network.
"""

from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from app.core.config import Settings
from app.errors import SearchRateLimited, SearchUnavailable
from app.research.search import (
    MAX_LIMIT,
    OfflineSearch,
    SearchProvider,
    SearchResult,
    SearxngSearch,
    normalise_query,
    searxng_from_settings,
)

pytestmark = pytest.mark.anyio

QUERY = "kitchen starter set reviews"
RESULTS = [
    SearchResult(
        url=f"https://example.test/{n}",
        title=f"Result {n}",
        snippet=f"Snippet {n}",
        engine="duckduckgo",
        published="2026-10-01T00:00:00" if n == 1 else None,
    )
    for n in range(1, 13)
]


def searxng_payload(results: list[SearchResult]) -> dict[str, object]:
    return {
        "query": QUERY,
        "results": [
            {
                "url": r.url,
                "title": r.title,
                "content": r.snippet,
                "engine": r.engine,
                **({"publishedDate": r.published} if r.published else {}),
            }
            for r in results
        ],
    }


def searxng_over(handler: Callable[[httpx.Request], httpx.Response]) -> SearxngSearch:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SearxngSearch("http://searxng.test:8080/", client)


def serving_canned(request: httpx.Request) -> httpx.Response:
    asked = request.url.params["q"]
    body = searxng_payload(RESULTS if normalise_query(asked) == normalise_query(QUERY) else [])
    return httpx.Response(200, json=body)


@pytest.fixture(params=["offline", "searxng"])
async def provider(request: pytest.FixtureRequest) -> AsyncIterator[SearchProvider]:
    if request.param == "offline":
        yield OfflineSearch({QUERY: RESULTS})
    else:
        yield searxng_over(serving_canned)


# ── The contract, for both providers ─────────────────────────────────────────────────────────


async def test_results_come_back_in_rank_order_up_to_the_limit(provider: SearchProvider) -> None:
    assert await provider.search(QUERY, limit=5) == RESULTS[:5]
    assert await provider.search(QUERY, limit=50) == RESULTS  # fewer than the limit: all of them


async def test_the_default_limit_is_ten(provider: SearchProvider) -> None:
    assert len(await provider.search(QUERY)) == 10


async def test_a_query_with_no_hits_is_an_empty_list_not_an_error(provider: SearchProvider) -> None:
    assert await provider.search("nothing matches this") == []


async def test_the_query_is_matched_case_and_whitespace_insensitively(
    provider: SearchProvider,
) -> None:
    assert await provider.search("  Kitchen   STARTER set  reviews ", limit=3) == RESULTS[:3]


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
async def test_a_blank_query_is_refused(provider: SearchProvider, query: str) -> None:
    with pytest.raises(ValueError, match="blank"):
        await provider.search(query)


@pytest.mark.parametrize("limit", [0, -1, MAX_LIMIT + 1])
async def test_a_limit_outside_the_range_is_refused(provider: SearchProvider, limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        await provider.search(QUERY, limit=limit)


# ── SearXNG: what it sends and what it can go wrong with ─────────────────────────────────────


async def test_searxng_is_asked_for_json_with_the_stripped_query() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": []})

    await searxng_over(handler).search("  first kitchen  ")
    (request,) = seen
    assert request.url.path == "/search"  # the base URL's trailing slash is not doubled
    assert dict(request.url.params) == {"q": "first kitchen", "format": "json"}


async def test_malformed_rows_are_dropped_and_the_rest_kept() -> None:
    rows = [
        {"url": "https://ok.test/a", "title": "Good"},
        {"url": "ftp://nope.test/file", "title": "Not http"},
        {"url": "not a url", "title": "Not a url"},
        {"url": "https://ok.test/b", "title": "   "},
        {"url": "https://ok.test/c"},
        "a string, not an object",
        {"url": "https://ok.test/d", "title": " Padded ", "content": 7, "engine": ["x"]},
    ]
    found = await searxng_over(lambda _: httpx.Response(200, json={"results": rows})).search(QUERY)
    assert found == [
        SearchResult("https://ok.test/a", "Good", ""),
        SearchResult("https://ok.test/d", "Padded", ""),  # a non-string field becomes empty/None
    ]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"no_results_key": []}),
        httpx.Response(200, json={"results": "a string"}),
        httpx.Response(200, json=["a", "list"]),
        httpx.Response(500),
        httpx.Response(502),
        httpx.Response(403),
    ],
)
async def test_an_engine_that_does_not_answer_with_results_is_unavailable(
    response: httpx.Response,
) -> None:
    with pytest.raises(SearchUnavailable):
        await searxng_over(lambda _: response).search(QUERY)


async def test_a_403_says_how_to_fix_the_engine() -> None:
    with pytest.raises(SearchUnavailable, match="search.formats"):
        await searxng_over(lambda _: httpx.Response(403)).search(QUERY)


async def test_a_429_is_rate_limited_not_unavailable() -> None:
    with pytest.raises(SearchRateLimited):
        await searxng_over(lambda _: httpx.Response(429)).search(QUERY)


@pytest.mark.parametrize("failure", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
async def test_an_unreachable_engine_is_unavailable_never_a_crash(failure: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise failure

    with pytest.raises(SearchUnavailable, match="unreachable") as raised:
        await searxng_over(handler).search(QUERY)
    assert raised.value.__cause__ is failure


def test_rate_limited_and_unavailable_are_both_search_errors() -> None:
    from app.errors import SearchError

    assert issubclass(SearchRateLimited, SearchError) and issubclass(SearchUnavailable, SearchError)


# ── The offline provider's own promises ──────────────────────────────────────────────────────


async def test_the_offline_provider_records_each_call_and_can_be_told_to_fail() -> None:
    offline = OfflineSearch({QUERY: RESULTS})
    await offline.search(f"  {QUERY} ", limit=2)
    assert offline.calls == [(QUERY, 2)]  # the stripped query, as the real provider sends it

    for failure in (SearchUnavailable("down"), SearchRateLimited("slow")):
        failing = OfflineSearch({QUERY: RESULTS}, fail_with=failure)
        with pytest.raises(type(failure)):
            await failing.search(QUERY)
        assert failing.calls == [(QUERY, 10)]


async def test_the_offline_provider_never_returns_its_own_storage() -> None:
    offline = OfflineSearch({QUERY: RESULTS})
    (await offline.search(QUERY)).clear()
    assert len(await offline.search(QUERY)) == 10


# ── Configuration ────────────────────────────────────────────────────────────────────────────


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_an_unset_searxng_url_is_search_unavailable_not_a_crash() -> None:
    with pytest.raises(SearchUnavailable, match="SEARXNG_URL"):
        searxng_from_settings(settings(), httpx.AsyncClient())


async def test_the_provider_is_built_from_the_configured_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    built = searxng_from_settings(settings(searxng_url="http://127.0.0.1:8080"), client)
    await built.search(QUERY)
    assert str(seen[0].url).startswith("http://127.0.0.1:8080/search?")


def test_normalise_query_is_the_shared_cache_key() -> None:
    assert normalise_query("  A\tb\n C ") == "a b c"
    assert normalise_query("STRASSE") == normalise_query("straße")  # casefold, not lower
