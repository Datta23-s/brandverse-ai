"""What share of the search results the pipeline cannot take, by content type.

    PYTHONPATH=. uv run python scripts/classify_result_types.py LOG.jsonl [LOG.jsonl ...]

(run from backend/; the logs are evaluation/search_rate/probe-*.jsonl)

Owner, 2026-10-26: PDFs are not supported (docs/fetcher.md), and whether that matters is measured,
not assumed. Every result URL the rate probe logged is classified by what its server answers with,
and the share that are PDFs is reported overall and for the regulated group. A couple of percent
stays a footnote; a fifth of the regulated sector's results is a real gap, and the owner rules
again.

Each distinct URL is asked once, for its response headers only (the body is never read), through the
same `PageFetcher` the pipeline uses: its address policy, and robots.txt honoured, so a URL the
fetcher would not request is counted as unreached and not requested. A path ending in `.pdf` is
counted as a PDF by name, without a request. Writes `<first log>.types.json` beside the first log,
one object per URL.
"""

import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import httpx

from app.errors import FetchError
from app.memory.scoring import load_scoring
from app.research.fetch import PageFetcher, is_pdf_url

CONCURRENCY = 4


def urls_by_group(paths: list[Path]) -> dict[str, dict[str, str | None]]:
    """group -> {url: engine}, each distinct URL once per group (a URL returned by several
    searches counts once)."""
    groups: dict[str, dict[str, str | None]] = defaultdict(dict)
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("kind") != "search":
                continue
            for url, engine in record.get("urls", []):
                groups[record.get("group", "ungrouped")].setdefault(url, engine)
    return groups


async def classify(fetcher: PageFetcher, url: str) -> str:
    if is_pdf_url(url):
        return "pdf (by name)"
    try:
        media = await fetcher.content_type_of(url)
    except FetchError as e:
        detail = f"{type(e).__name__}:{e.kind}" if e.kind else type(e).__name__
        return f"unreached: {detail}"
    if media == "application/pdf":
        return "pdf (by content type)"
    if media in ("text/html", "application/xhtml+xml"):
        return "html"
    return f"other: {media or 'none declared'}"


def share(counts: Counter[str], label: str) -> str:
    total = sum(counts.values())
    if not total:
        return f"{label}: no URLs"
    pdf = sum(n for k, n in counts.items() if k.startswith("pdf"))
    reached = sum(n for k, n in counts.items() if not k.startswith("unreached"))
    out = f"{label}: {total} distinct URLs; PDF {pdf} = {pdf / total:.1%} of all"
    if reached:
        out += f", {pdf / reached:.1%} of the {reached} reached"
    return out


async def main(paths: list[Path]) -> None:
    groups = urls_by_group(paths)
    cfg = load_scoring().research
    results: dict[str, dict[str, dict[str, str | None]]] = {}
    async with httpx.AsyncClient() as client:
        fetcher = PageFetcher(client, cfg)
        gate = asyncio.Semaphore(CONCURRENCY)

        async def one(url: str) -> tuple[str, str]:
            async with gate:
                return url, await classify(fetcher, url)

        for group, urls in groups.items():
            done = await asyncio.gather(*(one(u) for u in urls))
            results[group] = {u: {"engine": urls[u], "type": t} for u, t in done}
            print(f"classified {len(done)} URLs in group {group!r}", file=sys.stderr)

    everything: Counter[str] = Counter()
    for group, rows in results.items():
        counts = Counter(str(r["type"]) for r in rows.values())
        everything.update(counts)
        print(share(counts, group))
        for kind, n in counts.most_common():
            print(f"    {n:5d}  {kind}")
    print(share(everything, "all groups"))
    out = paths[0].with_suffix(".types.json")
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"written: {out}")


if __name__ == "__main__":
    asyncio.run(main([Path(p) for p in sys.argv[1:]]))
