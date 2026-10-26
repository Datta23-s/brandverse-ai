"""Unattended measurement of how many searches the local SearXNG sustains (docs/searxng.md).

    uv run python scripts/searxng_rate_probe.py --interval-min 10 --hours 14

One search every `--interval-min` minutes through `SearxngSearch`, each a DISTINCT query (so no
cache, ours or an engine's, flatters the rate), appended as one JSON line to
`evaluation/search_rate/<start>.jsonl`. Nothing is retried and nothing is inferred while it runs:
every line records what the engine said. `scripts/summarise_search_rate.py` reads the file.

Why it exists: the evaluation's recording phase needs real searches, and about ten searches in a few
minutes got this laptop's IP throttled for over twenty minutes (docs/searxng.md). Nothing said how
many per hour the three engines sustain, how long a throttled state takes to clear, or whether the
engines differ.

Each line: UTC time, the query, the provider's outcome (ok / rate_limited / unavailable / error),
result count, results per engine, the engines SearXNG named as unresponsive with their reasons, and
whether any reason says "too many requests" or CAPTCHA. A search that raised is logged, never fatal.
"""

import argparse
import asyncio
import itertools
import json
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.errors import SearchRateLimited, SearchUnavailable
from app.research.search import SearxngSearch

REPO_ROOT = Path(__file__).resolve().parents[2]

# Distinct by construction: every (product, aspect) pair is used at most once, in a fixed shuffled
# order. Indian kitchen and food products, the project's test domain (evaluation/test_set.yaml).
PRODUCTS = [
    "stainless steel kadai",
    "cast iron tawa",
    "pressure cooker",
    "cold pressed groundnut oil",
    "desi cow ghee",
    "raw forest honey",
    "turmeric powder",
    "copper water bottle",
    "air fryer",
    "mixer grinder",
    "non stick frying pan",
    "millet flour atta",
    "basmati rice",
    "green tea",
    "protein powder",
    "coconut oil",
    "idli steamer",
    "induction cooktop",
    "steel tiffin box",
    "organic jaggery",
    "mustard oil",
    "cashew nuts",
    "hand blender",
    "glass storage jars",
]
ASPECTS = [
    "thickness gauge",
    "price in India",
    "customer reviews",
    "material safety",
    "ingredients list",
    "certification India",
    "how to clean",
    "health benefits",
    "warranty terms",
    "compared with alternatives",
    "shelf life",
    "lab test results",
]
TERMINAL = "TERMINAL"


def distinct_queries(seed: int) -> list[str]:
    pairs = list(itertools.product(PRODUCTS, ASPECTS))
    random.Random(seed).shuffle(pairs)  # noqa: S311 - a fixed order, not a secret
    return [f"{product} {aspect}" for product, aspect in pairs]


def classify(failed: list[list[str]]) -> dict[str, bool]:
    reasons = [str(r[1]).casefold() for r in failed if len(r) == 2]
    return {
        "throttle_named": any("too many requests" in r for r in reasons),
        "captcha_named": any("captcha" in r for r in reasons),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8888")
    ap.add_argument("--interval-min", type=float, default=10.0)
    ap.add_argument("--hours", type=float, default=14.0)
    ap.add_argument("--seed", type=int, default=20261026)
    ap.add_argument("--out-dir", type=Path, default=REPO_ROOT / "evaluation" / "search_rate")
    args = ap.parse_args()

    started = datetime.now(UTC)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / f"probe-{started:%Y%m%dT%H%M%SZ}.jsonl"
    last: dict[str, Any] = {}

    async def keep(response: httpx.Response) -> None:
        await response.aread()
        last["status"] = response.status_code
        try:
            last["body"] = response.json()
        except ValueError:
            last["body"] = None

    def write(record: dict[str, Any]) -> None:
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    async with httpx.AsyncClient(timeout=30, event_hooks={"response": [keep]}) as client:
        engines: list[str] = []
        try:
            config = (await client.get(args.url.rstrip("/") + "/config")).json()
            engines = sorted(e["name"] for e in config["engines"] if e["enabled"])
            version = config.get("version")
        except Exception as e:  # noqa: BLE001 - the header is informative; the run goes on
            version = f"unreadable: {type(e).__name__}"
        write(
            {
                "kind": "header",
                "started_utc": started.isoformat(),
                "interval_min": args.interval_min,
                "hours": args.hours,
                "engines_enabled": engines,
                "searxng_version": version,
                "url": args.url,
            }
        )
        search = SearxngSearch(args.url, client)
        deadline = time.monotonic() + args.hours * 3600
        for n, query in enumerate(distinct_queries(args.seed)):
            if time.monotonic() >= deadline:
                break
            last.clear()
            t0 = time.perf_counter()
            outcome, error, found = "ok", None, []
            try:
                found = (await search.search(query, limit=50)).results
            except SearchRateLimited as e:
                outcome, error = "rate_limited", str(e)
            except SearchUnavailable as e:
                outcome, error = "unavailable", str(e)
            except Exception as e:  # noqa: BLE001 - unattended: log it, keep measuring
                outcome, error = "error", f"{type(e).__name__}: {e}"
            body = last.get("body") if isinstance(last.get("body"), dict) else {}
            failed = [r for r in (body.get("unresponsive_engines") or []) if isinstance(r, list)]
            per_engine: dict[str, int] = {}
            for row in body.get("results") or []:
                if isinstance(row, dict):
                    for name in row.get("engines") or [row.get("engine")]:
                        per_engine[str(name)] = per_engine.get(str(name), 0) + 1
            write(
                {
                    "kind": "search",
                    "n": n,
                    "utc": datetime.now(UTC).isoformat(),
                    "query": query,
                    "outcome": outcome,
                    "error": error,
                    "http_status": last.get("status"),
                    "elapsed_s": round(time.perf_counter() - t0, 3),
                    "results": len(found),
                    "raw_rows": len(body.get("results") or []),
                    "per_engine_rows": per_engine,
                    "unresponsive": failed,
                    **classify(failed),
                }
            )
            await asyncio.sleep(args.interval_min * 60)
        write({"kind": TERMINAL, "utc": datetime.now(UTC).isoformat()})


if __name__ == "__main__":
    asyncio.run(main())
