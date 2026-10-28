"""The sustained hold and the silent-wait recovery (docs/evaluation-plan.md section 8).

    PYTHONPATH=. uv run python scripts/searxng_hold_probe.py --mode hold
    PYTHONPATH=. uv run python scripts/searxng_hold_probe.py --mode silent

**hold** (the decisive test; owner, 2026-10-27): after resting, search at EXACTLY 10-minute gaps for
at least 4 hours and 24 searches, distinct queries throughout, and stop at the first refusal. If
brave stays clean the whole time, 6 an hour is a measured sustained rate; if it breaks, the log
says at which search and how many requests were in the trailing hour. It rests first
(`--rest-min`, default 90) because a bucket drained by an earlier run would make the hold measure
the drained state, not the sustained rate (the token-bucket reading in section 8 predicts a full
refill takes 1.2 to 1.6 hours). The rest asks nothing.

**silent** (the recovery variant): provoke a throttle with a burst (a search every 2 minutes until
the engine refuses), then STOP ASKING and probe once at growing delays after the break
(5, 10, 15, 20, 30, 45, 60, 90, 120, 180 minutes), with one confirming probe after the first clean
one. If recovery is minutes when nobody asks and hours when somebody keeps asking, the recording
must pause on a throttle rather than retry. The result is an interval, not a point.

Every search is one JSON line, written and fsynced as it happens, with the trailing-hour request
count and the machine's available memory beside it. Only `--engine` (default brave) decides
anything; duckduckgo and wikipedia are logged and never steer. If SearXNG itself is unreachable
three times running, the run stops and says so.
"""

import argparse
import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.research.rate_ramp import (
    HoldController,
    SetHoldController,
    SilentRecoveryController,
    trailing_count,
)
from app.research.search import SearxngSearch
from scripts.rate_probe_lib import Log, event_record, keep_awake, observe, queries

REPO_ROOT = Path(__file__).resolve().parents[2]
UNREACHABLE_LIMIT = 3


async def sleep_until(epoch: float, speed: float) -> None:
    await asyncio.sleep(max(0.0, (epoch - time.time()) / speed))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["hold", "silent"], required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8888")
    ap.add_argument(
        "--engine",
        default="brave",
        help="the steering engine, or a comma-separated SET: the hold breaks at the first failure"
        " of ANY engine in it. Engines outside the set stay enabled, are logged, and never steer.",
    )
    ap.add_argument("--seed", type=int, default=20261028)
    ap.add_argument(
        "--regulated-start", type=int, default=5, help="skip the entries earlier runs used"
    )
    ap.add_argument(
        "--rest-min", type=float, default=90.0, help="hold only: silence before the first search"
    )
    ap.add_argument(
        "--silent-since",
        default=None,
        help="hold only: ISO time of the last request made to the engine by anything; the rest "
        "already served since then is subtracted from --rest-min (the rest is judged by actual "
        "silence, not by when this process started)",
    )
    ap.add_argument(
        "--broke-at",
        default=None,
        help="silent only: ISO time of a refusal that ALREADY happened (and nothing has been asked "
        "since); recovery is probed from it instead of from a newly provoked throttle",
    )
    ap.add_argument("--interval-min", type=float, default=10.0, help="hold only")
    ap.add_argument("--min-searches", type=int, default=24, help="hold only")
    ap.add_argument("--min-hours", type=float, default=4.0, help="hold only")
    ap.add_argument(
        "--max-hours", type=float, default=8.0, help="a wall-clock stop, whatever happens"
    )
    ap.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="divides every wait, for testing against a fake server ONLY: the times it logs, the "
        "trailing-hour counts and the hold's hours are real clock time and mean nothing under it",
    )
    ap.add_argument("--out-dir", type=Path, default=REPO_ROOT / "evaluation" / "search_rate")
    args = ap.parse_args()
    steering = tuple(e.strip().casefold() for e in args.engine.split(",") if e.strip())
    if not steering:
        raise SystemExit("--engine names no engine")
    engine: str | tuple[str, ...] = steering[0] if len(steering) == 1 else steering

    # A SET of engines runs to completion (SetHoldController): one failing engine does not stop it.
    hold: HoldController | SetHoldController
    if isinstance(engine, tuple):
        hold = SetHoldController(
            engine,
            interval_min=args.interval_min,
            min_searches=args.min_searches,
            min_hours=args.min_hours,
        )
    else:
        hold = HoldController(
            interval_min=args.interval_min, min_searches=args.min_searches, min_hours=args.min_hours
        )
    started = datetime.now(UTC)
    if args.mode == "silent" and args.broke_at:
        # Begin in silence from a throttle that already happened (a hold that broke): nothing has
        # been asked since, so the recovery is measured from that break, not from a new burst.
        silent = SilentRecoveryController.after_a_break(
            broke_at=datetime.fromisoformat(args.broke_at).timestamp(), now=started.timestamp()
        )
    else:
        silent = SilentRecoveryController()
    log = Log(args.out_dir / f"{args.mode}-{started:%Y%m%dT%H%M%SZ}.jsonl")
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
        header = {
            "kind": "header",
            "mode": args.mode,
            "started_utc": started.isoformat(),
            "engine": engine if isinstance(engine, str) else list(engine),
            "engines_enabled": enabled,
            "searxng_version": version,
            "seed": args.seed,
            "regulated_start": args.regulated_start,
            "rest_min": args.rest_min if args.mode == "hold" else None,
            "interval_min": args.interval_min if args.mode == "hold" else None,
            "min_searches": args.min_searches if args.mode == "hold" else None,
            "min_hours": args.min_hours if args.mode == "hold" else None,
            "url": args.url,
        }
        log.write(header)
        # A set is the candidate declared set: only its engines can degrade a search, so brave's
        # throttling (still enabled) does not turn the search into an error.
        search = SearxngSearch(
            args.url, client, declared_engines=None if isinstance(engine, str) else steering
        )
        deadline = time.monotonic() + args.max_hours * 3600 / args.speed
        queue = queries(args.seed, args.regulated_start)
        times: list[float] = []
        unreachable = 0

        awake = keep_awake()
        log.write(
            {
                "kind": "event",
                "event": "keep_awake_requested",
                "accepted": awake,
                "utc": started.isoformat(),
                "note": "stops idle sleep only; a closed lid or manual sleep still sleeps",
            }
        )
        rest_min = args.rest_min
        silent_for_min = 0.0
        if args.mode == "hold" and args.silent_since:
            since = datetime.fromisoformat(args.silent_since)
            silent_for_min = max(0.0, (started - since).total_seconds() / 60)
            rest_min = max(0.0, args.rest_min - silent_for_min)
        if args.mode == "hold":
            log.write(
                {
                    "kind": "event",
                    "event": "resting" if rest_min > 0 else "rest_already_served",
                    "utc": started.isoformat(),
                    "rest_required_min": args.rest_min,
                    "silent_for_min": round(silent_for_min, 1),
                    "rest_remaining_min": round(rest_min, 1),
                    "silent_since": args.silent_since,
                    "why": "a drained bucket would make the hold measure the drained state",
                }
            )
        if args.mode == "hold" and rest_min > 0:  # the rest belongs to the hold alone
            await asyncio.sleep(rest_min * 60 / args.speed)

        next_at = time.time()
        if args.mode == "silent" and args.broke_at:
            log.write(
                {
                    "kind": "event",
                    "event": "silence_from_an_existing_break",
                    "utc": started.isoformat(),
                    "broke_at": args.broke_at,
                    "skipped_delays_min": silent.skipped_delays_min,
                    "first_probe_after_min": None
                    if silent.done
                    else silent.delays_min[silent.probe_index],
                    "note": "nothing has been asked since the break; skipped delays were not "
                    "probed late, so the recovery interval's lower bound is the break itself",
                }
            )
            if not silent.done:
                next_at = silent.next_search_at(time.time())
        for n, (group, query) in enumerate(queue):
            controller_done = hold.done if args.mode == "hold" else silent.done
            if controller_done or time.monotonic() >= deadline:
                break
            await sleep_until(next_at, args.speed)
            before = trailing_count(times, time.time())
            obs = await observe(search, last, engine=engine, n=n, group=group, query=query)
            obs.record["requests_in_trailing_hour_including_this"] = before
            obs.record["phase"] = args.mode if args.mode == "hold" else silent.phase
            log.write(obs.record)
            times.append(obs.at)
            if not obs.answered:
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
                next_at = obs.at + (args.interval_min * 60 if args.mode == "hold" else 120.0)
                continue
            unreachable = 0
            if args.mode == "hold":
                if isinstance(hold, SetHoldController):
                    rec = obs.record
                    events = hold.observe(
                        at=obs.at,
                        rows=rec["steering_rows"],
                        errors={e: str(r) for e, r in rec["steering_failed"].items()},
                    )
                    rec_status = {e: h[-1] for e, h in hold.history.items()}
                    log.write(
                        {"kind": "engine_status", "n": n, "utc": rec["utc"], "status": rec_status}
                    )
                else:
                    events = hold.observe(at=obs.at, clean=obs.clean)
                for event in events:
                    log.write(event_record(event))
                next_at = obs.at + hold.wait_s - (time.time() - obs.at)
            else:
                for event in silent.observe(at=obs.at, clean=obs.clean):
                    log.write(event_record(event))
                if not silent.done:
                    next_at = silent.next_search_at(obs.at)

        if args.mode == "hold" and isinstance(hold, SetHoldController):
            summary: dict[str, Any] = {
                "outcome": hold.outcome,
                "searches": len(hold.times),
                "hours": round((hold.times[-1] - hold.times[0]) / 3600, 3) if hold.times else 0,
                "survival": hold.survival(),
                "late_gaps_s": [round(g, 1) for g in hold.late_gaps_s],
                "keep_awake_accepted": awake,
            }
        elif args.mode == "hold":
            assert isinstance(hold, HoldController)  # noqa: S101 - narrows for the type checker
            summary = {
                "outcome": hold.outcome,
                "searches": len(hold.times),
                "clean": hold.clean_count,
                "max_requests_in_any_trailing_hour": hold.max_trailing,
                "hours": round((hold.times[-1] - hold.times[0]) / 3600, 3) if hold.times else 0,
                "late_gaps_s": [round(g, 1) for g in hold.late_gaps_s],
                "keep_awake_accepted": awake,
            }
        else:
            summary = {
                "outcome": silent.outcome,
                "searches_in_the_burst": silent.provoked,
                "first_clean_after_min": silent.first_clean_delay_min,
                "last_refused_after_min": silent.last_refused_delay_min,
                "recovered_between_min": [
                    silent.last_refused_delay_min or 0.0,
                    silent.first_clean_delay_min,
                ]
                if silent.first_clean_delay_min is not None
                else None,
                "confirmed": silent.confirmed,
            }
        log.write({"kind": "summary", "utc": datetime.now(UTC).isoformat(), **summary})
    print(f"log: {log.path}")


if __name__ == "__main__":
    asyncio.run(main())
