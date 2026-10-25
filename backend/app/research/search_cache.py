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

`mode="bypass"` neither reads nor writes the cache; the ledger still records every fetch. Whether
evaluation runs use the cache at all is a decision recorded in docs/evaluation-plan.md, not here.

The cache is an optimisation and never a reason for a run to fail: a Redis error on read or write
is treated as a miss, the search goes to the engine, and the ledger notes the cache error. An
engine error is not cached and propagates unchanged.
"""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict
from redis.exceptions import RedisError

from app.research.search import (
    SearchProvider,
    SearchResult,
    check_arguments,
    normalise_query,
)

CacheMode = Literal["use", "bypass"]
Outcome = Literal["reused", "fetched", "failed"]
KEY_PREFIX = "search_cache:v1:"


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


class SearchEvidenceReport(BaseModel):
    """What a run's web evidence was made of: the figure the evaluation compares between arms."""

    model_config = ConfigDict(frozen=True)

    mode: CacheMode
    ttl_s: int  # the TTL in force for the whole run
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
    records: list[SearchRecord]


class SearchLedger:
    def __init__(self, *, mode: CacheMode, ttl_s: int) -> None:
        self.mode, self.ttl_s = mode, ttl_s
        self.records: list[SearchRecord] = []

    def report(self) -> SearchEvidenceReport:
        fetched = [r for r in self.records if r.outcome == "fetched"]
        reused = [r for r in self.records if r.outcome == "reused"]
        n_fetched, n_reused = sum(r.results for r in fetched), sum(r.results for r in reused)
        return SearchEvidenceReport(
            mode=self.mode,
            ttl_s=self.ttl_s,
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
            records=list(self.records),
        )


def cache_key(query: str) -> str:
    return KEY_PREFIX + hashlib.sha256(normalise_query(query).encode("utf-8")).hexdigest()


class CachedSearch:
    """A SearchProvider that reuses results for up to `ttl_s`. One instance per run."""

    def __init__(
        self,
        inner: SearchProvider,
        redis: KeyValue,
        *,
        ttl_s: int,
        mode: CacheMode = "use",
        clock: Callable[[], float] = time.time,
    ) -> None:
        if ttl_s < 1:
            raise ValueError("ttl_s must be at least 1 second")
        self._inner, self._redis, self._ttl_s, self._mode, self._clock = (
            inner,
            redis,
            ttl_s,
            mode,
            clock,
        )
        self.ledger = SearchLedger(mode=mode, ttl_s=ttl_s)

    def report(self) -> SearchEvidenceReport:
        return self.ledger.report()

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        text = check_arguments(query, limit)
        normalised = normalise_query(text)
        cache_error = False
        if self._mode == "use":
            hit, cache_error = await self._read(text, limit)
            if hit is not None:
                results, age = hit
                self._note(normalised, "reused", len(results), age_s=age)
                return results
        try:
            results = await self._inner.search(text, limit=limit)
        except Exception:
            self._note(normalised, "failed", 0, cache_error=cache_error)
            raise
        if self._mode == "use":
            cache_error = await self._write(text, limit, results) or cache_error
        self._note(normalised, "fetched", len(results), cache_error=cache_error)
        return results

    def _note(
        self,
        query: str,
        outcome: Outcome,
        results: int,
        *,
        age_s: float | None = None,
        cache_error: bool = False,
    ) -> None:
        self.ledger.records.append(
            SearchRecord(
                query=query, outcome=outcome, results=results, age_s=age_s, cache_error=cache_error
            )
        )

    async def _read(
        self, query: str, limit: int
    ) -> tuple[tuple[list[SearchResult], float] | None, bool]:
        """((results, age) or None, whether Redis failed). An entry is usable only if it is within
        the TTL in force, was fetched with at least this `limit`, and parses; anything else is a
        miss."""
        try:
            raw = await self._redis.get(cache_key(query))
        except RedisError:
            return None, True
        if raw is None:
            return None, False
        try:
            entry = json.loads(raw)
            age = self._clock() - float(entry["fetched_at"])
            stored = [SearchResult(**row) for row in entry["results"]]
            fetched_limit = int(entry["limit"])
        except (ValueError, KeyError, TypeError):  # corrupt: replaced by the fetch that follows
            return None, False
        if not 0 <= age <= self._ttl_s or fetched_limit < limit:
            return None, False
        return (stored[:limit], age), False

    async def _write(self, query: str, limit: int, results: list[SearchResult]) -> bool:
        """Whether Redis failed. The entry carries its own fetch time: age is measured from it."""
        entry = {
            "query": normalise_query(query),
            "fetched_at": self._clock(),
            "limit": limit,
            "results": [asdict(r) for r in results],
        }
        try:
            await self._redis.set(cache_key(query), json.dumps(entry), ex=self._ttl_s)
        except RedisError:
            return True
        return False
