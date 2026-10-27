"""What the hold and silent-wait probes share (docs/evaluation-plan.md section 8).

Kept apart from `searxng_ramp_probe.py`, which is NOT refactored: the ramp's data was produced by
that script as it stood on 2026-10-27, and changing it afterwards would leave the data without the
code that made it. The few lines these helpers repeat from it are the price of that provenance.
"""

import ctypes
import itertools
import json
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.errors import SearchRateLimited, SearchUnavailable
from app.research.rate_ramp import Event
from app.research.search import SearxngSearch
from scripts.searxng_rate_probe import ASPECTS, PRODUCTS, REGULATED


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


def keep_awake() -> bool:
    """Ask Windows not to put the machine to sleep because it is idle, for as long as this process
    runs (ES_CONTINUOUS | ES_SYSTEM_REQUIRED; released when the process exits). On 2026-10-27 the
    machine slept for 80 minutes in the middle of a timed run and the schedule slipped with it.
    This stops IDLE sleep only: a closed lid or a manual sleep still sleeps, which is why the hold
    also detects and records late asks. Returns whether the request was accepted."""
    if sys.platform != "win32":
        return False
    es_continuous, es_system_required = 0x80000000, 0x00000001
    previous = ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
        es_continuous | es_system_required
    )
    return bool(previous)


def memory_available_gb() -> float | None:
    """Physical memory available to the machine, in GB (Windows). Logged with every search so a
    run that is killed comes with a timeline (Architecture.md section 11, "Worker-host memory")."""
    if sys.platform != "win32":
        return None

    class Status(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_phys", ctypes.c_ulonglong),
            ("avail_phys", ctypes.c_ulonglong),
            ("total_page", ctypes.c_ulonglong),
            ("avail_page", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("avail_virtual", ctypes.c_ulonglong),
            ("avail_extended", ctypes.c_ulonglong),
        ]

    status = Status()
    status.length = ctypes.sizeof(Status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return None
    return round(status.avail_phys / 2**30, 2)


def queries(seed: int, regulated_start: int) -> list[tuple[str, str]]:
    """(group, query), alternating, every query distinct. The regulated list is a fixed list, so
    `regulated_start` skips the entries earlier runs used (the first five were used by the 9-search
    probe and the ramp), and the general list is shuffled by `seed`, which differs per run."""
    pairs = list(itertools.product(PRODUCTS, ASPECTS))
    random.Random(seed).shuffle(pairs)  # noqa: S311 - a fixed order, not a secret
    general = [("general", f"{p} {a}") for p, a in pairs]
    regulated = [("regulated", q) for q in REGULATED[regulated_start:]]
    merged: list[tuple[str, str]] = []
    for i in range(max(len(general), len(regulated))):
        merged += regulated[i : i + 1] + general[i : i + 1]
    return merged


@dataclass(frozen=True, slots=True)
class Observation:
    record: dict[str, Any]
    clean: bool  # SearXNG answered (HTTP 200) and the engine was not named as failed
    answered: bool  # SearXNG answered at all
    at: float  # epoch seconds when it finished


async def observe(
    search: SearxngSearch, last: dict[str, Any], *, engine: str, n: int, group: str, query: str
) -> Observation:
    """One search, and everything worth logging about it (the same fields as the ramp's)."""
    began = time.monotonic()
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
                key = str(name).casefold()
                rows_by_engine[key] = rows_by_engine.get(key, 0) + 1
    answered = last.get("status") == 200
    named = {str(u[0]).casefold(): str(u[1]) for u in failed if len(u) == 2}
    clean = answered and engine not in named
    now = time.time()
    record = {
        "kind": "search",
        "n": n,
        "utc": datetime.fromtimestamp(now, UTC).isoformat(),
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
        "mem_available_gb": memory_available_gb(),
        "urls": [[r.url, r.engine] for r in found],
    }
    return Observation(record, clean, answered, now)
