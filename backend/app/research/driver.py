"""The research driver (Architecture.md 6.2, docs/evaluation-plan.md section 8): the planner's
queries in, scored attributed findings out.

    search each query -> dedupe by canonical URL -> fetch -> extract -> score -> keep or discard

It is ONE piece of code run live or replayed: the search provider and the page source are injected
behind their protocols, so the evaluation's recording runs it against the live web and the arms run
it against the frozen snapshot, and nothing here knows which. The arms never need the network.

**What it takes from the plan.** `plan.for_depth(depth)`: D0 searches nothing, D1 the first pass, D2
the whole ranked list. The cap on pages considered per query is the plan's `max_pages_per_query`, a
safety bound that never binds in normal operation.

**Two modes that want opposite things (owner, 2026-10-28). The driver is told which it is in, and
there is no default.** `Mode.PRODUCTION` follows `Rules.md` C4: degrade to partial rather than
fail. A throttled search (`SearchRateLimited`) stops the remaining queries, because asking an
engine that has just refused is the thing most likely to prolong the throttle, and they are
reported `not_attempted`; an unavailable search is recorded and the next query goes on. A run that
loses a facet to a throttle yields findings from the rest and DECLARES the gap (`uncovered_facets`)
instead of yielding nothing. `Mode.RECORD` is the evaluation snapshot's capture: a request is
committed whole or not at all, so ANY search that is throttled, unavailable or degraded raises
`RequestAborted` at once; fetching pages for a request that will be discarded and redone is wasted
work, and more load on an engine that is already refusing. In both modes a page that cannot be
fetched, a page with too little main text, and a page scoring below the acceptance threshold are
counted by reason and never reach an agent. Every outcome is reported, because a run that saw 40
pages and one that saw 4 are different evidence (the same discipline as the search and page
ledgers).

**Counts are the result.** `searches_issued` and `pages_fetched` are what SRR and the web part of LR
are computed from (LR is derived as time from these counts and recorded unit costs, never observed:
docs/evaluation-plan.md section 8). They count requests the driver made to its sources, whether the
source then served them live or from a snapshot.

**Zero retention.** The outcome holds extracted text, scores and attribution, never a raw page body.

**`first_party` is passed through, unpopulated and unread (V6).**
"""

import asyncio
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Literal
from urllib.parse import urlsplit

from app.core.first_party import is_first_party
from app.errors import (
    BrandverseError,
    ExtractionFailed,
    FetchError,
    SearchRateLimited,
    SearchUnavailable,
)
from app.memory.scoring import SourceScoring
from app.research.extraction import ExtractedPage, Extractor
from app.research.fetch import PageSource
from app.research.planner import Depth, PlannedQuery, QueryPlan
from app.research.search import SearchProvider, SearchResult
from app.research.source_scoring import SourceScore, score_source

QueryOutcome = Literal["ok", "rate_limited", "unavailable", "not_attempted"]


class Mode(StrEnum):
    """What the run is FOR. There is no default: the two want opposite things on a failed search."""

    PRODUCTION = "production"  # degrade to partial, declare the gap (Rules.md C4)
    RECORD = "record"  # the evaluation snapshot's capture: whole request or nothing


class RequestAborted(BrandverseError):
    """RECORD mode only: a search failed or came back degraded, so the request cannot be captured
    whole and the capture is to be discarded and redone later. Carries what was seen so far, for
    the log; nothing in it may be committed."""

    def __init__(self, message: str, *, outcome: "ResearchOutcome") -> None:
        super().__init__(message)
        self.outcome = outcome


def canonical_url(url: str) -> str:
    """The form two results for the same page share: scheme, a leading `www.`, the fragment and a
    trailing slash do not distinguish pages. The query string does (it can select a page)."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").casefold().removeprefix("www.")
    port = f":{parts.port}" if parts.port not in (None, 80, 443) else ""
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{port}{parts.path.rstrip('/')}{query}"


@dataclass(frozen=True, slots=True)
class QueryRun:
    query: PlannedQuery
    outcome: QueryOutcome
    results: int = 0  # what the search returned
    considered: int = 0  # after the per-query cap
    degraded: bool = False  # a declared engine was named as failed in this answer
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PageFailure:
    url: str
    stage: Literal["fetch", "extract"]
    error: str  # the error class, e.g. FetchFailed
    kind: str | None  # why, e.g. "http_status", "robots", "pdf", "timeout"


@dataclass(frozen=True, slots=True)
class Finding:
    """One accepted page: what an agent may reason over, with where it came from."""

    page: ExtractedPage
    score: SourceScore
    queries: tuple[int, ...]  # ranks of every planned query that returned this URL
    engines: tuple[str, ...]
    # Whether the page is the brand's own domain (V6): populated from the brand's registered
    # domains, None if it has none. Read by A2 (own pages are not competitors) and the claim gate's
    # sole-evidence rule; NOT folded into the source score (authority and independence stay apart).
    first_party: bool | None = None


@dataclass(slots=True)
class ResearchOutcome:
    depth: Depth
    plan_report: dict[str, object]
    queries: list[QueryRun] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)  # accepted, best score first
    failures: list[PageFailure] = field(default_factory=list)
    results_seen: int = 0  # URLs the searches returned, before dedupe and the cap
    distinct_urls: int = 0  # after dedupe by canonical URL
    pages_fetched: int = 0  # fetches requested of the page source (served or failed)
    pages_served: int = 0  # of those, the ones the source returned a page for
    pages_extracted: int = 0  # of those, the ones with enough main text
    extraction_refused: int = 0
    discarded_low_score: int = 0

    @property
    def searches_issued(self) -> int:
        return sum(1 for q in self.queries if q.outcome != "not_attempted")

    @property
    def uncovered_facets(self) -> tuple[str, ...]:
        """The facets with no successful query: the gap a production run DECLARES. A facet whose
        queries all failed or were not attempted contributed nothing, whatever the others did."""
        asked = {q.query.facet for q in self.queries}
        covered = {q.query.facet for q in self.queries if q.outcome == "ok"}
        return tuple(sorted(asked - covered))

    @property
    def partial(self) -> bool:
        """True when anything the plan asked for did not complete."""
        return any(q.outcome != "ok" for q in self.queries)

    def failures_by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.failures:
            key = f"{f.stage}:{f.error}" + (f"/{f.kind}" if f.kind else "")
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))


def _first_party(result_url: str, final_url: str, domains: Collection[str]) -> bool | None:
    """V6: None if the brand registered no domain (unknown is not independent), True if the
    result or the page it resolved to is the brand's own, else False."""
    if not domains:
        return None
    return bool(is_first_party(result_url, domains) or is_first_party(final_url, domains))


@dataclass(slots=True)
class _Candidate:
    result: SearchResult
    ranks: list[int]
    engines: set[str]


async def research(
    plan: QueryPlan,
    depth: Depth,
    *,
    mode: Mode,
    search: SearchProvider,
    pages: PageSource,
    extractor: Extractor,
    source_cfg: SourceScoring,
    today: date,
    concurrency: int = 8,
    search_limit: int = 50,
    brand_domains: Collection[str] = (),
) -> ResearchOutcome:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if not isinstance(mode, Mode):
        raise TypeError("mode must be a Mode (record or production), not a flag")
    outcome = ResearchOutcome(depth=depth, plan_report=plan.report())
    planned = plan.for_depth(depth)
    cap = plan.max_pages_per_query

    candidates: dict[str, _Candidate] = {}
    stopped = False
    for q in planned:
        if stopped:
            outcome.queries.append(QueryRun(q, "not_attempted"))
            continue
        try:
            response = await search.search(q.text, limit=search_limit)
        except SearchRateLimited as e:
            outcome.queries.append(QueryRun(q, "rate_limited", error=str(e)))
            if mode is Mode.RECORD:
                raise RequestAborted(f"query {q.rank} throttled: {e}", outcome=outcome) from e
            stopped = True  # do not ask an engine that has just refused
            continue
        except SearchUnavailable as e:
            outcome.queries.append(QueryRun(q, "unavailable", error=str(e)))
            if mode is Mode.RECORD:
                raise RequestAborted(f"query {q.rank} unavailable: {e}", outcome=outcome) from e
            continue
        if mode is Mode.RECORD and not response.complete:
            outcome.queries.append(QueryRun(q, "ok", len(response.results), degraded=True))
            raise RequestAborted(f"query {q.rank} answered degraded", outcome=outcome)
        considered = response.results if cap is None else response.results[:cap]
        outcome.queries.append(
            QueryRun(q, "ok", len(response.results), len(considered), degraded=response.degraded)
        )
        outcome.results_seen += len(considered)
        for result in considered:
            key = canonical_url(result.url)
            found = candidates.get(key)
            if found is None:
                found = candidates[key] = _Candidate(result, [], set())
            found.ranks.append(q.rank)
            found.engines.update(result.engines or ((result.engine,) if result.engine else ()))
    outcome.distinct_urls = len(candidates)

    gate = asyncio.Semaphore(concurrency)

    async def one(key: str, cand: _Candidate) -> Finding | PageFailure:
        async with gate:
            try:
                page = await pages.fetch(cand.result.url)
            except FetchError as e:
                return PageFailure(cand.result.url, "fetch", type(e).__name__, e.kind)
        try:
            extracted = await asyncio.to_thread(extractor.extract, page.html, page.final_url)
        except ExtractionFailed:
            return PageFailure(cand.result.url, "extract", "ExtractionFailed", None)
        query_text = next(q.text for q in planned if q.rank == min(cand.ranks))
        score = score_source(extracted, query_text, today=today, cfg=source_cfg)
        return Finding(
            page=extracted,
            score=score,
            queries=tuple(sorted(cand.ranks)),
            engines=tuple(sorted(cand.engines)),
            # the brand's own page by the search result's URL OR where the fetch ended up
            first_party=_first_party(cand.result.url, extracted.url, brand_domains),
        )

    outcome.pages_fetched = len(candidates)
    done = await asyncio.gather(*(one(k, c) for k, c in candidates.items()))
    accepted: list[Finding] = []
    for item in done:
        if isinstance(item, PageFailure):
            outcome.failures.append(item)
            if item.stage == "extract":
                outcome.pages_served += 1
                outcome.extraction_refused += 1
            continue
        outcome.pages_served += 1
        outcome.pages_extracted += 1
        if item.score.accepted:
            accepted.append(item)
        else:
            outcome.discarded_low_score += 1
    outcome.findings = sorted(accepted, key=lambda f: (-f.score.value, f.page.url))
    return outcome
