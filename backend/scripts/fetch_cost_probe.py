"""What it costs to fetch the pages ONE real search returned (docs/fetcher.md, "Fetch cost").

    PYTHONPATH=. uv run python scripts/fetch_cost_probe.py URLS.json [--concurrency 8] [--out FILE]

URLS.json is a SearXNG JSON answer (`results[].url`) or a plain list of URLs. Every distinct URL is
fetched once through the real `PageFetcher` (robots.txt honoured, per-host politeness on, PDFs
skipped unrequested), at the stated concurrency. One JSON line per URL with its outcome and time,
then a summary: the time a SEQUENTIAL pass would take is estimated as the sum of the per-URL times
(politeness waits excluded, they are reported on their own), because running it sequentially too
would hit every host twice. Nothing is stored: pages are read and dropped.
"""

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.errors import FetchError
from app.memory.scoring import load_scoring
from app.research.fetch import PageFetcher

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_urls(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = [r["url"] for r in data["results"]] if isinstance(data, dict) else list(data)
    seen: dict[str, None] = {}
    for url in raw:
        key = url.split("#", 1)[0].rstrip("/")
        seen.setdefault(key, None)
    return list(seen)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", type=Path)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    urls = load_urls(args.urls)
    started = datetime.now(UTC)
    out = (
        args.out
        or REPO_ROOT / "evaluation" / "fetch_cost" / f"fetch-{started:%Y%m%dT%H%M%SZ}.jsonl"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    cfg = load_scoring().research
    gate = asyncio.Semaphore(args.concurrency)
    records: list[dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        fetcher = PageFetcher(client, cfg)

        async def one(i: int, url: str) -> None:
            async with gate:
                t0 = time.perf_counter()
                rec: dict[str, Any] = {"kind": "fetch", "n": i, "url": url}
                rec["host"] = (urlsplit(url).hostname or "").lower()
                try:
                    page = await fetcher.fetch(url)
                    rec.update(outcome="ok", status=page.status, chars=len(page.html))
                except FetchError as e:
                    rec.update(outcome=type(e).__name__, error_kind=getattr(e, "kind", None))
                except Exception as e:  # noqa: BLE001 - a probe: log it, keep going
                    rec.update(outcome="error", error_kind=type(e).__name__)
                rec["elapsed_s"] = round(time.perf_counter() - t0, 3)
                records.append(rec)

        wall0 = time.perf_counter()
        await asyncio.gather(*(one(i, u) for i, u in enumerate(urls)))
        wall = time.perf_counter() - wall0

        times = sorted(r["elapsed_s"] for r in records)
        outcomes: dict[str, int] = {}
        for r in records:
            key = r["outcome"] + (f"/{r['error_kind']}" if r.get("error_kind") else "")
            outcomes[key] = outcomes.get(key, 0) + 1
        requested = [r for r in records if r.get("error_kind") not in ("pdf", "robots")]
        summary = {
            "kind": "summary",
            "utc": datetime.now(UTC).isoformat(),
            "urls": len(urls),
            "hosts": len({r["host"] for r in records}),
            "concurrency": args.concurrency,
            "host_delay_s": cfg.fetch_host_delay_s,
            "wall_s": round(wall, 1),
            "sequential_estimate_s": round(sum(times), 1),
            "median_s": round(statistics.median(times), 2),
            "p90_s": round(times[int(0.9 * (len(times) - 1))], 2),
            "slowest_s": times[-1],
            "outcomes": dict(sorted(outcomes.items(), key=lambda kv: -kv[1])),
            "ok_chars_median": int(
                statistics.median([r["chars"] for r in records if r["outcome"] == "ok"] or [0])
            ),
            "robots_fetches": fetcher.robots_fetches,
            "politeness_waits": fetcher.waits,
            "politeness_waited_s": round(fetcher.waited_s, 1),
            "requested_pages": len(requested),
        }
    with out.open("w", encoding="utf-8", newline="\n") as f:
        for r in sorted(records, key=lambda r: r["n"]):
            f.write(json.dumps(r) + "\n")
        f.write(json.dumps(summary) + "\n")
    print(json.dumps(summary, indent=1))
    print(f"log: {out}")


if __name__ == "__main__":
    asyncio.run(main())
