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
from typing import Any, Literal

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


# ── The sustained hold and the silent-wait recovery (owner, 2026-10-27) ──────────────────────
#
# The ramp finds where to look; it does not certify a rate (five clean searches over twenty minutes
# is a burst, and rate and count are confounded in it). These two are the decisive tests, run after
# the ramp and in this order:
#
# 1. HOLD at one fixed gap for hours. If the steering engine stays clean for 24+ searches over 4+
#    hours at 10-minute gaps (6 an hour), 6 an hour is a MEASURED sustained rate, the only kind a
#    planning figure may rest on. Stops at the first degradation: a hold that breaks says so.
# 2. SILENT-WAIT recovery. Provoke a throttle with a burst, then STOP ASKING and probe once at
#    growing delays. If recovery is minutes when nobody asks and hours when somebody keeps asking,
#    the recording must PAUSE on a throttle rather than retry, which is a rule of the procedure,
#    not a tuning detail. The result is an interval (between the last refused probe and the first
#    clean one), never a point: that is the resolution the schedule gives.

WINDOW_S = 3600.0
SILENT_DELAYS_MIN = (5.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0, 90.0, 120.0, 180.0)


def trailing_count(times: list[float], at: float, window_s: float = WINDOW_S) -> int:
    """How many requests (answered or refused) fall in the `window_s` seconds up to and including
    `at`: the EARLIER requests in `times`, plus `at` itself, which `times` must not yet contain.
    The quantity a rolling-window hypothesis is about."""
    return sum(1 for t in [*times, at] if at - window_s < t <= at)


class HoldController:
    """Fixed gap, stop at the first degradation, done after `min_searches` AND `min_hours`."""

    def __init__(
        self, *, interval_min: float = 10.0, min_searches: int = 24, min_hours: float = 4.0
    ) -> None:
        if interval_min <= 0 or min_searches < 1 or min_hours <= 0:
            raise ValueError("interval_min, min_searches and min_hours must be positive")
        self.interval_min, self.min_searches, self.min_hours = interval_min, min_searches, min_hours
        self.times: list[float] = []  # every request, answered or refused
        self.clean_count = 0
        self.max_trailing = 0
        self.broke_at: float | None = None
        # A gap longer than this many intervals is LATE: the machine slept, or something stalled.
        # More time between asks only gives a limit longer to refill, so a late gap can make a pass
        # easier and never a break, which is why it cannot certify one.
        self.late_factor = 1.5
        self.late_gaps_s: list[float] = []
        self.outcome: Literal["held", "held_with_gaps", "broke"] | None = None

    @property
    def done(self) -> bool:
        return self.outcome is not None

    @property
    def wait_s(self) -> float:
        return self.interval_min * 60

    def observe(self, *, at: float, clean: bool) -> list[Event]:
        if self.done:
            raise ValueError("the hold is finished")
        in_window = trailing_count(self.times, at)
        first = self.times[0] if self.times else at
        events: list[Event] = []
        if self.times and at - self.times[-1] > self.late_factor * self.interval_min * 60:
            gap = at - self.times[-1]
            self.late_gaps_s.append(gap)
            events.append(
                Event(
                    "late_ask",
                    at,
                    {
                        "gap_s": round(gap, 1),
                        "planned_s": self.interval_min * 60,
                        "search_number": len(self.times) + 1,
                    },
                )
            )
        self.times.append(at)
        self.max_trailing = max(self.max_trailing, in_window)
        if not clean:
            self.broke_at, self.outcome = at, "broke"
            return [
                *events,
                Event(
                    "hold_broke",
                    at,
                    {
                        "search_number": len(self.times),
                        "clean_before": self.clean_count,
                        "requests_in_trailing_hour_including_this": in_window,
                        "after_s": at - first,
                        "late_gaps": len(self.late_gaps_s),
                    },
                ),
            ]
        self.clean_count += 1
        elapsed = at - first
        if len(self.times) >= self.min_searches and elapsed >= self.min_hours * 3600:
            self.outcome = "held_with_gaps" if self.late_gaps_s else "held"
            return [
                *events,
                Event(
                    "hold_held" if not self.late_gaps_s else "hold_held_with_gaps",
                    at,
                    {
                        "searches": len(self.times),
                        "hours": round(elapsed / 3600, 3),
                        "max_requests_in_any_trailing_hour": self.max_trailing,
                        "late_gaps_s": [round(g, 1) for g in self.late_gaps_s],
                    },
                ),
            ]
        return events


class SilentRecoveryController:
    """Provoke a throttle with a burst, go silent, probe once at each delay after the break.

    Phases: `provoke` (a search every `provoke_gap_min` until the engine degrades, at most
    `provoke_cap` of them; if it never does, that is a finding about the burst ceiling and the run
    ends), `silent` (no searches; one probe at each delay measured from the break), `confirm` (one
    more probe `confirm_after_min` after the first clean one, to see that it held), `done`."""

    def __init__(
        self,
        *,
        provoke_gap_min: float = 2.0,
        provoke_cap: int = 15,
        delays_min: tuple[float, ...] = SILENT_DELAYS_MIN,
        confirm_after_min: float = 10.0,
    ) -> None:
        if provoke_gap_min <= 0 or provoke_cap < 1 or confirm_after_min <= 0:
            raise ValueError("gaps and caps must be positive")
        if not delays_min or list(delays_min) != sorted(set(delays_min)) or delays_min[0] <= 0:
            raise ValueError("delays must be positive and strictly increasing")
        self.provoke_gap_min, self.provoke_cap = provoke_gap_min, provoke_cap
        self.delays_min, self.confirm_after_min = delays_min, confirm_after_min
        self.phase: Literal["provoke", "silent", "confirm", "done"] = "provoke"
        self.provoked = 0
        self.broke_at: float | None = None
        self.skipped_delays_min: list[float] = []
        self.probe_index = 0
        self.first_clean_delay_min: float | None = None
        self.last_refused_delay_min: float | None = None
        self.first_clean_at: float | None = None
        self.confirmed: bool | None = None
        self.outcome: (
            Literal["recovered", "not_recovered_within_schedule", "no_break_provoked"] | None
        ) = None

    @classmethod
    def after_a_break(
        cls, *, broke_at: float, now: float, margin_s: float = 5.0, **kw: Any
    ) -> "SilentRecoveryController":
        """Begin in silence from a throttle that ALREADY happened (a hold that broke, say) instead
        of provoking a new one: nothing has been asked since `broke_at`, so the recovery measured is
        from that break. Probe delays already past (within `margin_s` of now) are skipped, not made
        late: a probe at the wrong delay would be a different measurement. The skipped delays are
        recorded and the interval's lower bound stays 0 (the break itself was a refusal)."""
        ctl = cls(**kw)
        ctl.broke_at, ctl.phase = broke_at, "silent"
        ctl.skipped_delays_min = [d for d in ctl.delays_min if broke_at + d * 60 <= now + margin_s]
        ctl.probe_index = len(ctl.skipped_delays_min)
        if ctl.probe_index == len(ctl.delays_min):
            ctl.phase, ctl.outcome = "done", "not_recovered_within_schedule"
        return ctl

    @property
    def done(self) -> bool:
        return self.phase == "done"

    def next_search_at(self, now: float) -> float:
        """Epoch time of the next search (the first one is `now`). In `silent` this is the next
        probe time: between probes NOTHING is requested."""
        if self.phase == "provoke":
            return now + self.provoke_gap_min * 60 if self.provoked else now
        if self.phase == "silent" and self.broke_at is not None:
            return self.broke_at + self.delays_min[self.probe_index] * 60
        if self.phase == "confirm" and self.first_clean_at is not None:
            return self.first_clean_at + self.confirm_after_min * 60
        raise ValueError("the run is finished")

    def observe(self, *, at: float, clean: bool) -> list[Event]:
        if self.phase == "provoke":
            self.provoked += 1
            if not clean:
                self.broke_at, self.phase = at, "silent"
                return [
                    Event(
                        "break",
                        at,
                        {"searches_in_the_burst": self.provoked, "gap_min": self.provoke_gap_min},
                    ),
                    Event("silence_begins", at, {"first_probe_after_min": self.delays_min[0]}),
                ]
            if self.provoked >= self.provoke_cap:
                self.phase, self.outcome = "done", "no_break_provoked"
                return [Event("no_break_provoked", at, {"searches": self.provoked})]
            return []
        if self.phase == "silent":
            if self.broke_at is None:
                raise RuntimeError("silent phase without a break")
            delay = self.delays_min[self.probe_index]
            if clean:
                self.first_clean_delay_min, self.first_clean_at = delay, at
                self.phase = "confirm"
                return [
                    Event(
                        "first_clean",
                        at,
                        {
                            "recovered_between_min": [self.last_refused_delay_min or 0.0, delay],
                            "after_s": at - self.broke_at,
                        },
                    )
                ]
            self.last_refused_delay_min = delay
            self.probe_index += 1
            if self.probe_index == len(self.delays_min):
                self.phase, self.outcome = "done", "not_recovered_within_schedule"
                return [Event("not_recovered", at, {"last_probe_after_min": delay})]
            return [Event("still_throttled", at, {"after_min": delay})]
        if self.phase == "confirm":
            self.confirmed = clean
            self.phase, self.outcome = "done", "recovered"
            return [Event("confirmed" if clean else "flapped", at, {"clean": clean})]
        raise ValueError("the run is finished")


# ── Which description does a run of asks fit? (registered 2026-10-27 06:43 UTC) ──────────────
#
# docs/evaluation-plan.md section 8 registered, before the ten asks 06:44..07:29 were made, what a
# trickle (a bucket-like limit) and a window each predict. This applies exactly those rules to the
# observed sequence, so the verdict is not a reading. `T` is an answer, `X` a refusal.


def classify_pattern(
    sequence: str, *, ask_gap_min: float = 5.0, min_apart_min: float = 20.0
) -> str:
    """ "window" (two or more answers in a row), "trickle" (isolated answers, at most three, any two
    at least `min_apart_min` apart), "undecided" (no answer at all: both descriptions allow it), or
    "neither"."""
    if set(sequence) - {"T", "X"}:
        raise ValueError("a sequence is made of T and X only")
    if "T" not in sequence:
        return "undecided"
    if "TT" in sequence:
        return "window"
    at = [i for i, c in enumerate(sequence) if c == "T"]
    apart = all((b - a) * ask_gap_min >= min_apart_min for a, b in zip(at, at[1:], strict=False))
    return "trickle" if len(at) <= 3 and apart else "neither"
