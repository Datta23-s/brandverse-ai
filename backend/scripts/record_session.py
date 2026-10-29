"""Capture requests from the REAL web into the evaluation snapshot, then prove the replay matches.

    PYTHONPATH=. uv run --group extraction python scripts/record_session.py \\
        --store D:/brandverse-eval/snapshot-trial --requests R01 --verify

The real stack: SearXNG at --url, the declared engine set from config, the real `PageFetcher`
(robots.txt honoured, politeness on), the real trafilatura extractor, the recorder's session loop
(adaptive gap, refusal -> discard and redo, dark-period ceiling). `--store` must be OUTSIDE the
repository (the store refuses otherwise) and is created as an evaluation namespace if absent.

`--verify` is the check the first live capture exists for: every live search answer and every live
page body is hashed AS THE LIVE SOURCE RETURNED IT (not as the store holds it), then the committed
requests are replayed and compared, byte for byte, and a report of what the real web did that a mock
would not is printed. A replay that matches the store but not the live bytes would be circular.

Nothing heavy may run on this machine while it does (Architecture.md section 11): it is a long
search job. The machine is to be on AC power.
"""

import argparse
import asyncio
import hashlib
import json
import statistics
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from app.errors import FetchError
from app.evaluation.test_set import plan_requests
from app.memory.scoring import load_scoring
from app.research.extraction import Extractor, trafilatura_extract
from app.research.fetch import FetchedPage, PageFetcher
from app.research.planner import plan_queries
from app.research.recorder import RecorderConfig, record_session
from app.research.search import SearchResponse, SearxngSearch
from app.research.snapshot import SnapshotPages, SnapshotSearch, SnapshotStore
from app.research.snapshot_backup import (
    UnsuitableBackupDestination,
    backup_snapshot,
    check_destination,
    obtain_acknowledgement,
)
from scripts.rate_probe_lib import keep_awake, memory_available_gb

REPO_ROOT = Path(__file__).resolve().parents[2]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TeeSearch:
    """Wraps the live search and keeps what it returned, independent of the store."""

    def __init__(self, inner: SearxngSearch) -> None:
        self._inner = inner
        self.answers: dict[str, list[dict[str, Any]]] = {}
        self.elapsed: list[float] = []

    async def search(self, query: str, *, limit: int = 10) -> SearchResponse:
        began = asyncio.get_running_loop().time()
        response = await self._inner.search(query, limit=limit)
        self.elapsed.append(asyncio.get_running_loop().time() - began)
        if response.complete:
            self.answers[query] = [
                {"url": r.url, "title": r.title, "snippet": r.snippet, "engines": list(r.engines)}
                for r in response.results
            ]
        return response


class TeePages:
    """Wraps the live fetcher and keeps a hash of every body, or the failure, as returned."""

    def __init__(self, inner: PageFetcher) -> None:
        self._inner = inner
        self.live: dict[str, dict[str, Any]] = {}

    async def fetch(self, url: str) -> FetchedPage:
        try:
            page = await self._inner.fetch(url)
        except FetchError as e:
            self.live[url] = {
                "error": type(e).__name__,
                "kind": e.kind,
                "status": getattr(e, "status", None),
            }
            raise
        self.live[url] = {
            "sha": sha(page.html),
            "chars": len(page.html),
            "bytes": len(page.html.encode("utf-8")),
            "final_url": page.final_url,
            "redirects": page.redirects,
            "content_type": page.content_type,
        }
        return page


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", type=Path, required=True)
    ap.add_argument("--requests", default="R01", help="comma-separated ids, or 'all'")
    ap.add_argument("--url", default="http://127.0.0.1:8888")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument(
        "--backup-to",
        type=Path,
        default=None,
        help="the backup destination (second physical disk, not cloud): REQUIRED unless --trial",
    )
    ap.add_argument(
        "--allow-same-disk",
        action="store_true",
        help="permit --backup-to on the same physical disk; prints the consequence and asks you to "
        "type it back",
    )
    ap.add_argument(
        "--same-disk-confirmation", default=None, help="the exact sentence, no keyboard"
    )
    ap.add_argument(
        "--trial",
        action="store_true",
        help="a disposable store for a test capture; the only way to run without a backup",
    )
    args = ap.parse_args()

    cfg = load_scoring()
    research = cfg.research
    if not research.declared_engines:
        raise SystemExit("research.declared_engines is null: the harness refuses to record")
    wanted = plan_requests()
    ids = list(wanted) if args.requests == "all" else args.requests.split(",")
    plans = {
        rid: plan_queries(
            wanted[rid],
            queries_per_facet=research.planner_queries_per_facet,
            locale=research.planner_locale,
            max_pages_per_query=research.max_pages_per_query,
        )
        for rid in ids
    }
    if args.backup_to is None and not args.trial:
        raise SystemExit(
            "the real recording needs --backup-to: retention on the laptop plus one backup is a "
            "condition of the zero-retention exception (Architecture.md section 6). Use --trial "
            "for a disposable test store."
        )
    if args.backup_to is not None:
        # checked BEFORE the first search, so a recording never starts without a legal backup
        try:
            acknowledgement = obtain_acknowledgement(
                args.allow_same_disk, args.same_disk_confirmation
            )
            check_destination(args.store, args.backup_to, same_disk_acknowledgement=acknowledgement)
        except UnsuitableBackupDestination as e:
            raise SystemExit(f"REFUSED: {e}") from e
    store = SnapshotStore.create(args.store)
    started = datetime.now(UTC)
    log_path = store.root / "sessions" / f"session-{started:%Y%m%dT%H%M%SZ}.jsonl"
    awake = keep_awake()
    print(
        f"store {store.root}\nlog   {log_path}\nkeep-awake accepted: {awake}; "
        f"available memory {memory_available_gb()} GB",
        flush=True,
    )

    extractor = Extractor(
        trafilatura_extract,
        min_chars=research.extraction_min_chars,
        max_chars=research.extraction_max_chars,
    )
    async with httpx.AsyncClient(timeout=30) as client:
        search = TeeSearch(
            SearxngSearch(args.url, client, declared_engines=research.declared_engines)
        )
        pages = TeePages(PageFetcher(client, research))
        report = await record_session(
            plans,
            store=store,
            search=search,
            pages=pages,
            extractor=extractor,
            source_cfg=cfg.source,
            declared=research.declared_engines,
            cfg=RecorderConfig.from_research(research),
            today=date.today(),
            log_path=log_path,
        )
    print(json.dumps(report.summary(), indent=1), flush=True)
    summary = {"summary": report.summary(), "manifest": store.manifest_summary()}
    if args.backup_to is not None:
        backup = backup_snapshot(
            store.root, args.backup_to, same_disk_acknowledgement=acknowledgement
        )
        summary["backup"] = {
            "ok": backup.ok,
            "verified": backup.verified,
            "problems": backup.problems,
        }
        print(f"backup: {summary['backup']}", flush=True)
    if args.verify:
        summary["verification"] = await verify(store, plans, search, pages, extractor, cfg)
        print(json.dumps(summary["verification"], indent=1))
    out = REPO_ROOT / "evaluation" / "snapshot_trial" / f"capture-{started:%Y%m%dT%H%M%SZ}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"report {out}")


async def verify(
    store, plans, search: TeeSearch, pages: TeePages, extractor, cfg
) -> dict[str, Any]:
    """Replay what was committed and compare it byte for byte with what the live sources gave."""
    from app.research.driver import Mode, research

    replay_search = SnapshotSearch(store, cfg.research.declared_engines)
    replay_pages = SnapshotPages(store)
    problems: list[str] = []
    searches_checked = pages_checked = pages_failed_checked = 0
    for request_id, plan in plans.items():
        if request_id not in store.committed_requests():
            continue
        for q in plan.queries:
            live = search.answers.get(q.text)
            replayed = await replay_search.search(q.text, limit=50)
            if live is None:
                continue  # an answer reused from the store: no live copy in this session
            got = [
                {"url": r.url, "title": r.title, "snippet": r.snippet, "engines": list(r.engines)}
                for r in replayed.results
            ]
            searches_checked += 1
            if got != live[: len(got)] or len(got) != len(live):
                problems.append(
                    f"search differs: {q.text!r} ({len(live)} live, {len(got)} replayed)"
                )
    for url, live_page in pages.live.items():
        try:
            page = await replay_pages.fetch(url)
        except FetchError as e:
            pages_failed_checked += 1
            if (
                "error" not in live_page
                or live_page["error"] != type(e).__name__
                or live_page["kind"] != e.kind
            ):
                problems.append(
                    f"frozen failure differs: {url}: live {live_page} "
                    f"replay {type(e).__name__}/{e.kind}"
                )
            continue
        pages_checked += 1
        if "sha" not in live_page or sha(page.html) != live_page["sha"]:
            problems.append(f"page bytes differ: {url}")
        if page.final_url != live_page.get("final_url"):
            problems.append(f"final url differs: {url}")
    outcome = await research(
        next(iter(plans.values())),
        "D2",
        mode=Mode.PRODUCTION,
        search=replay_search,
        pages=replay_pages,
        extractor=extractor,
        source_cfg=cfg.source,
        today=date.today(),
    )
    ok = [v for v in pages.live.values() if "sha" in v]
    failed = [v for v in pages.live.values() if "error" in v]
    return {
        "byte_for_byte": not problems,
        "problems": problems,
        "searches_compared": searches_checked,
        "pages_compared": pages_checked,
        "frozen_failures_compared": pages_failed_checked,
        "live_searches_seconds": {
            "n": len(search.elapsed),
            "median": round(statistics.median(search.elapsed), 2) if search.elapsed else None,
            "max": round(max(search.elapsed), 2) if search.elapsed else None,
        },
        "live_pages": {
            "ok": len(ok),
            "failed": len(failed),
            "failures_by_kind": _count(f"{v['error']}/{v['kind']}/{v['status']}" for v in failed),
            "bytes_median": int(statistics.median(v["bytes"] for v in ok)) if ok else None,
            "bytes_max": max((v["bytes"] for v in ok), default=None),
            "with_redirects": sum(1 for v in ok if v["redirects"]),
            "final_url_differs_from_requested": sum(
                1
                for u, v in pages.live.items()
                if "final_url" in v and v["final_url"].rstrip("/") != u.rstrip("/")
            ),
            "content_types": _count(v["content_type"] for v in ok),
        },
        "replayed_driver_run": {
            "searches": outcome.searches_issued,
            "pages_fetched": outcome.pages_fetched,
            "pages_served": outcome.pages_served,
            "pages_extracted": outcome.pages_extracted,
            "extraction_refused": outcome.extraction_refused,
            "discarded_low_score": outcome.discarded_low_score,
            "accepted_findings": len(outcome.findings),
            "failures_by_kind": outcome.failures_by_kind(),
        },
    }


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[str(item)] = out.get(str(item), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


if __name__ == "__main__":
    asyncio.run(main())
