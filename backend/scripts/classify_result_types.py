"""What share of the search results the pipeline cannot take: PDFs, estimated cheaply.

    PYTHONPATH=. uv run python scripts/classify_result_types.py LOG.jsonl [LOG.jsonl ...]
        [--sample 50] [--sample-group regulated] [--seed N] [--dry]

(run from backend/; the logs are evaluation/search_rate/*.jsonl)

Owner, 2026-10-26: PDFs are not supported (docs/fetcher.md), and whether that matters is measured,
not assumed. A couple of percent stays a footnote; a fifth of the regulated sector's results is a
real gap and the owner rules again. The IP is already throttled by the search engines, so this
spends as little of it as it can, in this order:

1. **By extension, free.** A URL whose path ends in `.pdf` is a PDF with no request at all.
2. **A random sample of the rest, with HEAD, on ONE group.** Owner, 2026-10-27: 50 HEAD requests,
   all on the regulated group and none on the general one. The general group's free floor (1 of 96
   by extension) already answers it and needs no request; the open question is only whether
   regulated PDFs are a real share, so the requests go where the answer is in doubt. The sampled
   group draws a seeded random sample of up to `--sample` of its remaining distinct URLs and asks
   each for its response headers only, through the same `PageFetcher` the pipeline uses (its address
   policy, robots.txt honoured).
   HEAD, never GET: a server that refuses HEAD is counted as unknown, not retried with GET.
   Concurrency is 1, and no URL is asked twice.
3. **An estimate with its uncertainty, not a count.** The group's PDF share is the by-extension
   count plus the sampled rate times the unsampled remainder, over all the group's URLs; the sampled
   rate carries a 95% Wilson interval, which is carried into the share. Unknown samples (refused,
   unreachable, HEAD refused) are reported and left out of the rate, so a high unknown share says
   the estimate is weak rather than being hidden.

`--dry` prints what WOULD be requested (how many URLs, per group) and makes no request, so the cost
can be seen before it is spent. A run writes `<first log>.types.json` with one object per URL asked.
"""

import argparse
import asyncio
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

import httpx

from app.errors import FetchError
from app.memory.scoring import load_scoring
from app.research.fetch import PageFetcher, is_pdf_url


def urls_by_group(paths: list[Path]) -> dict[str, list[str]]:
    """group -> its distinct URLs in first-seen order (a URL returned by several searches counts
    once). Reads only the `urls` the probes logged: no titles or snippets exist in the logs."""
    groups: dict[str, dict[str, None]] = defaultdict(dict)
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("kind") != "search":
                continue
            for url, _engine in record.get("urls", []):
                groups[record.get("group", "ungrouped")].setdefault(url, None)
    return {group: list(urls) for group, urls in groups.items()}


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """The 95% Wilson score interval for a proportion; (0, 1) when nothing was sampled."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def plan(urls: list[str], sample: int, seed: int) -> tuple[list[str], list[str], list[str]]:
    """(PDFs by extension, URLs to ask, URLs left unasked). The sample is seeded and random."""
    by_extension = [u for u in urls if is_pdf_url(u)]
    rest = [u for u in urls if not is_pdf_url(u)]
    chosen = random.Random(seed).sample(rest, min(sample, len(rest)))  # noqa: S311 - not a secret
    asked = set(chosen)
    return by_extension, chosen, [u for u in rest if u not in asked]


async def classify(fetcher: PageFetcher, url: str) -> str:
    try:
        media = await fetcher.content_type_of(url)
    except FetchError as e:
        return f"unknown: {type(e).__name__}:{e.kind}" if e.kind else f"unknown: {type(e).__name__}"
    if media == "application/pdf":
        return "pdf"
    if media in ("text/html", "application/xhtml+xml"):
        return "html"
    return f"other: {media or 'none declared'}"


def estimate(total: int, ext_pdf: int, answers: Counter[str]) -> tuple[float, float, float]:
    """(point, low, high) for the group's PDF share of all `total` URLs."""
    known = sum(n for k, n in answers.items() if not k.startswith("unknown"))
    low_rate, high_rate = wilson(answers.get("pdf", 0), known)
    rate = answers.get("pdf", 0) / known if known else 0.0
    remainder = total - ext_pdf  # every non-extension URL is covered by the rate, asked or not
    point = (ext_pdf + rate * remainder) / total
    return (
        point,
        (ext_pdf + low_rate * remainder) / total,
        (ext_pdf + high_rate * remainder) / total,
    )


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+", type=Path)
    ap.add_argument("--sample", type=int, default=50, help="HEAD requests, all on --sample-group")
    ap.add_argument(
        "--sample-group",
        default="regulated",
        help="the ONE group that is sampled (owner, 2026-10-27); every other group gets its free "
        "by-extension floor and no request at all",
    )
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--dry", action="store_true", help="show the cost; make no request")
    args = ap.parse_args()
    groups = urls_by_group(args.logs)
    if args.sample_group not in groups:
        raise SystemExit(f"no group {args.sample_group!r} in the logs (found {sorted(groups)})")
    plans = {
        g: plan(urls, args.sample if g == args.sample_group else 0, args.seed)
        for g, urls in groups.items()
    }

    print("plan (no request is made for the by-extension PDFs):")
    for g, (ext, asked, unasked) in plans.items():
        print(
            f"  {g}: {len(groups[g])} distinct URLs: {len(ext)} PDF by extension, "
            f"{len(asked)} to ask with HEAD, {len(unasked)} left unasked"
        )
    print(f"  requests to third-party sites: {sum(len(a) for _, a, _ in plans.values())}")
    if args.dry:
        return

    cfg = load_scoring().research
    results: dict[str, dict[str, str]] = {}
    async with httpx.AsyncClient() as client:
        fetcher = PageFetcher(client, cfg)
        for g, (_ext, asked, _unasked) in plans.items():
            results[g] = {}
            for url in asked:  # one at a time: the IP cannot afford a burst
                results[g][url] = await classify(fetcher, url)
            print(f"asked {len(asked)} URLs in group {g!r}", file=sys.stderr)

    print()
    for g, (ext, asked, _unasked) in plans.items():
        total = len(groups[g])
        if not asked:  # not sampled: only what the extension alone proves, said to be only that
            print(
                f"{g}: at least {len(ext) / total:.1%} are PDFs ({len(ext)} of {total} URLs end in "
                ".pdf); a FLOOR from the extension alone, no request made, nothing sampled"
            )
            continue
        answers = Counter(results[g].values())
        point, low, high = estimate(total, len(ext), answers)
        print(
            f"{g}: PDF share ~ {point:.1%} (95% interval {low:.1%} to {high:.1%}) of {total} URLs"
        )
        print(
            f"    by extension {len(ext)}; sampled {len(asked)}: "
            + ", ".join(f"{n} {k}" for k, n in answers.most_common())
        )
        unknown = sum(n for k, n in answers.items() if k.startswith("unknown"))
        if asked and unknown / len(asked) > 0.25:
            print(f"    WARNING: {unknown} of {len(asked)} samples unknown: the estimate is weak")
    out = args.logs[0].with_suffix(".types.json")
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"written: {out}")


if __name__ == "__main__":
    asyncio.run(main())
