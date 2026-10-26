"""Real searches through SearxngSearch against the local SearXNG, and where the real answer differs
from what the mock in tests/test_research_search.py assumes (docs/searxng.md).

    uv run python scripts/searxng_probe.py [--url http://127.0.0.1:8888] [--errors]

Needs the container up: `docker compose up -d searxng`. Prints, per query: what SearxngSearch
returned and the raw JSON's own shape (top-level keys, per-row keys, date formats, row counts), so
the mock can be compared with the engine. `--errors` also provokes the failure paths.
"""

import argparse
import asyncio
import time
from collections import Counter
from typing import Any

import httpx

from app.errors import SearchRateLimited, SearchUnavailable
from app.research.search import SearxngSearch

QUERIES = [
    "stainless steel kadai thickness",
    "FSSAI labelling requirements packaged food",
    "cold pressed groundnut oil benefits",
    "best cast iron tawa India",
    "  Mixed   CASE   query  ",
    "zzzxqv nonsense token qqqwwweee",
]


async def provider_run(url: str, pause: float) -> None:
    # One request per query: a hook keeps the raw body of the very response SearxngSearch parsed,
    # so "parsed vs raw" compares one answer with itself (the engines throttle a second request).
    last: dict[str, Any] = {}

    async def keep(response: httpx.Response) -> None:
        await response.aread()
        last["status"], last["body"] = response.status_code, response.json()

    async with httpx.AsyncClient(timeout=15, event_hooks={"response": [keep]}) as client:
        await _queries(SearxngSearch(url, client), last, pause)


async def _queries(search: SearxngSearch, last: dict[str, Any], pause: float) -> None:
    for n, query in enumerate(QUERIES):
        if n:
            await asyncio.sleep(pause)
        t0 = time.perf_counter()
        last.clear()
        try:
            found = await search.search(query, limit=50)
        except (SearchUnavailable, SearchRateLimited) as e:
            print(f"\n[{query!r}] {type(e).__name__}: {e}")
            continue
        took = time.perf_counter() - t0
        raw = last["body"]
        rows = raw.get("results", [])
        print(f"\n[{query!r}] {len(found)} parsed of {len(rows)} raw rows in {took:.2f}s")
        print("  top-level keys:", sorted(raw))
        print("  row keys (union):", sorted({k for r in rows for k in r}))
        print("  engines on parsed:", dict(Counter(r.engine for r in found)))
        print("  engine field types:", Counter(type(r.get("engine")).__name__ for r in rows))
        print(
            "  publishedDate present:",
            sum(1 for r in rows if r.get("publishedDate")),
            "of",
            len(rows),
            "| samples:",
            [r.get("publishedDate") for r in rows if r.get("publishedDate")][:3],
        )
        print(
            "  empty content:",
            sum(1 for r in rows if not r.get("content")),
            "| no title:",
            sum(1 for r in rows if not (r.get("title") or "").strip()),
        )
        print(
            "  dropped by parser:",
            len(rows) - len(found),
            "| duplicate urls:",
            len(found) - len({r.url for r in found}),
        )
        print("  unresponsive_engines:", raw.get("unresponsive_engines"))
        for r in found[:3]:
            print("   ", r.engine, "|", r.url[:70], "|", r.title[:50], "|", repr(r.published))


async def error_paths(url: str) -> None:
    print("\n=== error paths")
    base = url.rstrip("/")

    async def attempt(label: str, search: SearxngSearch, query: str = "kadai") -> None:
        t0 = time.perf_counter()
        try:
            r = await search.search(query)
            print(f"{label}: returned {len(r)} results in {time.perf_counter() - t0:.2f}s")
        except (SearchUnavailable, SearchRateLimited) as e:
            print(f"{label}: {type(e).__name__}: {e} ({time.perf_counter() - t0:.2f}s)")

    async with httpx.AsyncClient(timeout=10) as c:
        await attempt("closed port", SearxngSearch("http://127.0.0.1:1", c))
        await attempt("wrong path / not SearXNG", SearxngSearch(base + "/nope", c))
        await attempt("host that does not resolve", SearxngSearch("http://searxng.invalid:8888", c))
    async with httpx.AsyncClient(timeout=httpx.Timeout(0.001)) as c:
        await attempt("timeout 1 ms", SearxngSearch(base, c))
    async with httpx.AsyncClient(timeout=10) as c:
        # json format asked of an engine that has it disabled is a 403: exercised by the
        # `formats` settings, see docs/searxng.md; here we ask for an impossible format directly.
        r = await c.get(base + "/search", params={"q": "x", "format": "csv"})
        print("format=csv directly (not through the provider):", r.status_code)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8888")
    ap.add_argument("--errors", action="store_true")
    ap.add_argument(
        "--pause",
        type=float,
        default=20.0,
        help="seconds between queries; the upstream engines throttle a burst (docs/searxng.md)",
    )
    args = ap.parse_args()
    await provider_run(args.url, args.pause)
    if args.errors:
        await error_paths(args.url)


if __name__ == "__main__":
    asyncio.run(main())
