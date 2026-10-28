"""The recorder's session loop (docs/evaluation-plan.md section 8, "The recorder is adaptive, not
scheduled"; owner, 2026-10-27 and 2026-10-28): capture the pending requests, one at a time, in the
test set's order, into the evaluation snapshot.

**Adaptive, not scheduled.** No constant described the engines that throttled this IP, and the ones
that sustained 2-minute gaps were observed for 91 minutes, so the recorder does not trust a rate.
It asks at a *gap* between live searches (`PacedSearch`), stretches that gap when a request is
refused (multiplicative increase of the gap), and shortens it again after a run of clean searches
(additive decrease), never below the starting gap: ordinary congestion control, which needs no model
of why the limit moves, only the signal that it did.

**A refused request is discarded and redone** (`Mode.RECORD` aborts it at once; an atomic capture
never continues). The redo waits a delay that grows on every consecutive refusal, from a base to a
ceiling, with jitter, so retries never form a tight loop. Searches that were clean inside the
discarded attempt are lost with it: the cost of the atomic rule, accepted by the owner, and it is
reported (`searches_wasted`).

**A session ends cleanly**, not by crashing: when every request is committed (`complete`), when no
search has been answered cleanly for `recorder_dark_ceiling_s` (`dark_period_ceiling`: a dead night
ends instead of burning attempts into a ban), or after `recorder_wall_limit_s` (`wall_limit`). The
in-flight request is discarded, and the store says where to resume (`store.pending`).

**It logs its own measurement.** Every attempt writes a line: when, which request, the outcome, the
delay it waited, the gap in force, the clean searches in the trailing hour and since the session
began (the achieved rate), and the live searches by kind (clean, throttled, unavailable, degraded,
with the declared engine that caused each degraded one). The degraded-search rate is the number the
first recording session exists to measure: 45 searches gave a bound, not an answer.

Everything the clock and the pauses touch is injected, so the loop is tested without waiting.
"""

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.memory.scoring import Research, SourceScoring
from app.research.extraction import Extractor
from app.research.fetch import PageSource
from app.research.planner import QueryPlan
from app.research.search import SearchProvider, SearchResponse
from app.research.snapshot import CaptureReport, CaptureTally, SnapshotStore, record_request

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class RecorderConfig:
    """The session loop's parameters: all untuned defaults, recorded with every session."""

    search_gap_s: float
    gap_ceiling_s: float
    gap_increase_factor: float
    gap_decrease_step_s: float
    clean_run_to_speed_up: int
    backoff_base_s: float
    backoff_max_s: float
    backoff_jitter: float
    dark_ceiling_s: float
    wall_limit_s: float
    snapshot_page_retries: int

    @classmethod
    def from_research(cls, research: Research) -> "RecorderConfig":
        return cls(
            search_gap_s=research.recorder_search_gap_s,
            gap_ceiling_s=research.recorder_gap_ceiling_s,
            gap_increase_factor=research.recorder_gap_increase_factor,
            gap_decrease_step_s=research.recorder_gap_decrease_step_s,
            clean_run_to_speed_up=research.recorder_clean_run_to_speed_up,
            backoff_base_s=research.recorder_backoff_base_s,
            backoff_max_s=research.recorder_backoff_max_s,
            backoff_jitter=research.recorder_backoff_jitter,
            dark_ceiling_s=research.recorder_dark_ceiling_s,
            wall_limit_s=research.recorder_wall_limit_s,
            snapshot_page_retries=research.snapshot_page_retries,
        )


class Pacer:
    """The gap between LIVE searches, adjusted by what the engines do. Starts at the estimate and is
    never faster than it."""

    def __init__(self, cfg: RecorderConfig, clock: Clock, sleep: Sleep) -> None:
        self._cfg, self._clock, self._sleep = cfg, clock, sleep
        self.gap_s = cfg.search_gap_s
        self._last_live: float | None = None
        self._clean_run = 0
        self.last_clean_at: float | None = None  # when a live search last came back clean

    async def wait(self) -> None:
        if self._last_live is not None:
            due = self._last_live + self.gap_s
            now = self._clock()
            if due > now:
                await self._sleep(due - now)
        self._last_live = self._clock()

    def refused(self) -> None:
        self._clean_run = 0
        self.gap_s = min(self._cfg.gap_ceiling_s, self.gap_s * self._cfg.gap_increase_factor)

    def clean(self) -> None:
        self.last_clean_at = self._clock()
        self._clean_run += 1
        if self._clean_run >= self._cfg.clean_run_to_speed_up:
            self._clean_run = 0
            self.gap_s = max(self._cfg.search_gap_s, self.gap_s - self._cfg.gap_decrease_step_s)


class PacedSearch:
    """A search provider that waits its turn before every live call. Only calls that reach it are
    live: an answer already in the store never does, and costs no wait."""

    def __init__(self, inner: SearchProvider, pacer: Pacer) -> None:
        self._inner, self._pacer = inner, pacer

    async def search(self, query: str, *, limit: int = 10) -> SearchResponse:
        await self._pacer.wait()
        try:
            response = await self._inner.search(query, limit=limit)
        except Exception:
            self._pacer.refused()
            raise
        if response.degraded:
            self._pacer.refused()
        else:
            self._pacer.clean()
        return response


@dataclass(slots=True)
class SessionReport:
    ended: str = "complete"  # complete | dark_period_ceiling | wall_limit
    started_utc: str = ""
    elapsed_s: float = 0.0
    config: dict[str, Any] = field(default_factory=dict)
    requests_committed: list[str] = field(default_factory=list)
    requests_pending: list[str] = field(default_factory=list)
    attempts: int = 0
    aborted_attempts: int = 0
    waited_s: float = 0.0
    searches_clean: int = 0
    searches_throttled: int = 0
    searches_unavailable: int = 0
    searches_degraded: int = 0
    searches_reused: int = 0
    searches_wasted: int = 0  # clean searches inside attempts that were then discarded
    degraded_causes: dict[str, int] = field(default_factory=dict)
    pages_new_ok: int = 0
    pages_reused: int = 0
    pages_frozen_failed: int = 0
    pages_total: int = 0
    retries_used: int = 0
    final_gap_s: float = 0.0
    capture_reports: list[CaptureReport] = field(default_factory=list)

    @property
    def searches_live(self) -> int:
        return (
            self.searches_clean
            + self.searches_throttled
            + self.searches_unavailable
            + self.searches_degraded
        )

    @property
    def degraded_search_rate(self) -> float | None:
        """Degraded over live searches issued: the figure the first session exists to measure."""
        return self.searches_degraded / self.searches_live if self.searches_live else None

    @property
    def achieved_clean_per_hour(self) -> float | None:
        return self.searches_clean / (self.elapsed_s / 3600) if self.elapsed_s > 0 else None

    @property
    def frozen_failed_share(self) -> float | None:
        return self.pages_frozen_failed / self.pages_total if self.pages_total else None

    def summary(self) -> dict[str, Any]:
        return {
            "ended": self.ended,
            "started_utc": self.started_utc,
            "elapsed_s": round(self.elapsed_s, 1),
            "committed": self.requests_committed,
            "pending": self.requests_pending,
            "attempts": self.attempts,
            "aborted_attempts": self.aborted_attempts,
            "waited_s": round(self.waited_s, 1),
            "searches_live": self.searches_live,
            "searches_clean": self.searches_clean,
            "searches_throttled": self.searches_throttled,
            "searches_unavailable": self.searches_unavailable,
            "searches_degraded": self.searches_degraded,
            "searches_reused": self.searches_reused,
            "searches_wasted": self.searches_wasted,
            "degraded_search_rate": self.degraded_search_rate,
            "degraded_causes": dict(sorted(self.degraded_causes.items())),
            "achieved_clean_per_hour": self.achieved_clean_per_hour,
            "pages_total": self.pages_total,
            "pages_new_ok": self.pages_new_ok,
            "pages_reused": self.pages_reused,
            "pages_frozen_failed": self.pages_frozen_failed,
            "frozen_failed_share": self.frozen_failed_share,
            "retries_used": self.retries_used,
            "final_gap_s": self.final_gap_s,
            "config": self.config,
        }


def _add(report: SessionReport, tally: CaptureTally) -> None:
    report.searches_clean += tally.searches_new
    report.searches_throttled += tally.searches_throttled
    report.searches_unavailable += tally.searches_unavailable
    report.searches_degraded += tally.searches_degraded
    report.searches_reused += tally.searches_reused
    report.retries_used += tally.retries_used
    for engine, n in tally.degraded_causes.items():
        report.degraded_causes[engine] = report.degraded_causes.get(engine, 0) + n


async def record_session(
    plans: Mapping[str, QueryPlan],
    *,
    store: SnapshotStore,
    search: SearchProvider,
    pages: PageSource,
    extractor: Extractor,
    source_cfg: SourceScoring,
    declared: Iterable[str] | None,
    cfg: RecorderConfig,
    today: date,
    log_path: Path | None = None,
    clock: Clock = time.monotonic,
    sleep: Sleep = asyncio.sleep,
    rng: random.Random | None = None,
    concurrency: int = 8,
) -> SessionReport:
    """Capture every pending request in `plans` (in its order) until done, or the session ends."""
    rng = rng or random.Random()  # noqa: S311 - jitter, not a secret
    report = SessionReport(
        started_utc=datetime.now(UTC).isoformat(),
        config={k: getattr(cfg, k) for k in cfg.__slots__},
    )
    began = clock()
    pacer = Pacer(cfg, clock, sleep)
    paced = PacedSearch(search, pacer)
    consecutive_refusals = 0
    pending = store.pending(plans)

    def log(event: dict[str, Any]) -> None:
        if log_path is None:
            return
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"utc": datetime.now(UTC).isoformat(), **event}) + "\n")

    log({"kind": "session_start", "pending": pending, "config": report.config})
    index = 0
    while index < len(pending):
        request_id = pending[index]
        if clock() - began >= cfg.wall_limit_s:
            report.ended = "wall_limit"
            break
        # "no search has been ANSWERED for the ceiling": a clean search anywhere counts, committed
        # request or not, so a request refused near its end does not start the dark clock
        last_clean = max(began, pacer.last_clean_at or began)
        if clock() - last_clean >= cfg.dark_ceiling_s:
            report.ended = "dark_period_ceiling"
            break
        report.attempts += 1
        attempt_began = clock()
        capture = await record_request(
            request_id,
            plans[request_id],
            store=store,
            search=paced,
            pages=pages,
            extractor=extractor,
            source_cfg=source_cfg,
            declared=declared,
            retries=cfg.snapshot_page_retries,
            today=today,
            concurrency=concurrency,
            sleep=sleep,
        )
        _add(report, capture.tally)
        report.capture_reports.append(capture)
        waited = 0.0
        if capture.outcome == "committed":
            consecutive_refusals = 0
            report.requests_committed.append(request_id)
            report.pages_total += capture.pages_total
            report.pages_frozen_failed += capture.frozen_failed
            report.pages_new_ok += capture.tally.pages_new_ok
            report.pages_reused += capture.tally.pages_reused
            index += 1
        else:
            report.aborted_attempts += 1
            report.searches_wasted += capture.tally.searches_new
            consecutive_refusals += 1
            base = min(cfg.backoff_max_s, cfg.backoff_base_s * 2 ** (consecutive_refusals - 1))
            waited = base * (1 + cfg.backoff_jitter * rng.random())
            report.waited_s += waited
        elapsed = clock() - began
        report.elapsed_s = elapsed
        log(
            {
                "kind": "attempt",
                "request": request_id,
                "outcome": capture.outcome,
                "abort_reason": capture.abort_reason,
                "attempt_s": round(clock() - attempt_began, 1),
                "waited_next_s": round(waited, 1),
                "gap_s": pacer.gap_s,
                "searches_clean_since_start": report.searches_clean,
                "achieved_clean_per_hour": report.achieved_clean_per_hour,
                "degraded_search_rate": report.degraded_search_rate,
                "searches_degraded": report.searches_degraded,
                "degraded_causes": report.degraded_causes,
                "frozen_failed": capture.frozen_failed,
                "pages_total": capture.pages_total,
                "retries_used": capture.tally.retries_used,
            }
        )
        if capture.outcome != "committed":
            await sleep(waited)
    else:
        report.ended = "complete"
    report.elapsed_s = clock() - began
    report.final_gap_s = pacer.gap_s
    report.requests_pending = store.pending(plans)
    log({"kind": "session_end", **report.summary()})
    return report
