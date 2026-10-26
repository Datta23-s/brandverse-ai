"""The zero-retention page cache (Architecture.md section 6; Phases.md Phase 5): a wrapper around
any `PageSource`.

Raw fetched page bodies live in Redis under a TTL and **nowhere else**: nothing in this module, or
in the fetcher, writes to Postgres, and the entry holds the page and its fetch metadata and nothing
derived from it. Only extracted, scored, attributed findings persist, and those are built later.

**The TTL is a parameter, not a constant**, from `config/scoring.yaml` `research.page_cache_ttl_s`,
passed in; nothing here has a default (the same discipline as the search cache). An entry older than
the TTL in force is a miss even if Redis still holds it. Every `CachedPages` keeps a ledger of the
mode and TTL in force and, for every fetch, whether the body was fetched, reused (with the age of
the entry used) or failed (with the kind of failure), and `report()` states it, so what a run's
pages were made of can be compared between arms.

`mode="bypass"` neither reads nor writes. A fetch error is never cached, and the cache is never a
reason for a run to fail: a Redis error on read or write is a miss and is counted.
"""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Literal, Protocol
from urllib.parse import urldefrag

from pydantic import BaseModel, ConfigDict
from redis.exceptions import RedisError

from app.errors import FetchError
from app.research.fetch import FetchedPage, PageSource

CacheMode = Literal["use", "bypass"]
Outcome = Literal["reused", "fetched", "failed"]
KEY_PREFIX = "page_cache:v1:"


class KeyValue(Protocol):
    async def get(self, name: str, /) -> bytes | str | None: ...

    async def set(self, name: str, value: str, /, *, ex: int) -> object: ...


class PageRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    outcome: Outcome
    bytes: int = 0  # the body's size in characters as held; 0 when failed
    age_s: float | None = None  # reused only
    error: str | None = None  # failed only: the error class, e.g. FetchRefused
    kind: str | None = None  # failed only: why, e.g. "robots", "pdf", "address", "http_status"
    cache_error: bool = False


class PageEvidenceReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: CacheMode
    ttl_s: int
    fetches: int
    fetched: int
    reused: int
    failed: int
    failures_by_kind: dict[str, int]
    reused_share: float | None  # reused / (fetched + reused); None if neither
    oldest_reused_age_s: float | None
    cache_errors: int
    records: list[PageRecord]


def cache_key(url: str) -> str:
    """The URL without its fragment (which the server never sees), hashed."""
    return KEY_PREFIX + hashlib.sha256(urldefrag(url.strip())[0].encode("utf-8")).hexdigest()


class CachedPages:
    """A PageSource that reuses fetched pages for up to `ttl_s`. One instance per run."""

    def __init__(
        self,
        inner: PageSource,
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
        self.mode, self.ttl_s = mode, ttl_s
        self.records: list[PageRecord] = []

    async def fetch(self, url: str) -> FetchedPage:
        cache_error = False
        if self._mode == "use":
            hit, cache_error = await self._read(url)
            if hit is not None:
                page, age = hit
                self.records.append(
                    PageRecord(url=url, outcome="reused", bytes=len(page.html), age_s=age)
                )
                return page
        try:
            page = await self._inner.fetch(url)
        except FetchError as e:
            self.records.append(
                PageRecord(
                    url=url,
                    outcome="failed",
                    error=type(e).__name__,
                    kind=e.kind,
                    cache_error=cache_error,
                )
            )
            raise
        if self._mode == "use":
            cache_error = await self._write(url, page) or cache_error
        self.records.append(
            PageRecord(url=url, outcome="fetched", bytes=len(page.html), cache_error=cache_error)
        )
        return page

    def report(self) -> PageEvidenceReport:
        fetched = [r for r in self.records if r.outcome == "fetched"]
        reused = [r for r in self.records if r.outcome == "reused"]
        failed = [r for r in self.records if r.outcome == "failed"]
        kinds: dict[str, int] = {}
        for r in failed:
            label = f"{r.error}:{r.kind}" if r.kind else str(r.error)
            kinds[label] = kinds.get(label, 0) + 1
        return PageEvidenceReport(
            mode=self.mode,
            ttl_s=self.ttl_s,
            fetches=len(self.records),
            fetched=len(fetched),
            reused=len(reused),
            failed=len(failed),
            failures_by_kind=dict(sorted(kinds.items())),
            reused_share=len(reused) / (len(fetched) + len(reused)) if fetched or reused else None,
            oldest_reused_age_s=max((r.age_s or 0.0 for r in reused), default=None),
            cache_errors=sum(1 for r in self.records if r.cache_error),
            records=list(self.records),
        )

    async def _read(self, url: str) -> tuple[tuple[FetchedPage, float] | None, bool]:
        """((page, age) or None, whether Redis failed). Usable only within the TTL in force and if
        it parses; anything else is a miss."""
        try:
            raw = await self._redis.get(cache_key(url))
        except RedisError:
            return None, True
        if raw is None:
            return None, False
        try:
            page = FetchedPage(**json.loads(raw))
            age = self._clock() - page.fetched_at
        except (ValueError, TypeError):  # corrupt: replaced by the fetch that follows
            return None, False
        if not 0 <= age <= self._ttl_s:
            return None, False
        return (page, age), False

    async def _write(self, url: str, page: FetchedPage) -> bool:
        """Whether Redis failed. The entry is the page as fetched, with its own fetch time."""
        try:
            await self._redis.set(cache_key(url), json.dumps(asdict(page)), ex=self._ttl_s)
        except RedisError:
            return True
        return False
