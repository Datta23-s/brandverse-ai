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
  Callers turn both into a partial result (Phase 5 exit criteria); neither is retried here;
- SearXNG itself answers HTTP 200 with NO results when its upstream engines are throttled or
  CAPTCHA'd, naming them in `unresponsive_engines` (observed against the real engine). Zero results
  with any engine named is refused (`SearchRateLimited` if one says "too many requests", else
  `SearchUnavailable`), not returned as a search with no hits;
- **a degraded answer says so.** Results with an engine named as failed are returned inside a
  `SearchResponse` that carries the failed engines and their reasons, and `engines_seen`, the
  engines that actually contributed a result. A search that saw one engine is not the same evidence
  as one that saw three, and the run record and report keep the difference (search_cache.py; owner,
  2026-10-26: it bears on Rules.md C4's partial-on-breach rule);
- **degraded is measured against the DECLARED engine set**, not against every engine SearXNG could
  run (owner, 2026-10-26). `declared_engines` has no default: it is `research.declared_engines`,
  None until the rate probe has shown which engines answer. With a set declared, only a failure of
  an engine in it makes an answer degraded or a zero-result answer a refusal; failures outside it
  are kept as `undeclared_failures`; a result from an engine outside it is `undeclared_seen`.

Caching by normalised query (Rules.md C7) and the zero-retention page cache are separate pieces
that sit above this interface; `normalise_query` is the key they share.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.errors import SearchRateLimited, SearchUnavailable

MAX_LIMIT = 50
# A search is complete for recording when this many declared engines answered with rows. The
# declared SET, and so the cache key, is unchanged by it (docs/evaluation-plan.md section 8).
MIN_DECLARED_ANSWERING = 3


@dataclass(frozen=True, slots=True)
class SearchResult:
    url: str
    title: str
    snippet: str
    engine: str | None = None
    published: str | None = None  # as the engine reported it; source scoring parses it
    # Every engine that returned this URL (the engine merges the same URL across engines);
    # `engine` is only the first of them.
    engines: tuple[str, ...] = ()
    # Whether the page is the brand's own domain. UNPOPULATED (None) until Phase 6 supplies a brand
    # domain, and NOTHING scores or filters on it (V6, docs/evaluation-plan.md): the field exists so
    # the pipeline does not have to be reopened when it is filled.
    first_party: bool | None = None


@dataclass(frozen=True, slots=True)
class EngineFailure:
    """An upstream engine the search engine named as not having answered, and why."""

    engine: str
    reason: str


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """What a search returned, and how complete that is **with respect to the declared engine set**.

    `declared_engines` is the configuration declared in advance and reported beside every result
    (None: not declared, so every engine the search engine names counts). `failed_engines` are the
    declared engines that failed, and only they make an answer degraded; `undeclared_failures` are
    engines outside the declared set that failed, kept in the record and otherwise ignored (owner,
    2026-10-26, docs/evaluation-plan.md section 8)."""

    results: list[SearchResult]
    failed_engines: tuple[EngineFailure, ...] = ()
    undeclared_failures: tuple[EngineFailure, ...] = ()
    declared_engines: tuple[str, ...] | None = None

    @property
    def undeclared_seen(self) -> tuple[str, ...]:
        """Engines outside the declared set that nonetheless returned a result: a mismatch between
        the declaration and the search engine's own configuration, visible rather than silent."""
        if self.declared_engines is None:
            return ()
        return tuple(e for e in self.engines_seen if e not in self.declared_engines)

    @property
    def engines_seen(self) -> tuple[str, ...]:
        """The engines that contributed at least one returned result, sorted. An engine that
        answered with nothing is not visible here, and not listed as failed either."""
        names = {n for r in self.results for n in (*r.engines, *([r.engine] if r.engine else []))}
        return tuple(sorted(names))

    @property
    def degraded(self) -> bool:
        return bool(self.failed_engines)

    @property
    def declared_answering(self) -> tuple[str, ...]:
        """The declared engines that contributed at least one row, sorted. Nothing declared: every
        engine seen."""
        if self.declared_engines is None:
            return self.engines_seen
        return tuple(sorted({e.casefold() for e in self.engines_seen} & set(self.declared_engines)))

    @property
    def complete(self) -> bool:
        """Whether the answer is a complete search FOR RECORDING (owner, 2026-10-29): at least
        `MIN_DECLARED_ANSWERING` of the declared engines answered with rows (a declared set smaller
        than that needs all of them). Nothing declared: no engine failed. `degraded` is unchanged
        and still names any declared failure; a complete answer may be degraded."""
        if self.declared_engines is None:
            return not self.degraded
        return len(self.declared_answering) >= min(
            MIN_DECLARED_ANSWERING, len(self.declared_engines)
        )


class SearchProvider(Protocol):
    async def search(self, query: str, *, limit: int = 10) -> SearchResponse: ...


def declare(engines: Sequence[str] | None) -> tuple[str, ...] | None:
    """The declared engine set in its one form: lower-case, sorted, distinct. None is "not
    declared"; an empty or blank declaration is a programming error, not a way to say "none"."""
    if engines is None:
        return None
    names = tuple(sorted({e.strip().casefold() for e in engines}))
    if not names or "" in names:
        raise ValueError("a declared engine set must name at least one engine, none blank")
    return names


def split_failures(
    failed: Sequence[EngineFailure], declared: tuple[str, ...] | None
) -> tuple[tuple[EngineFailure, ...], tuple[EngineFailure, ...]]:
    """(failures that count, failures outside the declared set). With nothing declared every
    failure counts."""
    if declared is None:
        return tuple(failed), ()
    counted = tuple(f for f in failed if f.engine.casefold() in declared)
    return counted, tuple(f for f in failed if f.engine.casefold() not in declared)


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

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient,
        *,
        declared_engines: Sequence[str] | None,
    ) -> None:
        """`declared_engines` has no default: whoever builds the provider says, from
        `research.declared_engines`, which engines the configuration is declared to be (None:
        not declared yet)."""
        self._endpoint = base_url.rstrip("/") + "/search"
        self._client = client
        self._declared = declare(declared_engines)

    async def search(self, query: str, *, limit: int = 10) -> SearchResponse:
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
            body = response.json()
            rows = body["results"]
        except (ValueError, KeyError, TypeError) as e:
            raise SearchUnavailable("SearXNG did not answer with a result list") from e
        if not isinstance(rows, list):
            raise SearchUnavailable("SearXNG did not answer with a result list")
        found = [r for r in (_parse_row(r) for r in rows) if r is not None][:limit]
        failed, undeclared = split_failures(
            _failed_engines(body.get("unresponsive_engines")), self._declared
        )
        if not found:  # an engine outside the declared set failing says nothing about an absence
            _refuse_if_engines_failed(failed)
        return SearchResponse(found, failed, undeclared, self._declared)


def _failed_engines(unresponsive: Any) -> tuple[EngineFailure, ...]:
    """The engines SearXNG names as not having answered (`[[engine, reason], ...]`); anything that
    is not that shape is ignored, as a malformed result row is."""
    return tuple(
        EngineFailure(str(row[0]), str(row[1]))
        for row in (unresponsive if isinstance(unresponsive, list) else [])
        if isinstance(row, list) and len(row) == 2
    )


RATE_REFUSAL = "too many requests"


def is_rate_refusal(failure: EngineFailure) -> bool:
    """Whether an engine failure is the engine saying "slow down" rather than a service fault.

    THE ONE TEST, used everywhere that distinguishes the two (the refusal below, and the recorder's
    pacer: `docs/evaluation-plan.md`, "The pacer backs off for a rate refusal only"). SearXNG
    reports both a fresh refusal ("too many requests") and the suspension that follows one
    ("Suspended: too many requests") with this text; a CAPTCHA, a timeout, an access denial or a
    parsing error is a fault, not a refusal."""
    return RATE_REFUSAL in failure.reason.casefold()


def rate_refusing_engines(failed: Iterable[EngineFailure]) -> tuple[str, ...]:
    """The engines among `failed` that refused the rate, in order; empty when none did."""
    return tuple(f.engine for f in failed if is_rate_refusal(f))


def _refuse_if_engines_failed(failed: tuple[EngineFailure, ...]) -> None:
    """A real SearXNG whose upstream engines are throttled or CAPTCHA'd answers HTTP 200 with an
    empty result list and names the engines in `unresponsive_engines` (observed 2026-10-26,
    docs/searxng.md). Zero results with a failed engine is not a search with no hits: it cannot be
    told apart from one, so it is refused rather than reported as an absence. Results with a failed
    engine are returned, with the failure, by the caller."""
    if not failed:
        return
    named = ", ".join(f"{f.engine}: {f.reason}" for f in failed)
    if rate_refusing_engines(failed):
        raise SearchRateLimited(f"SearXNG's engines are rate-limited ({named})")
    raise SearchUnavailable(f"SearXNG's engines did not answer ({named})")


def _parse_row(row: Any) -> SearchResult | None:
    if not isinstance(row, Mapping):
        return None
    url, title = row.get("url"), row.get("title")
    if not isinstance(url, str) or not re.match(r"^https?://[^\s/]+", url):
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    snippet, engine, published = row.get("content"), row.get("engine"), row.get("publishedDate")
    engines = row.get("engines")
    return SearchResult(
        url=url,
        title=title.strip(),
        snippet=snippet if isinstance(snippet, str) else "",
        engine=engine if isinstance(engine, str) else None,
        published=published if isinstance(published, str) else None,
        engines=tuple(e for e in engines if isinstance(e, str))
        if isinstance(engines, list)
        else (),
    )


class OfflineSearch:
    """Canned results for CI: no network, deterministic, and every call is recorded.

    A query with no entry returns no results, as a real search with no hits would. `fail_with`
    makes every call raise it, for the partial-result paths. `failed_engines` is reported on every
    answer that has results, as a real engine reports a throttled upstream beside the others'.
    """

    def __init__(
        self,
        results: Mapping[str, Sequence[SearchResult]] | None = None,
        *,
        fail_with: SearchUnavailable | SearchRateLimited | None = None,
        failed_engines: Sequence[EngineFailure] = (),
        declared_engines: Sequence[str] | None = None,
    ) -> None:
        self._results = {normalise_query(q): list(rs) for q, rs in (results or {}).items()}
        self._fail_with = fail_with
        self._declared = declare(declared_engines)
        self._failed, self._undeclared = split_failures(failed_engines, self._declared)
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, limit: int = 10) -> SearchResponse:
        text = check_arguments(query, limit)
        self.calls.append((text, limit))
        if self._fail_with is not None:
            raise self._fail_with
        found = list(self._results.get(normalise_query(text), []))[:limit]
        if not found:
            _refuse_if_engines_failed(self._failed)
        return SearchResponse(found, self._failed, self._undeclared, self._declared)


def searxng_from_settings(
    settings: Settings, client: httpx.AsyncClient, *, declared_engines: Sequence[str] | None
) -> SearxngSearch:
    """The real provider, from `SEARXNG_URL`. Unset is `SearchUnavailable`: research degrades to a
    partial result rather than failing the run (Architecture.md §11). `declared_engines` is
    `research.declared_engines` (None until the probe has decided it)."""
    if settings.searxng_url is None:
        raise SearchUnavailable("SEARXNG_URL is not set")
    return SearxngSearch(str(settings.searxng_url), client, declared_engines=declared_engines)
