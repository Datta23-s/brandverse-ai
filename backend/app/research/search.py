"""The search interface (Phases.md Phase 5; Architecture.md §11): one contract, two implementations.

`SearchProvider` is the only way research reaches the web. `SearxngSearch` is the real one,
against the self-hosted SearXNG JSON API at `SEARXNG_URL` (no paid SERP: Rules.md C0).
`OfflineSearch` is the CI implementation: canned results keyed by normalised query, no network, and
it can be told to fail so degradation paths are testable. Both satisfy the same contract tests.

The contract:
- results come back in the engine's rank order, at most `limit` of them, each with an http(s) URL
  and a non-blank title; a malformed row from the engine is dropped, never a crash;
- a blank query or a `limit` outside 1..MAX_LIMIT is a programming error (ValueError);
- an engine that cannot be reached, answers with an error, or answers with something that is not a
  result list raises `SearchUnavailable`; one that says "slow down" raises `SearchRateLimited`.
  Callers turn both into a partial result (Phase 5 exit criteria); neither is retried here.

Caching by normalised query (Rules.md C7) and the zero-retention page cache are separate pieces
that sit above this interface; `normalise_query` is the key they share.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.errors import SearchRateLimited, SearchUnavailable

MAX_LIMIT = 50


@dataclass(frozen=True, slots=True)
class SearchResult:
    url: str
    title: str
    snippet: str
    engine: str | None = None
    published: str | None = None  # as the engine reported it; source scoring parses it


class SearchProvider(Protocol):
    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]: ...


def normalise_query(query: str) -> str:
    """Case- and whitespace-insensitive form of a query: the cache key and the offline lookup."""
    return re.sub(r"\s+", " ", query).strip().casefold()


def check_arguments(query: str, limit: int) -> str:
    text = query.strip()
    if not text:
        raise ValueError("a search query must not be blank")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}, got {limit}")
    return text


class SearxngSearch:
    """SearXNG's JSON API. The client is injected so a test can supply a mock transport."""

    def __init__(self, base_url: str, client: httpx.AsyncClient) -> None:
        self._endpoint = base_url.rstrip("/") + "/search"
        self._client = client

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        text = check_arguments(query, limit)
        try:
            response = await self._client.get(self._endpoint, params={"q": text, "format": "json"})
        except httpx.HTTPError as e:  # connect, timeout, protocol: the laptop service is down
            raise SearchUnavailable(f"SearXNG unreachable: {type(e).__name__}") from e
        if response.status_code == 429:
            raise SearchRateLimited("SearXNG rate-limited the request")
        if response.status_code == 403:
            raise SearchUnavailable("SearXNG refused the request; is `json` in search.formats?")
        if response.status_code != 200:
            raise SearchUnavailable(f"SearXNG answered HTTP {response.status_code}")
        try:
            rows = response.json()["results"]
        except (ValueError, KeyError, TypeError) as e:
            raise SearchUnavailable("SearXNG did not answer with a result list") from e
        if not isinstance(rows, list):
            raise SearchUnavailable("SearXNG did not answer with a result list")
        found = (_parse_row(r) for r in rows)
        return [r for r in found if r is not None][:limit]


def _parse_row(row: Any) -> SearchResult | None:
    if not isinstance(row, Mapping):
        return None
    url, title = row.get("url"), row.get("title")
    if not isinstance(url, str) or not re.match(r"^https?://[^\s/]+", url):
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    snippet, engine, published = row.get("content"), row.get("engine"), row.get("publishedDate")
    return SearchResult(
        url=url,
        title=title.strip(),
        snippet=snippet if isinstance(snippet, str) else "",
        engine=engine if isinstance(engine, str) else None,
        published=published if isinstance(published, str) else None,
    )


class OfflineSearch:
    """Canned results for CI: no network, deterministic, and every call is recorded.

    A query with no entry returns no results, as a real search with no hits would. `fail_with`
    makes every call raise it, for the partial-result paths.
    """

    def __init__(
        self,
        results: Mapping[str, Sequence[SearchResult]] | None = None,
        *,
        fail_with: SearchUnavailable | SearchRateLimited | None = None,
    ) -> None:
        self._results = {normalise_query(q): list(rs) for q, rs in (results or {}).items()}
        self._fail_with = fail_with
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        text = check_arguments(query, limit)
        self.calls.append((text, limit))
        if self._fail_with is not None:
            raise self._fail_with
        return self._results.get(normalise_query(text), [])[:limit]


def searxng_from_settings(settings: Settings, client: httpx.AsyncClient) -> SearxngSearch:
    """The real provider, from `SEARXNG_URL`. Unset is `SearchUnavailable`: research degrades to a
    partial result rather than failing the run (Architecture.md §11)."""
    if settings.searxng_url is None:
        raise SearchUnavailable("SEARXNG_URL is not set")
    return SearxngSearch(str(settings.searxng_url), client)
