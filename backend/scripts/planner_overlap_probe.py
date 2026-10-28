"""How much a facet's SECOND query repeats its first (docs/evaluation-plan.md section 8, planner).

    PYTHONPATH=. uv run python scripts/planner_overlap_probe.py [--gap-s 120]

q = 8 (4 facets x 2 queries) is conditional (owner, 2026-10-28): a second query only adds breadth if
it retrieves different things. For a handful of real requests this plans the queries, runs all eight
with the candidate declared engine set, and reports the URL overlap between query 1 and query 2
WITHIN each facet and, as the baseline that makes a number readable, between query 1 of DIFFERENT
facets.

Overlap is containment of the smaller result set in the larger, on canonical URLs (scheme, a leading
www., a trailing slash and the fragment ignored): |A & B| / min(|A|, |B|). Hosts are reported the
same way. A search in which any steering engine failed is DEGRADED and is not counted. One log line
per search, then a summary line; paced (default 2-minute gaps) because the engines throttle this IP.
The categories come from the test set's frozen `category` field, not from this script.
"""

import argparse
import asyncio
import itertools
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.errors import SearchError
from app.evaluation.test_set import plan_requests
from app.memory.scoring import load_scoring
from app.research.planner import plan_queries
from app.research.search import SearxngSearch

REPO_ROOT = Path(__file__).resolve().parents[2]
SEVEN = ("yep", "yandex", "yahoo", "privacywall", "duckduckgo web", "swisscows", "mojeek")

# The three requests measured, drawn from the frozen category field of evaluation/test_set.yaml:
# R01 (consumer electronics), R19 (health supplements, regulated: a WHEY blend), R25 (mattress).
REQUEST_IDS = ("R01", "R19", "R25")


def canon(url: str) -> str:
    parts = urlsplit(url)
    return parts.netloc.lower().removeprefix("www.") + parts.path.rstrip("/")


def host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def containment(a: set[str], b: set[str]) -> float | None:
    return len(a & b) / min(len(a), len(b)) if a and b else None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap-s", type=float, default=120.0)
    ap.add_argument("--url", default="http://127.0.0.1:8888")
    args = ap.parse_args()
    cfg = load_scoring().research
    started = datetime.now(UTC)
    out = REPO_ROOT / "evaluation" / "planner_overlap" / f"overlap-{started:%Y%m%dT%H%M%SZ}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    sets: dict[tuple[str, str, int], tuple[set[str], set[str]]] = {}
    lines: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=30) as client:
        search = SearxngSearch(args.url, client, declared_engines=SEVEN)
        test_set = plan_requests()
        for rid in REQUEST_IDS:
            req = test_set[rid]
            plan = plan_queries(
                req,
                queries_per_facet=cfg.planner_queries_per_facet,
                locale=cfg.planner_locale,
                max_pages_per_query=cfg.max_pages_per_query,
            )
            for q in plan.queries:
                rec: dict[str, Any] = {
                    "kind": "search",
                    "request": rid,
                    "facet": q.facet,
                    "angle": q.angle,
                    "rank": q.rank,
                    "query": q.text,
                    "utc": datetime.now(UTC).isoformat(),
                }
                try:
                    response = await search.search(q.text, limit=50)
                    urls = [r.url for r in response.results]
                    degraded = response.degraded
                    rec.update(outcome="degraded" if degraded else "ok", urls=urls)
                    if not degraded:
                        sets[(rid, q.facet, q.angle)] = (
                            {canon(u) for u in urls},
                            {host(u) for u in urls},
                        )
                except SearchError as e:
                    rec.update(outcome=type(e).__name__, error=str(e), urls=[])
                lines.append(rec)
                with out.open("a", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps(rec) + "\n")
                await asyncio.sleep(args.gap_s)

    within, within_h, across, across_h = [], [], [], []
    per_facet: dict[str, list[float]] = {}
    for rid in REQUEST_IDS:
        facets = sorted({f for (r, f, _a) in sets if r == rid})
        for f in facets:
            one, two = sets.get((rid, f, 1)), sets.get((rid, f, 2))
            if one and two:
                c, ch = containment(one[0], two[0]), containment(one[1], two[1])
                if c is not None:
                    within.append(c)
                    per_facet.setdefault(f, []).append(c)
                if ch is not None:
                    within_h.append(ch)
        for fa, fb in itertools.combinations(facets, 2):
            a, b = sets.get((rid, fa, 1)), sets.get((rid, fb, 1))
            if a and b:
                c, ch = containment(a[0], b[0]), containment(a[1], b[1])
                if c is not None:
                    across.append(c)
                if ch is not None:
                    across_h.append(ch)

    def med(xs: list[float]) -> float | None:
        return round(statistics.median(xs), 3) if xs else None

    summary = {
        "kind": "summary",
        "utc": datetime.now(UTC).isoformat(),
        "searches": len(lines),
        "ok": sum(1 for r in lines if r["outcome"] == "ok"),
        "within_facet_url_containment": {
            "n": len(within),
            "median": med(within),
            "all": [round(x, 3) for x in within],
        },
        "within_facet_host_containment_median": med(within_h),
        "across_facets_q1_url_containment": {"n": len(across), "median": med(across)},
        "across_facets_q1_host_containment_median": med(across_h),
        "per_facet_median_url_containment": {f: med(v) for f, v in per_facet.items()},
        "elapsed_s": round(time.time() - started.timestamp(), 1),
    }  # fmt: skip
    with out.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary) + "\n")
    print(json.dumps(summary, indent=1))
    print(f"log: {out}")


if __name__ == "__main__":
    asyncio.run(main())
