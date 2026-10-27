"""The search-rate ramp's stepping logic (app/research/rate_ramp.py): pure, no network.

What must hold, because the §8 recording-phase figure rests on it: the gap steps DOWN through the
levels, five searches at each; stepping stops at the first degradation; the sustainable level is the
last level that was *fully* clean, never the one that broke; recovery is timed from the break to the
first clean answer and only believed once confirmed.
"""

import pytest

from app.research.rate_ramp import LEVELS_MIN, RampController


def drive(ctl: RampController, results: list[bool], *, start: float = 0.0) -> list[float]:
    """Feed observations, waiting each time the interval the controller asked for. Returns the
    waits (minutes) it requested before each search after the first."""
    now, waits = start, []
    for n, clean in enumerate(results):
        if n:
            waits.append(ctl.interval_min)
            now += ctl.interval_min * 60
        ctl.observe(at=now, clean=clean)
        if ctl.done:
            break
    return waits


def test_the_gap_steps_down_five_searches_at_a_time_through_every_level() -> None:
    ctl = RampController()
    waits = drive(ctl, [True] * 40)
    assert ctl.done and ctl.outcome == "ceiling_not_reached" and ctl.searches == 40
    # The wait before search n (n >= 1) is the level search n belongs to.
    expected = [level for level in LEVELS_MIN for _ in range(5)][1:]
    assert waits == expected
    assert LEVELS_MIN == (5.0, 4.0, 3.0, 2.0, 1.5, 1.0, 0.75, 0.5)


def test_when_nothing_breaks_the_report_says_the_highest_level_tested_not_a_ceiling() -> None:
    ctl = RampController()
    events = []
    now = 0.0
    while not ctl.done:
        events += ctl.observe(at=now, clean=True)
        now += 60
    assert ctl.outcome == "ceiling_not_reached"
    assert events[-1].kind == "ceiling_not_reached"
    assert events[-1].detail == {"highest_level_tested_min": 0.5}
    assert ctl.broke_at is None and ctl.last_clean_level == 0.5


def test_stepping_stops_at_the_first_degradation_and_names_the_level_that_broke() -> None:
    ctl = RampController()
    # 5 clean at 5 min, 5 at 4 min, 5 at 3 min, then 2 clean at 2 min and a break on the third.
    results = [True] * 15 + [True, True, False]
    drive(ctl, results)
    assert ctl.phase == "backoff" and ctl.broke_at_level == 2.0
    assert ctl.level_index == 3  # it did not step on to the next level
    assert ctl.last_clean_level == 3.0  # the last level with ALL five clean, not the 2 min one
    assert ctl.backoff_interval_min == 3.0


def test_the_break_event_records_how_much_of_the_broken_level_was_clean() -> None:
    ctl = RampController()
    drive(ctl, [True] * 15 + [True, True])
    events = ctl.observe(at=9999.0, clean=False)
    kinds = [e.kind for e in events]
    assert kinds == ["break", "backoff"]
    assert events[0].detail == {
        "level_min": 2.0,
        "clean_searches_at_this_level": 2,
        "of": 5,
        "last_fully_clean_level_min": 3.0,
    }


def test_a_break_at_the_very_first_level_has_no_clean_level_to_return_to() -> None:
    ctl = RampController(fallback_interval_min=10.0)
    events = ctl.observe(at=0.0, clean=False)
    assert ctl.broke_at_level == 5.0 and ctl.last_clean_level is None
    assert ctl.backoff_interval_min == 10.0  # the fallback, said to be a fallback
    assert "fallback" in str(events[1].detail["reason"])


def test_a_break_at_the_second_level_backs_off_to_the_first() -> None:
    ctl = RampController()
    drive(ctl, [True] * 5 + [False])
    assert ctl.broke_at_level == 4.0 and ctl.backoff_interval_min == 5.0


def test_after_the_break_searches_keep_coming_at_the_backoff_interval() -> None:
    ctl = RampController()
    drive(ctl, [True] * 15 + [False])
    assert ctl.interval_min == 3.0
    for _ in range(4):
        ctl.observe(at=1.0, clean=False)
        assert ctl.interval_min == 3.0 and ctl.phase == "backoff"


def test_recovery_is_timed_from_the_break_to_the_first_clean_answer() -> None:
    ctl = RampController(confirm_clean=2)
    ctl.observe(at=1000.0, clean=False)  # the break
    ctl.observe(at=1600.0, clean=False)
    first = ctl.observe(at=2200.0, clean=True)
    assert [e.kind for e in first] == ["first_clean"] and first[0].detail["after_s"] == 1200.0
    assert not ctl.done  # one clean answer is not believed yet
    last = ctl.observe(at=2800.0, clean=True)
    assert [e.kind for e in last] == ["recovered"]
    assert last[0].detail == {"recovery_s": 1200.0, "confirmed_by_clean_in_a_row": 2}
    assert ctl.done and ctl.outcome == "recovered"


def test_a_degraded_answer_between_clean_ones_resets_the_confirmation() -> None:
    ctl = RampController(confirm_clean=3)
    ctl.observe(at=0.0, clean=False)
    ctl.observe(at=100.0, clean=True)
    ctl.observe(at=200.0, clean=True)
    ctl.observe(at=300.0, clean=False)  # a gap in the throttle, not a recovery
    assert not ctl.done and ctl.first_clean_at is None
    ctl.observe(at=400.0, clean=True)
    ctl.observe(at=500.0, clean=True)
    events = ctl.observe(at=600.0, clean=True)
    # Recovery is dated from the first clean answer of the run that held, not the earlier one.
    assert events[-1].detail["recovery_s"] == 400.0


def test_a_recovery_that_never_comes_stops_at_the_cap_and_says_so() -> None:
    ctl = RampController(recovery_cap_s=3600.0)
    ctl.observe(at=0.0, clean=False)
    ctl.observe(at=3600.0, clean=False)  # exactly the cap: not beyond it
    assert not ctl.done
    events = ctl.observe(at=3660.0, clean=False)
    assert ctl.done and ctl.outcome == "not_recovered_within_cap"
    assert events[-1].kind == "not_recovered" and events[-1].detail == {"cap_s": 3600.0}


def test_nothing_can_be_observed_after_the_end() -> None:
    ctl = RampController()
    ctl.observe(at=0.0, clean=False)
    ctl.observe(at=1.0, clean=True)
    ctl.observe(at=2.0, clean=True)
    assert ctl.done
    with pytest.raises(ValueError, match="finished"):
        ctl.observe(at=3.0, clean=True)


@pytest.mark.parametrize(
    "bad",
    [
        {"levels": ()},
        {"levels": (1.0, 2.0)},  # stepping UP is not a ramp down
        {"levels": (5.0, 5.0)},
        {"per_level": 0},
        {"confirm_clean": 0},
    ],
)
def test_a_ramp_that_does_not_step_down_is_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RampController(**bad)  # type: ignore[arg-type]
