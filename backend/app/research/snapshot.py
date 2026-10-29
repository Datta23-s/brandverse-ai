"""The evaluation snapshot: search results AND page bodies, recorded once and replayed for every arm
(docs/evaluation-plan.md section 8; owner, 2026-10-25 and 2026-10-28).

**Why pages as well as searches.** The evidence an arm reasons over is the page, not the search
result. A TTL page cache would let arm C read a different page from arm B, so the held-constant
guarantee would cover search and not fetch. A replayed page is the same bytes, or the same frozen
failure, for every arm of a request and for every request that shares the URL.

**Two implementations of the existing protocols, so the driver is one piece of code run live or
replayed.** Recording wraps a live `SearchProvider` and `PageSource` and writes what they return;
replay (`SnapshotSearch`, `SnapshotPages`) only reads, never reaches the network, and raises
`SnapshotMiss` for anything that was not recorded (a replay that went live would silently end the
guarantee).

**The store is a directory on disk in an evaluation namespace**, and nowhere else. It is the one
scoped exception to the zero-retention rule (Architecture.md section 6): a directory that carries a
marker file, is OUTSIDE the repository (third-party pages), is retained on the laptop plus one
backup, and is not shared. `SnapshotStore` refuses a root without the marker, a root inside the
repository, and a non-empty directory it did not create. `find_raw_bodies` is the detector the
containment tests use to prove no raw body is written anywhere else.

**Write once, shared across requests.** An entry is never overwritten and never expires. Pages are
keyed by canonical URL for the whole recording, not per request: requests that share a query share
its URLs, and campaign 3 must read the same bytes as campaign 1 (the repeat-evidence ruling).

**A request is captured whole or not at all.** Its searches and pages are staged on disk; commit
moves the bodies, appends the entries, and appends the request's `committed` line LAST, with the
data fsynced first. A reader trusts only entries whose request has a committed line, so a crash at
any point leaves nothing visible, and the request is simply captured again.

**Failures are frozen, with a bound.** A permanent outcome (404, a robots refusal, a PDF) is frozen
as an outcome: every arm sees the same refusal, and a cited source returning 404 is a fact about
that source. A transient failure (timeout, network error, 5xx, 429) is retried at most
`research.snapshot_page_retries` more times, the attempts recorded, then frozen as failed. The share
frozen as failed is reported per request. A degraded search is never recorded (the driver aborts the
request in RECORD mode).

**Unit costs come from the recording itself.** Each search and page entry carries the seconds it
took live, which is where the derived web time of LR gets its inputs.
"""

import asyncio
import gzip
import hashlib
import json
import os
import shutil
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median
from typing import Any, Protocol

from app.errors import (
    BrandverseError,
    FetchError,
    FetchFailed,
    FetchRefused,
    FetchUnsupported,
    SearchRateLimited,
    SearchUnavailable,
)
from app.memory.scoring import SourceScoring
from app.research.driver import Mode, RequestAborted, canonical_url, research
from app.research.extraction import Extractor
from app.research.fetch import FetchedPage, PageSource
from app.research.planner import QueryPlan
from app.research.search import (
    SearchProvider,
    SearchResponse,
    SearchResult,
    declare,
    rate_refusing_engines,
)
from app.research.search_cache import cache_key

MARKER = ".evaluation-namespace"
REPO_ROOT = Path(__file__).resolve().parents[3]
TRANSIENT_KINDS = frozenset({"timeout", "network", "robots_unavailable"})


class SnapshotError(BrandverseError):
    pass


class NotAnEvaluationNamespace(SnapshotError):
    """The directory is not a store made for this: no marker, inside the repository, or not ours."""


class SnapshotMiss(SnapshotError):
    """A replay asked for something that was never recorded. Never answered from the network."""


def _utc() -> str:
    return datetime.now(UTC).isoformat()


def _append(path: Path, lines: Iterable[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for line in lines:
            f.write(json.dumps(line, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _read_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _span(stamps: list[str]) -> float | None:
    """Seconds from the earliest to the latest of sorted ISO timestamps; None if there are none."""
    if not stamps:
        return None
    return round(
        (datetime.fromisoformat(stamps[-1]) - datetime.fromisoformat(stamps[0])).total_seconds(), 1
    )


def is_transient(error: FetchError) -> bool:
    """A failure worth retrying: the server or the network, not a fact about the page."""
    if not isinstance(error, FetchFailed):
        return False
    status = getattr(error, "status", None)
    return error.kind in TRANSIENT_KINDS or (
        status is not None and (status >= 500 or status in (408, 429))
    )


class Staging:
    """One request's capture in progress. Nothing here is visible to a reader until commit."""

    def __init__(self, request_id: str, root: Path) -> None:
        self.request_id, self.root = request_id, root
        self.searches: dict[str, dict[str, Any]] = {}
        self.pages: dict[str, dict[str, Any]] = {}
        self.bodies = root / "bodies"
        self.bodies.mkdir(parents=True, exist_ok=True)

    def put_body(self, html: str) -> str:
        data = html.encode("utf-8")
        sha = hashlib.sha256(data).hexdigest()
        (self.bodies / f"{sha}.gz").write_bytes(gzip.compress(data, 6, mtime=0))
        return sha


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._check(self.root)
        if not (self.root / MARKER).is_file():
            raise NotAnEvaluationNamespace(f"{self.root} has no {MARKER}: not an evaluation store")
        self._load()

    @staticmethod
    def _check(root: Path) -> None:
        if root == REPO_ROOT or REPO_ROOT in root.parents:
            raise NotAnEvaluationNamespace(
                f"{root} is inside the repository: page bodies are third-party content and are "
                "never kept there (Architecture.md section 6)"
            )

    @classmethod
    def create(cls, root: Path) -> "SnapshotStore":
        root = root.resolve()
        cls._check(root)
        if root.exists() and any(root.iterdir()) and not (root / MARKER).is_file():
            raise NotAnEvaluationNamespace(f"{root} is not empty and was not made as a store")
        root.mkdir(parents=True, exist_ok=True)
        marker = root / MARKER
        if not marker.exists():
            marker.write_text(
                "Evaluation snapshot store: raw third-party page bodies. Not in the repository, "
                "not shared, retained on this laptop and one backup.\n",
                encoding="utf-8",
            )
        return cls(root)

    # ── reading ──────────────────────────────────────────────────────────────────────────────
    def _load(self) -> None:
        self.committed = {c["request"]: c for c in _read_lines(self.root / "committed.jsonl")}
        self._searches: dict[str, dict[str, Any]] = {}
        self._pages: dict[str, dict[str, Any]] = {}
        for entry in _read_lines(self.root / "searches.jsonl"):
            if entry["request"] in self.committed:  # an uncommitted line is a crashed capture
                self._searches.setdefault(entry["key"], entry)  # first wins: write once
        for entry in _read_lines(self.root / "pages.jsonl"):
            if entry["request"] in self.committed:
                self._pages.setdefault(entry["url"], entry)

    def search_entry(self, key: str) -> dict[str, Any] | None:
        return self._searches.get(key)

    def page_entry(self, canonical: str) -> dict[str, Any] | None:
        return self._pages.get(canonical)

    def body(self, sha: str) -> str:
        path = self.root / "bodies" / sha[:2] / f"{sha}.gz"
        return gzip.decompress(path.read_bytes()).decode("utf-8")

    def committed_requests(self) -> set[str]:
        return set(self.committed)

    def pending(self, request_ids: Iterable[str]) -> list[str]:
        """Resumable: what still has to be captured, in the given order."""
        return [r for r in request_ids if r not in self.committed]

    # ── writing: staged, then committed whole ────────────────────────────────────────────────
    def begin(self, request_id: str) -> Staging:
        if request_id in self.committed:
            raise SnapshotError(f"{request_id} is already committed: entries are write-once")
        staging_root = self.root / "staging" / request_id
        shutil.rmtree(staging_root, ignore_errors=True)  # a crashed earlier attempt
        return Staging(request_id, staging_root)

    def discard(self, staging: Staging) -> None:
        shutil.rmtree(staging.root, ignore_errors=True)

    def commit(self, staging: Staging, meta: dict[str, Any]) -> None:
        for body in staging.bodies.glob("*.gz"):
            target = self.root / "bodies" / body.name[:2] / body.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():  # content-addressed: the same bytes are already there
                body.unlink()
            else:
                body.replace(target)
        rid = staging.request_id
        _append(
            self.root / "searches.jsonl", ({**e, "request": rid} for e in staging.searches.values())
        )
        _append(self.root / "pages.jsonl", ({**e, "request": rid} for e in staging.pages.values()))
        _append(self.root / "committed.jsonl", [{"request": rid, "committed_utc": _utc(), **meta}])
        shutil.rmtree(staging.root, ignore_errors=True)
        self._load()

    def manifest_summary(self) -> dict[str, Any]:
        """What the repository records about the store: counts, a hash over the three index files,
        and the date range. Never a body."""
        digest = hashlib.sha256()
        for name in ("committed.jsonl", "searches.jsonl", "pages.jsonl"):
            path = self.root / name
            digest.update(path.read_bytes() if path.exists() else b"")
        stamps = sorted(c["committed_utc"] for c in self.committed.values())
        own = [
            c["search_span_s"]
            for c in self.committed.values()
            if c.get("search_span_s") is not None
        ]
        every = [
            c["search_span_all_s"]
            for c in self.committed.values()
            if c.get("search_span_all_s") is not None
        ]
        return {
            "search_span_s": {
                "max": max(own, default=None),
                "median": median(own) if own else None,
            },
            "search_span_all_s": {
                "max": max(every, default=None),
                "median": median(every) if every else None,
            },
            "requests": len(self.committed),
            "searches": len(self._searches),
            "pages": len(self._pages),
            "pages_ok": sum(1 for p in self._pages.values() if p["outcome"] == "ok"),
            "pages_frozen_failed": sum(1 for p in self._pages.values() if p["outcome"] == "failed"),
            "sha256": digest.hexdigest(),
            "first_committed_utc": stamps[0] if stamps else None,
            "last_committed_utc": stamps[-1] if stamps else None,
        }


# ── Replay ───────────────────────────────────────────────────────────────────────────────────


def _response(
    entry: dict[str, Any], limit: int, declared: tuple[str, ...] | None
) -> SearchResponse:
    results = [
        SearchResult(
            url=r["url"],
            title=r["title"],
            snippet=r["snippet"],
            engine=r["engine"],
            published=r["published"],
            engines=tuple(r["engines"]),
            first_party=r.get("first_party"),
        )
        for r in entry["results"]
    ]
    return SearchResponse(results[:limit], declared_engines=declared)


class SnapshotSearch:
    """Replays recorded searches. Never reaches the network; an unrecorded query is a miss."""

    def __init__(self, store: SnapshotStore, declared_engines: Iterable[str] | None) -> None:
        self._store, self._declared = (
            store,
            declare(list(declared_engines) if declared_engines else None),
        )

    async def search(self, query: str, *, limit: int = 10) -> SearchResponse:
        entry = self._store.search_entry(cache_key(query, self._declared))
        if entry is None:
            raise SnapshotMiss(f"query not in the snapshot: {query!r}")
        return _response(entry, limit, self._declared)


class SnapshotPages:
    """Replays recorded pages and frozen failures. Never reaches the network."""

    def __init__(self, store: SnapshotStore) -> None:
        self._store = store

    async def fetch(self, url: str) -> FetchedPage:
        clean = url.split("#", 1)[0].strip()
        entry = self._store.page_entry(canonical_url(clean))
        if entry is None:
            raise SnapshotMiss(f"page not in the snapshot: {url}")
        return _replay_page(self._store, entry, clean)


def _replay_page(store: SnapshotStore, entry: dict[str, Any], url: str) -> FetchedPage:
    if entry["outcome"] == "failed":
        e = entry["error"]
        cls = {"FetchRefused": FetchRefused, "FetchUnsupported": FetchUnsupported}.get(e["class"])
        if cls is not None:
            raise cls(e["message"], kind=e["kind"])
        raise FetchFailed(e["message"], status=e.get("status"), kind=e["kind"])
    return FetchedPage(
        url=url,
        final_url=entry["final_url"],
        status=entry["status"],
        content_type=entry["content_type"],
        html=store.body(entry["sha256"]),
        fetched_at=entry["fetched_at"],
        redirects=entry["redirects"],
        first_party=None,
    )


# ── Recording ────────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class CaptureTally:
    searches_new: int = 0
    searches_reused: int = 0  # answered from an EARLIER REQUEST's committed capture
    searches_carried: int = 0  # answered from an earlier attempt at THIS request, same session
    reused_fetch_utcs: list[str] = field(default_factory=list)  # when those were first fetched
    pages_new_ok: int = 0
    pages_reused: int = 0
    frozen_failed_permanent: int = 0
    frozen_failed_transient: int = 0
    retries_used: int = 0
    # The live searches that did NOT yield a recordable answer, by kind, and which declared engine
    # caused each degraded one (owner, 2026-10-28: the degraded-search rate is the measurement).
    searches_throttled: int = 0
    searches_unavailable: int = 0
    searches_degraded: int = 0
    # Of the degraded ones, those a declared engine rate-refused: the only ones that move the pace
    # (owner, 2026-10-28). The rest are service faults from one engine.
    searches_degraded_rate_refused: int = 0
    degraded_causes: dict[str, int] = field(default_factory=dict)
    search_seconds: list[float] = field(default_factory=list)
    page_seconds: list[float] = field(default_factory=list)
    # First live fetch started to last live fetch finished (monotonic seconds), the request's
    # fetching as the clock saw it: concurrency, stragglers, politeness waits and retry backoff
    # included, extraction and the commit write not (owner, 2026-10-29: LR's page component).
    fetch_first_s: float | None = None
    fetch_last_s: float | None = None

    @property
    def fetch_wall_s(self) -> float | None:
        if self.fetch_first_s is None or self.fetch_last_s is None:
            return None
        return self.fetch_last_s - self.fetch_first_s

    @property
    def pages_fetched_live(self) -> int:
        return self.pages_new_ok + self.frozen_failed_permanent + self.frozen_failed_transient

    @property
    def searches_rate_refused(self) -> int:
        """The live searches a declared engine refused the rate of, by either route (a throttled
        search or a degraded one): what moves the gap and the wait between attempts."""
        return self.searches_throttled + self.searches_degraded_rate_refused


class Pace(Protocol):
    """How fast live searches may be asked. `wait` is called BEFORE a live search and is not part of
    its cost; the other three report how it went, so the gap can adapt to the RIGHT signal.

    The three outcomes are kept apart because they mean different things (owner, 2026-10-28, after
    the first recording session was stopped: `docs/evaluation-plan.md`, "The pacer backs off for a
    rate refusal only"). A `rate_refusal` is a declared engine saying "too many requests": the one
    signal about how fast we may ask. A `fault` is one engine timing out, erroring, CAPTCHA'd or
    silent: a service fault that says nothing about our rate, so the pace must not move for it."""

    async def wait(self) -> None: ...

    def clean(self) -> None: ...

    def rate_refusal(self) -> None: ...

    def fault(self) -> None: ...


class RecordingSearch:
    """Searches live (or replays an earlier request's identical query) and stages the answer. A
    degraded answer is never staged; the driver aborts the request in RECORD mode.

    The pacing wait happens before the timer starts: the `elapsed_s` stored with an answer is what
    the search itself took, which is the unit cost the derived web time of LR is computed from. (The
    first live capture found the wait inside the timer, recording about 120 s per search.)"""

    def __init__(
        self,
        inner: SearchProvider,
        store: SnapshotStore,
        staging: Staging,
        declared: Iterable[str] | None,
        tally: CaptureTally,
        pace: Pace | None = None,
    ) -> None:
        self._inner, self._store, self._staging, self._tally = inner, store, staging, tally
        self._declared = declare(list(declared) if declared else None)
        self._pace = pace

    async def search(self, query: str, *, limit: int = 10) -> SearchResponse:
        key = cache_key(query, self._declared)
        stored = self._store.search_entry(key)
        known = stored or self._staging.searches.get(key)
        if known is not None:
            if stored is not None:
                self._tally.searches_reused += 1
                self._tally.reused_fetch_utcs.append(stored["fetched_utc"])
            else:  # staged: captured by an earlier attempt at this request in this session
                self._tally.searches_carried += 1
            return _response(known, limit, self._declared)
        if self._pace is not None:
            await self._pace.wait()
        began = time.monotonic()
        try:
            response = await self._inner.search(query, limit=limit)
        except SearchRateLimited:
            self._tally.searches_throttled += 1
            if self._pace is not None:
                self._pace.rate_refusal()
            raise
        except SearchUnavailable:
            # A fault, not a refusal: the engines did not answer, but none said "slow down".
            self._tally.searches_unavailable += 1
            if self._pace is not None:
                self._pace.fault()
            raise
        elapsed = round(time.monotonic() - began, 3)
        if response.degraded:
            refusing = rate_refusing_engines(response.failed_engines)
            if self._pace is not None:
                self._pace.rate_refusal() if refusing else self._pace.fault()
            self._tally.searches_degraded += 1
            if refusing:
                self._tally.searches_degraded_rate_refused += 1
            for failure in response.failed_engines:
                causes = self._tally.degraded_causes
                causes[failure.engine] = causes.get(failure.engine, 0) + 1
            return response  # not staged
        if self._pace is not None:
            self._pace.clean()
        self._tally.searches_new += 1
        self._tally.search_seconds.append(elapsed)
        self._staging.searches[key] = {
            "key": key,
            "query": query,
            "declared": list(self._declared) if self._declared else None,
            "fetched_utc": _utc(),
            "elapsed_s": elapsed,
            "results": [
                {
                    "url": r.url,
                    "title": r.title,
                    "snippet": r.snippet,
                    "engine": r.engine,
                    "published": r.published,
                    "engines": list(r.engines),
                    "first_party": r.first_party,
                }
                for r in response.results
            ],
        }
        return response


class RecordingPages:
    """Fetches live, retries a transient failure a bounded number of times, and stages the final
    outcome (a page, or the failure) once. A URL already in the store is replayed, not refetched."""

    def __init__(
        self,
        inner: PageSource,
        store: SnapshotStore,
        staging: Staging,
        tally: CaptureTally,
        *,
        retries: int,
        backoff_s: float = 2.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if retries < 0:
            raise ValueError("retries must be 0 or more: the bound is explicit")
        self._inner, self._store, self._staging, self._tally = inner, store, staging, tally
        self._retries, self._backoff, self._sleep = retries, backoff_s, sleep

    async def fetch(self, url: str) -> FetchedPage:
        clean = url.split("#", 1)[0].strip()
        canon = canonical_url(clean)
        stored = self._store.page_entry(canon)
        known = stored or self._staging.pages.get(canon)
        if known is not None:
            self._tally.pages_reused += 1
            if known["outcome"] == "failed":
                _replay_page(self._store, known, clean)  # raises the frozen failure
            return (
                _replay_page(self._store, known, clean)
                if stored is not None
                else self._staged_page(known, clean)
            )
        started = time.monotonic()
        try:
            return await self._fetch_live(canon, clean)
        finally:
            t = self._tally
            now = time.monotonic()
            t.fetch_first_s = started if t.fetch_first_s is None else min(t.fetch_first_s, started)
            t.fetch_last_s = now if t.fetch_last_s is None else max(t.fetch_last_s, now)

    async def _fetch_live(self, canon: str, clean: str) -> FetchedPage:
        attempts = 0
        spent = 0.0
        while True:
            attempts += 1
            began = time.monotonic()
            try:
                page = await self._inner.fetch(clean)
            except FetchError as e:
                spent += time.monotonic() - began
                if is_transient(e) and attempts <= self._retries:
                    self._tally.retries_used += 1
                    await self._sleep(self._backoff * attempts)
                    continue
                self._freeze_failure(canon, clean, e, attempts, spent)
                raise
            spent += time.monotonic() - began
            self._freeze_page(canon, clean, page, attempts, spent)
            return page

    def _staged_page(self, entry: dict[str, Any], url: str) -> FetchedPage:
        data = gzip.decompress((self._staging.bodies / f"{entry['sha256']}.gz").read_bytes())
        return FetchedPage(
            url=url,
            final_url=entry["final_url"],
            status=entry["status"],
            content_type=entry["content_type"],
            html=data.decode("utf-8"),
            fetched_at=entry["fetched_at"],
            redirects=entry["redirects"],
        )

    def _freeze_page(
        self, canon: str, url: str, page: FetchedPage, attempts: int, spent: float
    ) -> None:
        elapsed = round(spent, 3)
        self._tally.pages_new_ok += 1
        self._tally.page_seconds.append(elapsed)
        self._staging.pages[canon] = {
            "url": canon,
            "request_url": url,
            "outcome": "ok",
            "final_url": page.final_url,
            "status": page.status,
            "content_type": page.content_type,
            "fetched_at": page.fetched_at,
            "redirects": page.redirects,
            "sha256": self._staging.put_body(page.html),
            "chars": len(page.html),
            "attempts": attempts,
            "elapsed_s": elapsed,
        }

    def _freeze_failure(
        self, canon: str, url: str, error: FetchError, attempts: int, spent: float
    ) -> None:
        elapsed = round(spent, 3)
        transient = is_transient(error)
        if transient:
            self._tally.frozen_failed_transient += 1
        else:
            self._tally.frozen_failed_permanent += 1
        self._tally.page_seconds.append(elapsed)
        self._staging.pages[canon] = {
            "url": canon,
            "request_url": url,
            "outcome": "failed",
            "error": {
                "class": type(error).__name__,
                "kind": error.kind,
                "status": getattr(error, "status", None),
                "message": str(error),
            },
            "transient": transient,
            "attempts": attempts,
            "elapsed_s": elapsed,
        }


@dataclass(frozen=True, slots=True)
class CaptureReport:
    request_id: str
    outcome: str  # "committed" | "aborted"
    abort_reason: str | None
    tally: CaptureTally
    pages_total: int  # distinct pages the request's queries returned
    frozen_failed: int  # of those, frozen as failed (permanent or exhausted retries)
    # Clean searches captured for this request (an aborted attempt's, to be carried into the next
    # attempt of the SAME session; never across sessions). Empty once committed.
    carry: dict[str, dict[str, Any]] = field(default_factory=dict)
    # First to last search the request's evidence rests on, in seconds: `span_s` over the searches
    # captured for this request, `span_all_s` also counting answers reused from other requests at
    # the time THEY were first fetched. None until committed.
    span_s: float | None = None
    span_all_s: float | None = None

    @property
    def frozen_failed_share(self) -> float | None:
        return self.frozen_failed / self.pages_total if self.pages_total else None


async def record_request(
    request_id: str,
    plan: QueryPlan,
    *,
    store: SnapshotStore,
    search: SearchProvider,
    pages: PageSource,
    extractor: Extractor,
    source_cfg: SourceScoring,
    declared: Iterable[str] | None,
    retries: int,
    today: date,
    concurrency: int = 8,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    carry: Mapping[str, dict[str, Any]] | None = None,
    pace: Pace | None = None,
) -> CaptureReport:
    """Capture one request whole (the full ranked list, D2) or not at all.

    `carry` is the clean searches an earlier attempt at THIS request captured in THIS session
    (owner, 2026-10-28): they are reused, with their original fetch times, and the committed line
    records the span they put between the request's first and last search. It is never read from
    the store, so nothing is reused across sessions."""
    staging = store.begin(request_id)
    staging.searches.update({k: dict(v) for k, v in (carry or {}).items()})
    tally = CaptureTally()
    recording_search = RecordingSearch(search, store, staging, declared, tally, pace)
    recording_pages = RecordingPages(pages, store, staging, tally, retries=retries, sleep=sleep)
    try:
        outcome = await research(
            plan,
            "D2",
            mode=Mode.RECORD,
            search=recording_search,
            pages=recording_pages,
            extractor=extractor,
            source_cfg=source_cfg,
            today=today,
            concurrency=concurrency,
        )
    except RequestAborted as e:
        kept = {k: dict(v) for k, v in staging.searches.items()}
        store.discard(staging)
        return CaptureReport(request_id, "aborted", str(e), tally, 0, 0, carry=kept)
    except BaseException:
        store.discard(staging)
        raise
    frozen_failed = sum(1 for p in staging.pages.values() if p["outcome"] == "failed")
    own = sorted(e["fetched_utc"] for e in staging.searches.values())
    every = sorted([*own, *tally.reused_fetch_utcs])
    report = CaptureReport(
        request_id,
        "committed",
        None,
        tally,
        outcome.distinct_urls,
        frozen_failed,
        span_s=_span(own),
        span_all_s=_span(every),
    )
    store.commit(
        staging,
        {
            "queries": len(plan.queries),
            "pages": outcome.distinct_urls,
            "frozen_failed": frozen_failed,
            "retries_used": tally.retries_used,
            "retries_bound": retries,
            # the request's fetching on the clock, and the pages it was spread over: LR's page
            # component is the sum of the first over the sum of the second (owner, 2026-10-29)
            "fetch_wall_s": None if tally.fetch_wall_s is None else round(tally.fetch_wall_s, 1),
            "pages_fetched_live": tally.pages_fetched_live,
            "searches_new": tally.searches_new,
            "searches_reused": tally.searches_reused,
            "searches_carried": tally.searches_carried,
            "first_search_utc": own[0] if own else None,
            "last_search_utc": own[-1] if own else None,
            "search_span_s": report.span_s,
            "search_span_all_s": report.span_all_s,
            "pages_reused": tally.pages_reused,
        },
    )
    return report


# ── The detector the containment tests use ──────────────────────────────────────────────────

SKIP_DIRS = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache"}
)


def find_raw_bodies(roots: Iterable[Path], sentinel: str, allowed: Path) -> list[Path]:
    """Every file under `roots` whose bytes contain `sentinel`, as plain text OR inside gzip,
    except files under `allowed` (the evaluation namespace). A body written anywhere else is
    found."""
    needle = sentinel.encode("utf-8")
    allowed = allowed.resolve()
    found: list[Path] = []
    for root in roots:
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                path = Path(base) / name
                resolved = path.resolve()
                if resolved == allowed or allowed in resolved.parents:
                    continue
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                if needle in data:
                    found.append(path)
                    continue
                try:
                    if needle in gzip.decompress(data):
                        found.append(path)
                except (OSError, EOFError):
                    pass
    return found
