"""The search-result cache (Rules.md C7; Phases.md Phase 5): a wrapper around any SearchProvider.

Results are cached by normalised query in Redis, with a TTL, and nothing else: no page body is
stored here (the zero-retention page cache is a separate piece, and Postgres never holds either).

**The TTL is a parameter, not a constant.** It comes from `config/scoring.yaml`
`research.search_cache_ttl_s` and is passed in; nothing in this module has a default. Search
results change and the thesis measures freshness, so an entry reused across days silently changes
what a run saw. Three consequences are built in:
- an entry older than the TTL in force is a miss even if Redis still holds it, so lowering the
  setting takes effect at once on entries written under a longer one;
- every `CachedSearch` keeps a `SearchLedger`: the mode and TTL in force, and for every search
  whether it was fetched, reused (with the age of the entry used) or failed;
- `report()` states how much of a run's evidence was fetched and how much was reused, in searches
  and in results, which the evaluation compares between arms.

**A degraded answer has its own, much shorter TTL** (`research.search_cache_degraded_ttl_s`, also
passed in, never longer than the other; owner, 2026-10-26). A search answered while some engines
failed is a transient failure, and caching it for a day would let one bad minute poison a day of
runs. It is cached for the short TTL, still marked degraded. This is the development cache. The
evaluation's snapshot does not hold a degraded search at all (docs/evaluation-plan.md section 8);
it is retried later.

`mode="bypass"` neither reads nor writes the cache; the ledger still records every fetch. Whether
evaluation runs use the cache at all is a decision recorded in docs/evaluation-plan.md, not here.

The cache is an optimisation and never a reason for a run to fail: a Redis error on read or write
is treated as a miss, the search goes to the engine, and the ledger notes the cache error. An
engine error is not cached and propagates unchanged.
"""

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict
from redis.exceptions import RedisError

from app.research.search import (
    EngineFailure,
    SearchProvider,
    SearchResponse,
    SearchResult,
    check_arguments,
    declare,
    normalise_query,
)

CacheMode = Literal["use", "bypass"]
Outcome = Literal["reused", "fetched", "failed"]
# v2: an entry carries the engines that failed when it was fetched. A v1 entry has no such record
# and would read as "not degraded", which nobody knows, so v1 entries are never read.
# v3: an entry also carries the declared engine set it was fetched under, and is reused only under
# the same set; a v2 entry does not say, so it is never read either.
KEY_PREFIX = "search_cache:v3:"


class KeyValue(Protocol):
    """The two Redis commands the cache uses (redis.asyncio.Redis satisfies it)."""

    async def get(self, name: str, /) -> bytes | str | None: ...

    async def set(self, name: str, value: str, /, *, ex: int) -> object: ...


class SearchRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str  # normalised
    outcome: Outcome
    results: int  # how many results the caller received (0 when failed)
    age_s: float | None = None  # reused only: how old the entry was
    cache_error: bool = False  # Redis failed on this search and the cache was skipped
    # Degradation (owner, 2026-10-26): the engines the search engine named as not having answered
    # when this answer was fetched (a reused entry keeps its fetch-time failures), as
    # (engine, reason), and the engines that contributed a returned result. A search that saw one
    # engine is not the same evidence as one that saw three.
    failed_engines: tuple[tuple[str, str], ...] = ()  # failures of DECLARED engines only
    engines_seen: tuple[str, ...] = ()
    # Failures of engines outside the declared set: kept, and not degradation (section 8).
    undeclared_failures: tuple[tuple[str, str], ...] = ()
    undeclared_seen: tuple[str, ...] = ()  # outside the set, and returned a result anyway


class SearchEvidenceReport(BaseModel):
    """What a run's web evidence was made of: the figure the evaluation compares between arms."""

    model_config = ConfigDict(frozen=True)

    mode: CacheMode
    ttl_s: int  # the TTL in force for the whole run
    degraded_ttl_s: int  # the (shorter) TTL for an answer with failed engines
    searches: int
    fetched: int
    reused: int
    failed: int
    results_fetched: int
    results_reused: int
    reused_share_of_searches: float | None  # reused / (fetched + reused); None if neither
    reused_share_of_results: float | None
    oldest_reused_age_s: float | None
    cache_errors: int
    degraded_searches: int  # answered (fetched or reused) with at least one engine named as failed
    failed_engine_counts: dict[str, int]  # engine -> in how many answered searches it had failed
    engines_seen: tuple[str, ...]  # every engine that contributed a result to any answered search
    # The configuration "degraded" is measured against, declared in advance (None: not declared, so
    # every engine counted). Reported beside every result (docs/evaluation-plan.md section 8).
    declared_engines: tuple[str, ...] | None
    undeclared_failure_counts: dict[str, int]  # engines outside the set that failed, kept apart
    undeclared_engines_seen: tuple[str, ...]  # outside the set, yet returned a result: a mismatch
    records: list[SearchRecord]


class SearchLedger:
    def __init__(
        self,
        *,
        mode: CacheMode,
        ttl_s: int,
        degraded_ttl_s: int,
        declared_engines: tuple[str, ...] | None,
    ) -> None:
        self.mode, self.ttl_s, self.degraded_ttl_s = mode, ttl_s, degraded_ttl_s
        self.declared_engines = declared_engines
        self.records: list[SearchRecord] = []

    def report(self) -> SearchEvidenceReport:
        fetched = [r for r in self.records if r.outcome == "fetched"]
        reused = [r for r in self.records if r.outcome == "reused"]
        n_fetched, n_reused = sum(r.results for r in fetched), sum(r.results for r in reused)
        answered = fetched + reused
        failed_counts: dict[str, int] = {}
        undeclared_counts: dict[str, int] = {}
        for r in answered:
            for engine in {f[0] for f in r.failed_engines}:
                failed_counts[engine] = failed_counts.get(engine, 0) + 1
            for engine in {f[0] for f in r.undeclared_failures}:
                undeclared_counts[engine] = undeclared_counts.get(engine, 0) + 1
        return SearchEvidenceReport(
            mode=self.mode,
            ttl_s=self.ttl_s,
            degraded_ttl_s=self.degraded_ttl_s,
            searches=len(self.records),
            fetched=len(fetched),
            reused=len(reused),
            failed=sum(1 for r in self.records if r.outcome == "failed"),
            results_fetched=n_fetched,
            results_reused=n_reused,
            reused_share_of_searches=(
                len(reused) / (len(fetched) + len(reused)) if fetched or reused else None
            ),
            reused_share_of_results=(
                n_reused / (n_fetched + n_reused) if n_fetched or n_reused else None
            ),
            oldest_reused_age_s=max((r.age_s or 0.0 for r in reused), default=None),
            cache_errors=sum(1 for r in self.records if r.cache_error),
            degraded_searches=sum(1 for r in answered if r.failed_engines),
            failed_engine_counts=dict(sorted(failed_counts.items())),
            engines_seen=tuple(sorted({e for r in answered for e in r.engines_seen})),
            declared_engines=self.declared_engines,
            undeclared_failure_counts=dict(sorted(undeclared_counts.items())),
            undeclared_engines_seen=tuple(sorted({e for r in answered for e in r.undeclared_seen})),
            records=list(self.records),
        )


def cache_key(query: str, declared: tuple[str, ...] | None = None) -> str:
    """The normalised query under the declared engine set (None: undeclared). The set is part of
    the key so runs under different sets keep separate entries instead of overwriting each other;
    the entry also records it, and a mismatch on read is still a miss."""
    scope = "*" if declared is None else ",".join(declared)
    digest = hashlib.sha256(f"{scope}\n{normalise_query(query)}".encode()).hexdigest()
    return KEY_PREFIX + digest


class CachedSearch:
    """A SearchProvider that reuses results for up to `ttl_s`. One instance per run."""

    def __init__(
        self,
        inner: SearchProvider,
        redis: KeyValue,
        *,
        ttl_s: int,
        degraded_ttl_s: int,
        declared_engines: Sequence[str] | None,
        mode: CacheMode = "use",
        clock: Callable[[], float] = time.time,
    ) -> None:
        """`declared_engines` is `research.declared_engines` and has no default; it must be the set
        the inner provider was built with (a mismatch is refused on the first search)."""
        if ttl_s < 1 or degraded_ttl_s < 1:
            raise ValueError("ttl_s and degraded_ttl_s must each be at least 1 second")
        if degraded_ttl_s > ttl_s:
            raise ValueError("degraded_ttl_s must not exceed ttl_s")
        self._declared = declare(declared_engines)
        self._inner, self._redis, self._ttl_s, self._mode, self._clock = (
            inner,
            redis,
            ttl_s,
            mode,
            clock,
        )
        self._degraded_ttl_s = degraded_ttl_s
        self.ledger = SearchLedger(
            mode=mode, ttl_s=ttl_s, degraded_ttl_s=degraded_ttl_s, declared_engines=self._declared
        )

    def report(self) -> SearchEvidenceReport:
        return self.ledger.report()

    async def search(self, query: str, *, limit: int = 10) -> SearchResponse:
        text = check_arguments(query, limit)
        normalised = normalise_query(text)
        cache_error = False
        if self._mode == "use":
            hit, cache_error = await self._read(text, limit)
            if hit is not None:
                response, age = hit
                self._note(normalised, "reused", response, age_s=age)
                return response
        try:
            response = await self._inner.search(text, limit=limit)
        except Exception:
            self._note(normalised, "failed", None, cache_error=cache_error)
            raise
        if response.declared_engines != self._declared:
            raise ValueError(
                f"the provider answered under declared engines {response.declared_engines}, "
                f"the run declares {self._declared}: they must be the same set"
            )
        if self._mode == "use":
            cache_error = await self._write(text, limit, response) or cache_error
        self._note(normalised, "fetched", response, cache_error=cache_error)
        return response

    def _note(
        self,
        query: str,
        outcome: Outcome,
        response: SearchResponse | None,
        *,
        age_s: float | None = None,
        cache_error: bool = False,
    ) -> None:
        self.ledger.records.append(
            SearchRecord(
                query=query,
                outcome=outcome,
                results=len(response.results) if response else 0,
                age_s=age_s,
                cache_error=cache_error,
                failed_engines=tuple((f.engine, f.reason) for f in response.failed_engines)
                if response
                else (),
                engines_seen=response.engines_seen if response else (),
                undeclared_failures=tuple(
                    (f.engine, f.reason) for f in response.undeclared_failures
                )
                if response
                else (),
                undeclared_seen=response.undeclared_seen if response else (),
            )
        )

    async def _read(
        self, query: str, limit: int
    ) -> tuple[tuple[SearchResponse, float] | None, bool]:
        """((response, age) or None, whether Redis failed). An entry is usable only if it is within
        the TTL in force, was fetched with at least this `limit`, and parses; anything else is a
        miss. The response carries the engines that had failed when the entry was fetched."""
        try:
            raw = await self._redis.get(cache_key(query, self._declared))
        except RedisError:
            return None, True
        if raw is None:
            return None, False
        try:
            entry = json.loads(raw)
            age = self._clock() - float(entry["fetched_at"])
            stored = [
                SearchResult(**{**row, "engines": tuple(row.get("engines", ()))})
                for row in entry["results"]
            ]
            failed = tuple(EngineFailure(str(e), str(r)) for e, r in entry["failed_engines"])
            undeclared = tuple(
                EngineFailure(str(e), str(r)) for e, r in entry["undeclared_failures"]
            )
            stored_declared = declare(entry["declared_engines"])
            fetched_limit = int(entry["limit"])
        except (ValueError, KeyError, TypeError):  # corrupt: replaced by the fetch that follows
            return None, False
        if stored_declared != self._declared:  # fetched under another configuration: not this run's
            return None, False
        usable_for = self._degraded_ttl_s if failed else self._ttl_s  # the TTL in force for it
        if not 0 <= age <= usable_for or fetched_limit < limit:
            return None, False
        return (SearchResponse(stored[:limit], failed, undeclared, stored_declared), age), False

    async def _write(self, query: str, limit: int, response: SearchResponse) -> bool:
        """Whether Redis failed. The entry carries its own fetch time: age is measured from it."""
        entry = {
            "query": normalise_query(query),
            "fetched_at": self._clock(),
            "limit": limit,
            "results": [asdict(r) for r in response.results],
            "failed_engines": [[f.engine, f.reason] for f in response.failed_engines],
            "undeclared_failures": [[f.engine, f.reason] for f in response.undeclared_failures],
            "declared_engines": list(response.declared_engines)
            if response.declared_engines is not None
            else None,
        }
        expires_in = self._degraded_ttl_s if response.failed_engines else self._ttl_s
        try:
            await self._redis.set(
                cache_key(query, self._declared), json.dumps(entry), ex=expires_in
            )
        except RedisError:
            return True
        return False
