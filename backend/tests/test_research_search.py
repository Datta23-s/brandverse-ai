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
    EngineFailure,
    OfflineSearch,
    SearchProvider,
    SearchResponse,
    SearchResult,
    SearxngSearch,
    declare,
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
        engines=("duckduckgo",),  # a real row names the engines that returned it
    )
    for n in range(1, 13)
]


def searxng_payload(results: list[SearchResult]) -> dict[str, object]:
    # The shape of a real SearXNG 2026.10.19 answer (docs/searxng.md): the other top-level keys, the
    # per-row keys this provider ignores, and `unresponsive_engines`, which is always present.
    return {
        "query": QUERY,
        "answers": [],
        "corrections": [],
        "infoboxes": [],
        "suggestions": [],
        "unresponsive_engines": [],
        "results": [
            {
                "url": r.url,
                "title": r.title,
                "content": r.snippet,
                "engine": r.engine,
                "engines": [r.engine],
                "category": "general",
                "template": "default.html",
                "score": 1.0,
                "positions": [1],
                "priority": "",
                "parsed_url": ["https", "example.test", "/", "", "", ""],
                "thumbnail": "",
                **({"publishedDate": r.published, "pubdate": r.published} if r.published else {}),
            }
            for r in results
        ],
    }


def searxng_over(handler: Callable[[httpx.Request], httpx.Response]) -> SearxngSearch:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SearxngSearch("http://searxng.test:8080/", client, declared_engines=None)


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
    assert (await provider.search(QUERY, limit=5)).results == RESULTS[:5]
    got = await provider.search(QUERY, limit=50)
    assert got.results == RESULTS  # fewer than the limit: all of them


async def test_the_default_limit_is_ten(provider: SearchProvider) -> None:
    assert len((await provider.search(QUERY)).results) == 10


async def test_a_query_with_no_hits_is_an_empty_list_not_an_error(provider: SearchProvider) -> None:
    response = await provider.search("nothing matches this")
    assert response.results == [] and response.failed_engines == () and not response.degraded


async def test_the_query_is_matched_case_and_whitespace_insensitively(
    provider: SearchProvider,
) -> None:
    got = await provider.search("  Kitchen   STARTER set  reviews ", limit=3)
    assert got.results == RESULTS[:3]


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
    assert found.results == [
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


def answering(results: list[dict[str, object]], unresponsive: object) -> httpx.Response:
    return httpx.Response(200, json={"results": results, "unresponsive_engines": unresponsive})


ROW = {"url": "https://ok.test/a", "title": "Good", "engine": "brave"}


async def test_no_results_with_a_throttled_engine_is_rate_limited_not_an_empty_search() -> None:
    # Observed against the real engine: HTTP 200, no rows, every upstream engine throttled.
    failed = [
        ["brave", "too many requests"],
        ["duckduckgo", "CAPTCHA"],
        ["wikipedia", "Suspended: too many requests"],
    ]
    with pytest.raises(SearchRateLimited, match="brave: too many requests"):
        await searxng_over(lambda _: answering([], failed)).search(QUERY)


async def test_no_results_with_an_engine_that_did_not_answer_is_unavailable() -> None:
    with pytest.raises(SearchUnavailable, match="duckduckgo: CAPTCHA"):
        await searxng_over(lambda _: answering([], [["duckduckgo", "CAPTCHA"]])).search(QUERY)


@pytest.mark.parametrize("unresponsive", [[], None, "not a list", [["only-a-name"]], [[1, 2, 3]]])
async def test_no_results_and_no_named_failure_is_a_search_with_no_hits(
    unresponsive: object,
) -> None:
    response = await searxng_over(lambda _: answering([], unresponsive)).search(QUERY)
    assert response.results == [] and response.failed_engines == ()


async def test_results_with_a_failed_engine_are_returned_and_say_so() -> None:
    row = {**ROW, "engines": ["brave"]}
    failed = [["duckduckgo", "CAPTCHA"], ["wikipedia", "Suspended: too many requests"]]
    found = await searxng_over(lambda _: answering([row], failed)).search(QUERY)
    assert [r.url for r in found.results] == ["https://ok.test/a"]
    assert found.failed_engines == (
        EngineFailure("duckduckgo", "CAPTCHA"),
        EngineFailure("wikipedia", "Suspended: too many requests"),
    )
    assert found.degraded and found.engines_seen == ("brave",)


async def test_a_clean_answer_is_not_degraded_and_names_every_engine_that_returned_a_row() -> None:
    rows = [
        {**ROW, "engine": "brave", "engines": ["brave", "duckduckgo"]},
        {"url": "https://ok.test/b", "title": "B", "engine": "wikipedia", "engines": ["wikipedia"]},
    ]
    found = await searxng_over(lambda _: answering(rows, [])).search(QUERY)
    assert not found.degraded and found.engines_seen == ("brave", "duckduckgo", "wikipedia")
    assert found.results[0].engines == ("brave", "duckduckgo")  # the merged row keeps both


async def test_the_offline_provider_reports_failed_engines_beside_its_results() -> None:
    failed = [EngineFailure("brave", "too many requests")]
    offline = OfflineSearch({QUERY: RESULTS}, failed_engines=failed)
    got = await offline.search(QUERY)
    assert got.degraded and got.failed_engines == tuple(failed)
    with pytest.raises(SearchRateLimited):  # and, with nothing returned, refuses like the real one
        await offline.search("nothing matches this")


def test_first_party_is_unpopulated_everywhere() -> None:
    assert SearchResult("https://x.test", "t", "").first_party is None
    assert all(r.first_party is None for r in RESULTS)


async def test_rows_that_all_fail_to_parse_count_as_no_results() -> None:
    rows = [{"url": "ftp://x.test", "title": "No"}]
    with pytest.raises(SearchUnavailable):
        await searxng_over(lambda _: answering(rows, [["duckduckgo", "CAPTCHA"]])).search(QUERY)


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
    (await offline.search(QUERY)).results.clear()
    assert len((await offline.search(QUERY)).results) == 10


# ── Configuration ────────────────────────────────────────────────────────────────────────────


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_an_unset_searxng_url_is_search_unavailable_not_a_crash() -> None:
    with pytest.raises(SearchUnavailable, match="SEARXNG_URL"):
        searxng_from_settings(settings(), httpx.AsyncClient(), declared_engines=None)


async def test_the_provider_is_built_from_the_configured_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    built = searxng_from_settings(
        settings(searxng_url="http://127.0.0.1:8080"), client, declared_engines=None
    )
    await built.search(QUERY)
    assert str(seen[0].url).startswith("http://127.0.0.1:8080/search?")


def test_normalise_query_is_the_shared_cache_key() -> None:
    assert normalise_query("  A\tb\n C ") == "a b c"
    assert normalise_query("STRASSE") == normalise_query("straße")  # casefold, not lower


# ── Degradation is measured against the declared engine set (owner, 2026-10-26) ──────────────


def declared(engines: list[str] | None) -> SearxngSearch:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    return SearxngSearch("http://searxng.test:8080/", client, declared_engines=engines)


def answered_by(
    rows: list[dict[str, object]], unresponsive: list[list[str]], engines: list[str] | None
) -> SearxngSearch:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: answering(rows, unresponsive))
    )
    return SearxngSearch("http://searxng.test:8080/", client, declared_engines=engines)


BRAVE_ROW = {"url": "https://ok.test/a", "title": "Good", "engine": "brave", "engines": ["brave"]}
THROTTLED_OTHERS = [["duckduckgo", "CAPTCHA"], ["wikipedia", "Suspended: too many requests"]]


async def test_a_brave_only_search_that_brave_answered_is_complete_not_degraded() -> None:
    found = await answered_by([BRAVE_ROW], THROTTLED_OTHERS, ["brave"]).search(QUERY)
    assert not found.degraded and found.failed_engines == ()
    assert found.declared_engines == ("brave",)
    # What failed outside the declared set is kept, not dropped and not counted against it.
    assert found.undeclared_failures == (
        EngineFailure("duckduckgo", "CAPTCHA"),
        EngineFailure("wikipedia", "Suspended: too many requests"),
    )


async def test_a_failure_of_a_declared_engine_is_what_makes_an_answer_degraded() -> None:
    rows = [BRAVE_ROW]
    unresponsive = [["brave", "too many requests"], ["wikipedia", "CAPTCHA"]]
    found = await answered_by(rows, unresponsive, ["brave", "duckduckgo"]).search(QUERY)
    assert found.degraded and found.failed_engines == (EngineFailure("brave", "too many requests"),)
    assert found.undeclared_failures == (EngineFailure("wikipedia", "CAPTCHA"),)


async def test_no_results_with_only_undeclared_engines_failed_is_a_genuine_empty() -> None:
    found = await answered_by([], THROTTLED_OTHERS, ["brave"]).search(QUERY)
    assert found.results == [] and not found.degraded  # brave answered; nothing was found


async def test_no_results_with_the_declared_engine_throttled_is_still_refused() -> None:
    unresponsive = [["brave", "Suspended: too many requests"], *THROTTLED_OTHERS]
    with pytest.raises(SearchRateLimited, match="brave"):
        await answered_by([], unresponsive, ["brave"]).search(QUERY)


async def test_nothing_declared_means_every_engine_counts_as_before() -> None:
    found = await answered_by([BRAVE_ROW], THROTTLED_OTHERS, None).search(QUERY)
    assert found.degraded and found.declared_engines is None and found.undeclared_failures == ()
    with pytest.raises(SearchUnavailable):
        await answered_by([], [["duckduckgo", "CAPTCHA"]], None).search(QUERY)


async def test_an_engine_outside_the_declared_set_that_returns_results_is_flagged() -> None:
    wiki = {
        "url": "https://ok.test/w",
        "title": "Wiki",
        "engine": "wikipedia",
        "engines": ["wikipedia"],
    }
    found = await answered_by([BRAVE_ROW, wiki], [], ["brave"]).search(QUERY)
    assert found.engines_seen == ("brave", "wikipedia") and found.undeclared_seen == ("wikipedia",)
    assert (await answered_by([BRAVE_ROW], [], None).search(QUERY)).undeclared_seen == ()


async def test_engine_names_are_compared_case_insensitively() -> None:
    row = {**BRAVE_ROW, "engine": "Brave"}
    found = await answered_by(
        [row], [["Brave", "CAPTCHA"], ["DuckDuckGo", "CAPTCHA"]], ["BRAVE"]
    ).search(QUERY)
    assert found.declared_engines == ("brave",)
    assert found.failed_engines == (EngineFailure("Brave", "CAPTCHA"),)
    assert found.undeclared_failures == (EngineFailure("DuckDuckGo", "CAPTCHA"),)


def test_a_declared_set_is_normalised_and_cannot_be_empty_or_blank() -> None:
    assert declare(["Brave", " duckduckgo ", "brave"]) == ("brave", "duckduckgo")
    assert declare(None) is None
    for bad in ([], [""], ["brave", "  "]):
        with pytest.raises(ValueError, match="at least one engine"):
            declare(bad)
        with pytest.raises(ValueError, match="at least one engine"):
            declared(bad)


def test_the_provider_has_no_default_for_the_declared_set() -> None:
    with pytest.raises(TypeError, match="declared_engines"):
        SearxngSearch("http://x.test", httpx.AsyncClient())  # type: ignore[call-arg]


async def test_the_offline_provider_applies_the_same_rule() -> None:
    failures = [EngineFailure("duckduckgo", "CAPTCHA")]
    one = OfflineSearch({QUERY: RESULTS}, failed_engines=failures, declared_engines=["brave"])
    got = await one.search(QUERY)
    assert not got.degraded and got.undeclared_failures == tuple(failures)
    assert (
        await OfflineSearch({"x": []}, failed_engines=failures, declared_engines=["brave"]).search(
            "x"
        )
    ).results == []
    with pytest.raises(SearchUnavailable):
        await OfflineSearch(
            {"x": []}, failed_engines=failures, declared_engines=["duckduckgo"]
        ).search("x")


# ── A search is complete for recording when at least three declared engines answered ──────────


def _rows(*engines: str) -> list[SearchResult]:
    return [SearchResult(f"https://{e}.example/p", "t", "s", e, None, (e,)) for e in engines]


DECLARED_FOUR = ("duckduckgo web", "yahoo", "yandex", "yep")


def test_three_of_four_declared_engines_answering_is_complete_and_still_degraded():
    got = SearchResponse(
        _rows("yandex", "yep", "duckduckgo web"),
        failed_engines=(EngineFailure("yahoo", "parsing error"),),
        declared_engines=DECLARED_FOUR,
    )
    assert got.complete and got.degraded
    assert got.declared_answering == ("duckduckgo web", "yandex", "yep")


def test_two_of_four_declared_engines_answering_is_not_complete():
    got = SearchResponse(_rows("yandex", "yep"), declared_engines=DECLARED_FOUR)
    assert not got.complete and not got.degraded  # nothing named failed; two did not answer


def test_an_engine_outside_the_declared_set_does_not_count_toward_complete():
    got = SearchResponse(_rows("yandex", "yep", "bing", "brave"), declared_engines=DECLARED_FOUR)
    assert got.declared_answering == ("yandex", "yep") and not got.complete


def test_a_declared_set_smaller_than_three_needs_every_engine_and_nothing_declared_means_clean():
    pair = ("yandex", "yep")
    assert not SearchResponse(_rows("yep"), declared_engines=pair).complete
    assert SearchResponse(_rows("yep", "yandex"), declared_engines=pair).complete
    assert SearchResponse(_rows("brave")).complete
    failed = (EngineFailure("brave", "x"),)
    assert not SearchResponse(_rows("brave"), failed_engines=failed).complete
