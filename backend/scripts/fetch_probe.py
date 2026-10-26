"""The page fetcher against the real network (docs/fetcher.md): real DNS, TLS and redirects.

    PYTHONPATH=. uv run python scripts/fetch_probe.py [--redis]

A handful of fetches, one per case the fetcher distinguishes, printed with the outcome and what was
returned. It exists because the unit tests play the web with a mock, which is the developer's own
model of it (the SearXNG mock was wrong in exactly this way; docs/searxng.md). With --redis the same
URLs go through `CachedPages` against the local Redis, twice, to see a real reuse.
"""

import argparse
import asyncio
import time

import httpx
from redis.asyncio import Redis

from app.errors import FetchError, FetchFailed
from app.memory.scoring import load_scoring
from app.research.fetch import PageFetcher
from app.research.page_cache import CachedPages

CASES = [
    ("plain page", "https://example.com/"),
    ("http then https", "http://en.wikipedia.org/wiki/Karahi"),
    ("a redirect to another title", "https://en.wikipedia.org/wiki/Kadai"),
    ("a fragment", "https://example.com/#section"),
    ("a 404", "https://example.com/this-page-does-not-exist-xyz"),
    ("a PDF", "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"),
    ("a regulator", "https://www.fssai.gov.in/"),
    ("a host that does not exist", "https://no-such-host-brandverse.invalid/"),
    ("localhost by name", "http://localhost/"),
    ("loopback literal", "http://127.0.0.1/"),
    ("cloud metadata address", "http://169.254.169.254/latest/meta-data/"),
    ("a non-default port", "https://example.com:8443/"),
    ("not http", "ftp://example.com/file"),
]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--redis", action="store_true")
    args = ap.parse_args()
    cfg = load_scoring().research
    async with httpx.AsyncClient() as client:
        fetcher = PageFetcher(client, cfg)
        source = fetcher
        redis = None
        if args.redis:
            redis = Redis.from_url("redis://127.0.0.1:6379/0")
            source = CachedPages(fetcher, redis, ttl_s=cfg.page_cache_ttl_s)  # type: ignore[assignment,arg-type]
        for _ in range(2 if args.redis else 1):
            for label, url in CASES:
                t0 = time.perf_counter()
                try:
                    page = await source.fetch(url)
                    outcome = (
                        f"OK {page.status} {page.content_type} {len(page.html)} chars, "
                        f"{page.redirects} redirect(s), final {page.final_url}"
                    )
                except FetchFailed as e:
                    outcome = f"FetchFailed status={e.status}: {e}"
                except FetchError as e:
                    outcome = f"{type(e).__name__}: {e}"
                print(f"{time.perf_counter() - t0:5.2f}s  {label:28} {outcome}")
            print("-")
        if args.redis:
            print(source.report().model_dump_json(exclude={"records"}))  # type: ignore[attr-defined]
            for _, url in CASES:
                from app.research.page_cache import cache_key

                await redis.delete(cache_key(url))  # type: ignore[union-attr]
            await redis.aclose()  # type: ignore[union-attr]


if __name__ == "__main__":
    asyncio.run(main())
