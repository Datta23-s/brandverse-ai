"""Where a run's time goes (migration 0044; `app/evaluation/latency.py`).

A node that wants to know how long its LOCAL work took opens `collecting()`, and anything it calls
that is wrapped here (`TimedSearch`, `TimedEncoder`) adds its elapsed milliseconds to the node's own
dict, under the name it was built with. The dict lives in a context variable, so two runs on one
worker, or two nodes of one run, never write into each other's figures. Outside `collecting()` the
wrappers add nothing and cost nothing but the call.

The figures are WALL milliseconds of the calls themselves. They are not CPU time, and two wrapped
calls running at once add up to more than the wall time they shared; the evaluation reports the
difference as a signed residual rather than hiding it.
"""

import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from app.research.search import SearchProvider, SearchResponse

_current: ContextVar[dict[str, float] | None] = ContextVar("run_timings", default=None)


@contextmanager
def collecting() -> Iterator[dict[str, float]]:
    """The milliseconds the wrapped calls inside the block took, by name."""
    timings: dict[str, float] = {}
    token = _current.set(timings)
    try:
        yield timings
    finally:
        _current.reset(token)


def add(name: str, ms: float) -> None:
    timings = _current.get()
    if timings is not None:
        timings[name] = timings.get(name, 0.0) + ms


def merge_timings(
    left: dict[str, float] | None, right: dict[str, float] | None
) -> dict[str, float]:
    """The run state's reducer: figures of the same name add (a node may run more than once)."""
    out = dict(left or {})
    for name, ms in (right or {}).items():
        out[name] = out.get(name, 0.0) + ms
    return out


class TimedSearch:
    """A search provider that records the time of each call as `search_ms`."""

    def __init__(self, inner: SearchProvider) -> None:
        self._inner = inner

    async def search(self, query: str, *, limit: int = 10) -> SearchResponse:
        started = time.perf_counter()
        try:
            return await self._inner.search(query, limit=limit)
        finally:
            add("search_ms", (time.perf_counter() - started) * 1000)


class TimedEncoder:
    """An encoder that records the time of each embedding call under `name`. Everything else
    (model, version, tokenisation) is the wrapped encoder's own."""

    def __init__(self, inner: Any, name: str) -> None:
        self._inner, self._name = inner, name

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self._inner, attribute)

    def embed_queries(self, texts: Sequence[str]) -> Any:
        started = time.perf_counter()
        try:
            return self._inner.embed_queries(texts)
        finally:
            add(self._name, (time.perf_counter() - started) * 1000)

    def embed_documents(self, texts: Sequence[str]) -> Any:
        started = time.perf_counter()
        try:
            return self._inner.embed_documents(texts)
        finally:
            add(self._name, (time.perf_counter() - started) * 1000)
