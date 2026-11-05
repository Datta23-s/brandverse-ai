"""Re-running an attempt that an infrastructure failure ended, and recording the invalidation.

Owner ruling, 5 November 2026 (docs/evaluation-plan.md, Amendment 1). A run that would end
`completed` while carrying a `provider_unavailable` declared gap is INVALIDATED and re-run: the
gap is the provider's outage, not the arm's behaviour, and leaving it in would depress whichever
arm the outage touched.

WHO DECIDES. The classifier, automatically, from the gap's kind alone, at run end, before any human
sees the output (`Pipeline._invalidated`, `core.infrastructure.invalidating_gaps`). Nobody reads the
copy and then decides to re-run. This module holds the two pieces that come after that decision:

* the CAP: at most 2 re-runs per campaign; a third failure comes to the owner, it is not re-run;
* the RECORD: every invalidation, with the outage window it overlapped and the re-run that
  replaced it, so none is silent.

The attempt rule itself is unchanged: the primary result for a request-arm pair is the first attempt
that ended without infrastructure failure; superseded attempts stay in the table with their reason.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.evaluation.outages import OutageWindow

MAX_RERUNS = 2

Action = Literal["accept", "rerun", "escalate"]


@dataclass(frozen=True, slots=True)
class Attempt:
    number: int
    infrastructure_failure: str | None  # None: the attempt ended without infrastructure failure


def next_action(attempts: Sequence[Attempt]) -> Action:
    """What the harness does with a campaign's attempts so far.

    `accept`: some attempt ended without infrastructure failure; the first is the primary result.
    `rerun`: every attempt so far failed on infrastructure and the cap is not reached.
    `escalate`: the original and both re-runs failed on infrastructure: it comes to the owner."""
    if not attempts:
        raise ValueError("a campaign with no attempt has nothing to decide")
    if any(a.infrastructure_failure is None for a in attempts):
        return "accept"
    reruns_done = len(attempts) - 1
    return "rerun" if reruns_done < MAX_RERUNS else "escalate"


def primary(attempts: Sequence[Attempt]) -> Attempt | None:
    """The first attempt (lowest number) that ended without infrastructure failure, or None."""
    for attempt in sorted(attempts, key=lambda a: a.number):
        if attempt.infrastructure_failure is None:
            return attempt
    return None


@dataclass(frozen=True, slots=True)
class Invalidation:
    """One invalidated run, with the outage window it overlapped and the run that replaced it."""

    run_id: UUID
    kind: str
    infrastructure_failure: str
    gaps: tuple[Mapping[str, Any], ...]
    windows: tuple[OutageWindow, ...]
    rerun_run_id: UUID | None
    # An invalidation with no recorded window is an anomaly to look at, not a pass: either the
    # outage record is incomplete or the classification fired for another reason.
    window_found: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "window_found", bool(self.windows))


def invalidations(
    runs: Iterable[tuple[UUID, datetime, datetime, Mapping[str, Any] | None, UUID | None]],
    windows: Sequence[OutageWindow],
) -> list[Invalidation]:
    """Every invalidated run among `runs` = (run_id, started, ended, runs.failure, rerun_run_id)."""
    found: list[Invalidation] = []
    for run_id, started, ended, failure, rerun in runs:
        if not failure or not failure.get("invalidated"):
            continue
        found.append(
            Invalidation(
                run_id=run_id,
                kind=str(failure.get("kind")),
                infrastructure_failure=str(failure.get("infrastructure_failure")),
                gaps=tuple(failure.get("gaps") or ()),
                windows=tuple(w for w in windows if w.overlaps(started, ended)),
                rerun_run_id=rerun,
            )
        )
    return found
