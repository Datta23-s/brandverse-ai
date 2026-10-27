"""Find the highest rate one engine sustains, and how long it takes to recover (docs/searxng.md).

    PYTHONPATH=. uv run python scripts/searxng_ramp_probe.py --engine brave

A RAMP, not a fixed interval (owner, 2026-10-26): the gap between searches starts at 5 minutes
and steps down 5, 4, 3, 2, 1.5, 1, 0.75, 0.5 with five distinct searches at each level; stepping
stops at the first degradation of the engine being measured; then it backs off to the last fully
clean level and keeps searching until the engine answers cleanly again, which measures the recovery
time. The
stepping logic is app/research/rate_ramp.py (pure, tested). Only `--engine` steers it: duckduckgo
and wikipedia are logged like everything else and never decide anything.

Every search is one JSON line, written and fsynced as it happens, so a killed run keeps all it
did: UTC time, the gap since the previous search, level and phase, the provider's outcome, result
count, rows and failure status per engine with the reasons, and every result URL. Events (a level
starting, the break, the back-off, the recovery) are lines of their own.

"Clean" for the engine: SearXNG answered HTTP 200 and did not name the engine among its
unresponsive engines. Zero rows with no failure named is a genuine empty and is clean (it is logged
as `engine_rows: 0`). If SearXNG itself cannot be reached three times running, the run stops and
says so: that is not the engine's doing and is not an observation.
"""

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.errors import SearchRateLimited, SearchUnavailable
from app.research.rate_ramp import LEVELS_MIN, Event, RampController
from app.research.search import SearxngSearch
from scripts.searxng_rate_probe import distinct_queries

REPO_ROOT = Path(__file__).resolve().parents[2]
UNREACHABLE_LIMIT = 3


class Log:
    """Append-only JSON lines, each flushed and fsynced before the next search starts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())


def event_record(event: Event) -> dict[str, Any]:
    return {
        "kind": "event",
        "event": event.kind,
        "utc": datetime.fromtimestamp(event.at, UTC).isoformat(),
        **event.detail,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8888")
    ap.add_argument("--engine", default="brave", help="the one engine that steers the ramp")
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--per-level", type=int, default=5)
    ap.add_argument("--confirm-clean", type=int, default=2)
    ap.add_argument("--recovery-cap-hours", type=float, default=3.0)
    ap.add_argument("--fallback-min", type=float, default=10.0)
    ap.add_argument(
        "--max-hours", type=float, default=6.0, help="a wall-clock stop, whatever happens"
    )
    ap.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="divides every wait; for testing the runner against a fake server ONLY",
    )
    ap.add_argument("--out-dir", type=Path, default=REPO_ROOT / "evaluation" / "search_rate")
    args = ap.parse_args()
    engine = args.engine.casefold()

    ctl = RampController(
        per_level=args.per_level,
        confirm_clean=args.confirm_clean,
        recovery_cap_s=args.recovery_cap_hours * 3600,
        fallback_interval_min=args.fallback_min,
    )
    started = datetime.now(UTC)
    log = Log(args.out_dir / f"ramp-{started:%Y%m%dT%H%M%SZ}.jsonl")
    last: dict[str, Any] = {}

    async def keep(response: httpx.Response) -> None:
        await response.aread()
        last["status"] = response.status_code
        try:
            last["body"] = response.json()
        except ValueError:
            last["body"] = None

    async with httpx.AsyncClient(timeout=30, event_hooks={"response": [keep]}) as client:
        enabled: list[str] = []
        try:
            config = (await client.get(args.url.rstrip("/") + "/config")).json()
            enabled = sorted(e["name"] for e in config["engines"] if e["enabled"])
            version = config.get("version")
        except Exception as e:  # noqa: BLE001 - the header is informative; the run goes on
            version = f"unreadable: {type(e).__name__}"
        log.write(
            {
                "kind": "header",
                "started_utc": started.isoformat(),
                "ramp_engine": engine,
                "levels_min": list(LEVELS_MIN),
                "per_level": args.per_level,
                "confirm_clean": args.confirm_clean,
                "recovery_cap_hours": args.recovery_cap_hours,
                "fallback_min": args.fallback_min,
                "engines_enabled": enabled,
                "searxng_version": version,
                "seed": args.seed,
                "url": args.url,
            }
        )
        log.write(event_record(ctl.start_event(time.time())))
        search = SearxngSearch(args.url, client, declared_engines=None)
        deadline = time.monotonic() + args.max_hours * 3600
        previous_start: float | None = None
        unreachable = 0
        queries = distinct_queries(args.seed)

        for n, (group, query) in enumerate(queries):
            if ctl.done or time.monotonic() >= deadline:
                break
            began = time.monotonic()
            gap = None if previous_start is None else round(began - previous_start, 2)
            previous_start = began
            phase, level = ctl.phase, ctl.interval_min
            last.clear()
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
            rows_by_engine: dict[str, int] = {}
            for row in body.get("results") or []:
                if isinstance(row, dict):
                    for name in row.get("engines") or [row.get("engine")]:
                        rows_by_engine[str(name).casefold()] = (
                            rows_by_engine.get(str(name).casefold(), 0) + 1
                        )
            answered = last.get("status") == 200
            named = {str(u[0]).casefold(): str(u[1]) for u in failed if len(u) == 2}
            clean = answered and engine not in named
            now = time.time()
            log.write(
                {
                    "kind": "search",
                    "n": n,
                    "utc": datetime.fromtimestamp(now, UTC).isoformat(),
                    "gap_since_previous_s": gap,
                    "phase": phase,
                    "planned_interval_min": level,
                    "query": query,
                    "group": group,
                    "outcome": outcome,
                    "error": error,
                    "http_status": last.get("status"),
                    "elapsed_s": round(time.monotonic() - began, 3),
                    "results": len(found),
                    "raw_rows": len(body.get("results") or []),
                    "per_engine_rows": rows_by_engine,
                    "engine_rows": rows_by_engine.get(engine, 0),
                    "unresponsive": failed,
                    "engine_clean": clean,
                    "engine_reason": named.get(engine),
                    "urls": [[r.url, r.engine] for r in found],
                }
            )
            if not answered:
                unreachable += 1
                if unreachable >= UNREACHABLE_LIMIT:
                    log.write(
                        {
                            "kind": "event",
                            "event": "searxng_unreachable",
                            "utc": datetime.now(UTC).isoformat(),
                        }
                    )
                    break
            else:
                unreachable = 0
                for event in ctl.observe(at=now, clean=clean):
                    log.write(event_record(event))
            if not ctl.done:
                wait = max(0.0, ctl.interval_min * 60 / args.speed - (time.monotonic() - began))
                await asyncio.sleep(wait)

        log.write(
            {
                "kind": "summary",
                "utc": datetime.now(UTC).isoformat(),
                "outcome": ctl.outcome,
                "searches": ctl.searches,
                "broke_at_level_min": ctl.broke_at_level,
                "last_fully_clean_level_min": ctl.last_clean_level,
                "highest_sustainable_per_hour": None
                if ctl.last_clean_level is None
                else round(60 / ctl.last_clean_level, 2),
                "backoff_interval_min": ctl.backoff_interval_min,
                "recovery_s": None
                if ctl.broke_at is None or ctl.first_clean_at is None
                else ctl.first_clean_at - ctl.broke_at,
            }
        )
    print(f"log: {log.path}")


if __name__ == "__main__":
    asyncio.run(main())
