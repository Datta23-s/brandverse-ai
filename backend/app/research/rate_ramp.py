"""The search-rate ramp (docs/evaluation-plan.md section 8; owner, 2026-10-26): how fast one search
engine can be asked before it throttles, and how long it takes to recover. Pure logic, no network.

Why a ramp and not a fixed interval: a fixed 10-minute spacing measured only itself (brave answered
9 of 9, so its ceiling was never approached) and gave no recovery datapoint. The ramp steps the gap
between searches down until the engine breaks, then backs off and times the recovery:

1. Start at the first level (minutes between searches) and make `per_level` searches at it. If every
   one is clean, step to the next, shorter level.
2. **Stop stepping at the first degradation.** That search is the break; the level it happened at is
   the level that broke. The highest *sustainable* rate is the last level at which all `per_level`
   searches were clean, which may be the level before the broken one, never the broken one itself.
3. **Back off** to that last clean level (to `fallback_interval_min` if the very first level broke:
   there is no clean level to return to) and keep searching at it until the engine answers cleanly
   again. The time from the break to the first clean answer is the **recovery time**; the answer is
   only believed once `confirm_clean` clean answers in a row have followed (a single clean one can
   be a gap in a throttle), and a degraded answer in between resets the run.
4. A recovery that has not happened within `recovery_cap_s` of the break is recorded as not
   recovered, not waited on for ever.

Only ONE engine decides whether an observation is clean (the runner passes `clean`): duckduckgo
CAPTCHAs every search and wikipedia contributes nothing, so neither can steer the stepping
(owner, 2026-10-26). If every level is clean the ceiling was not reached, and the report says the
highest level *tested* instead of inventing a ceiling.
"""

from dataclasses import dataclass, field
from typing import Literal

LEVELS_MIN = (5.0, 4.0, 3.0, 2.0, 1.5, 1.0, 0.75, 0.5)  # minutes between searches, stepping down
Phase = Literal["ramp", "backoff", "done"]
Outcome = Literal["recovered", "not_recovered_within_cap", "ceiling_not_reached"]


@dataclass(frozen=True, slots=True)
class Event:
    # level_start, break, backoff, first_clean, recovered, not_recovered, ceiling_not_reached
    kind: str
    at: float  # epoch seconds of the observation that caused it
    detail: dict[str, object] = field(default_factory=dict)


class RampController:
    def __init__(
        self,
        *,
        levels: tuple[float, ...] = LEVELS_MIN,
        per_level: int = 5,
        confirm_clean: int = 2,
        recovery_cap_s: float = 3 * 3600.0,
        fallback_interval_min: float = 10.0,
    ) -> None:
        if not levels or per_level < 1 or confirm_clean < 1:
            raise ValueError("levels, per_level and confirm_clean must be positive")
        if list(levels) != sorted(levels, reverse=True) or len(set(levels)) != len(levels):
            raise ValueError("levels must strictly decrease: the ramp steps the gap DOWN")
        self.levels, self.per_level = levels, per_level
        self.confirm_clean, self.recovery_cap_s = confirm_clean, recovery_cap_s
        self.fallback_interval_min = fallback_interval_min
        self.phase: Phase = "ramp"
        self.level_index = 0
        self.clean_at_level = 0
        self.broke_at_level: float | None = None
        self.broke_at: float | None = None
        self.last_clean_level: float | None = None  # last level with ALL per_level clean
        self.backoff_interval_min: float | None = None
        self.first_clean_at: float | None = None  # first clean answer of the confirmed run
        self.clean_run = 0
        self.outcome: Outcome | None = None
        self.searches = 0

    @property
    def interval_min(self) -> float:
        """Minutes to wait before the NEXT search (start to start)."""
        if self.phase == "backoff":
            if self.backoff_interval_min is None:
                raise RuntimeError("backoff without an interval")
            return self.backoff_interval_min
        return self.levels[min(self.level_index, len(self.levels) - 1)]

    @property
    def done(self) -> bool:
        return self.phase == "done"

    def start_event(self, at: float) -> Event:
        return Event("level_start", at, {"level_min": self.levels[0]})

    def observe(self, *, at: float, clean: bool) -> list[Event]:
        """One search's result for the deciding engine. Returns the events it caused."""
        if self.done:
            raise ValueError("the ramp is finished")
        self.searches += 1
        return self._ramp(at, clean) if self.phase == "ramp" else self._backoff(at, clean)

    def _ramp(self, at: float, clean: bool) -> list[Event]:
        level = self.levels[self.level_index]
        if not clean:
            self.broke_at_level, self.broke_at = level, at
            self.backoff_interval_min = self.last_clean_level or self.fallback_interval_min
            self.phase = "backoff"
            return [
                Event(
                    "break",
                    at,
                    {
                        "level_min": level,
                        "clean_searches_at_this_level": self.clean_at_level,
                        "of": self.per_level,
                        "last_fully_clean_level_min": self.last_clean_level,
                    },
                ),
                Event(
                    "backoff",
                    at,
                    {
                        "interval_min": self.backoff_interval_min,
                        "reason": "last fully clean level"
                        if self.last_clean_level
                        else "no clean level exists: fallback interval",
                    },
                ),
            ]
        self.clean_at_level += 1
        if self.clean_at_level < self.per_level:
            return []
        self.last_clean_level = level  # all per_level clean
        self.clean_at_level = 0
        self.level_index += 1
        if self.level_index == len(self.levels):
            self.phase, self.outcome = "done", "ceiling_not_reached"
            return [Event("ceiling_not_reached", at, {"highest_level_tested_min": level})]
        return [Event("level_start", at, {"level_min": self.levels[self.level_index]})]

    def _backoff(self, at: float, clean: bool) -> list[Event]:
        if self.broke_at is None:
            raise RuntimeError("backoff without a break")
        events: list[Event] = []
        if clean:
            if self.clean_run == 0:
                self.first_clean_at = at
                events.append(Event("first_clean", at, {"after_s": at - self.broke_at}))
            self.clean_run += 1
            if self.clean_run >= self.confirm_clean:
                if self.first_clean_at is None:
                    raise RuntimeError("confirmed recovery without a first clean answer")
                self.phase, self.outcome = "done", "recovered"
                events.append(
                    Event(
                        "recovered",
                        at,
                        {
                            "recovery_s": self.first_clean_at - self.broke_at,
                            "confirmed_by_clean_in_a_row": self.clean_run,
                        },
                    )
                )
                return events
        else:
            self.clean_run, self.first_clean_at = 0, None  # a degraded answer resets the run
        if at - self.broke_at > self.recovery_cap_s:
            self.phase, self.outcome = "done", "not_recovered_within_cap"
            events.append(Event("not_recovered", at, {"cap_s": self.recovery_cap_s}))
        return events
