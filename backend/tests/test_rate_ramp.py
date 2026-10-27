"""The search-rate ramp's stepping logic (app/research/rate_ramp.py): pure, no network.

What must hold, because the §8 recording-phase figure rests on it: the gap steps DOWN through the
levels, five searches at each; stepping stops at the first degradation; the sustainable level is the
last level that was *fully* clean, never the one that broke; recovery is timed from the break to the
first clean answer and only believed once confirmed.
"""

import pytest

from app.research.rate_ramp import (
    LEVELS_MIN,
    HoldController,
    RampController,
    SilentRecoveryController,
    classify_pattern,
    trailing_count,
)


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


# ── The sustained hold ───────────────────────────────────────────────────────────────────────


def run_hold(ctl: HoldController, results: list[bool], *, gap_s: float = 600.0) -> list[object]:
    events: list[object] = []
    for n, clean in enumerate(results):
        events += ctl.observe(at=n * gap_s, clean=clean)
        if ctl.done:
            break
    return events


def test_a_hold_that_stays_clean_for_four_hours_and_24_searches_is_held() -> None:
    ctl = HoldController()
    events = run_hold(ctl, [True] * 25)  # 25 searches 10 minutes apart: 24 gaps = 4 hours exactly
    assert ctl.done and ctl.outcome == "held" and len(ctl.times) == 25
    assert events[-1].kind == "hold_held"  # type: ignore[attr-defined]
    assert events[-1].detail["searches"] == 25 and events[-1].detail["hours"] == 4.0  # type: ignore[attr-defined]


def test_a_hold_needs_BOTH_the_searches_and_the_hours() -> None:
    quick = HoldController()  # 30 searches a minute apart: enough searches, nowhere near 4 hours
    run_hold(quick, [True] * 30, gap_s=60.0)
    assert not quick.done and quick.outcome is None
    slow = HoldController()  # 10 searches an hour apart: more than 4 hours, too few searches
    run_hold(slow, [True] * 10, gap_s=3600.0)
    assert not slow.done


def test_the_hold_stops_at_the_first_degradation_and_says_how_far_it_got() -> None:
    ctl = HoldController()
    events = run_hold(ctl, [True] * 7 + [False] + [True] * 30)
    assert ctl.done and ctl.outcome == "broke" and len(ctl.times) == 8
    detail = events[-1].detail  # type: ignore[attr-defined]
    assert events[-1].kind == "hold_broke"  # type: ignore[attr-defined]
    assert detail["search_number"] == 8 and detail["clean_before"] == 7
    assert detail["after_s"] == 7 * 600.0


def test_the_hold_records_the_most_requests_it_ever_put_in_one_trailing_hour() -> None:
    ctl = HoldController(min_searches=7, min_hours=1.0)
    events = run_hold(ctl, [True] * 7)  # 0, 10, ... 60 minutes: the window is (t-60, t]
    assert ctl.outcome == "held"
    assert events[-1].detail["max_requests_in_any_trailing_hour"] == 6  # type: ignore[attr-defined]


def test_trailing_count_counts_refused_requests_too_and_the_window_is_half_open() -> None:
    earlier = [0.0, 600.0, 1200.0, 1800.0, 2400.0]  # `at` is the request being counted, not in it
    assert trailing_count(earlier, 3000.0) == 6  # five earlier plus this one
    assert trailing_count([*earlier, 3000.0], 3600.0) == 6  # the request at t=0 has just left
    assert trailing_count([*earlier, 3000.0], 3599.0) == 7  # at 3599 the one at 0 is still inside
    assert trailing_count([], 5.0) == 1


@pytest.mark.parametrize("bad", [{"interval_min": 0}, {"min_searches": 0}, {"min_hours": 0}])
def test_a_hold_with_no_gap_no_searches_or_no_time_is_refused(bad: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        HoldController(**bad)  # type: ignore[arg-type]


def test_nothing_can_be_observed_after_the_hold_ends() -> None:
    ctl = HoldController()
    ctl.observe(at=0.0, clean=False)
    with pytest.raises(ValueError, match="finished"):
        ctl.observe(at=1.0, clean=True)


# ── The silent-wait recovery ─────────────────────────────────────────────────────────────────


def test_the_burst_is_provoked_at_its_gap_until_the_engine_degrades() -> None:
    ctl = SilentRecoveryController(provoke_gap_min=2.0)
    assert ctl.next_search_at(1000.0) == 1000.0  # the first search is now
    ctl.observe(at=1000.0, clean=True)
    assert ctl.next_search_at(1000.0) == 1120.0  # then every 2 minutes
    ctl.observe(at=1120.0, clean=True)
    events = ctl.observe(at=1240.0, clean=False)
    assert [e.kind for e in events] == ["break", "silence_begins"]
    assert events[0].detail == {"searches_in_the_burst": 3, "gap_min": 2.0}
    assert ctl.phase == "silent" and ctl.broke_at == 1240.0


def test_in_silence_nothing_is_asked_between_the_probes() -> None:
    ctl = SilentRecoveryController(delays_min=(5.0, 10.0, 30.0))
    ctl.observe(at=0.0, clean=False)  # the break at t = 0
    assert ctl.next_search_at(0.0) == 300.0  # the first probe, 5 minutes after the break
    ctl.observe(at=300.0, clean=False)
    assert (
        ctl.next_search_at(300.0) == 600.0
    )  # then 10 minutes AFTER THE BREAK, not after the probe
    ctl.observe(at=600.0, clean=False)
    assert ctl.next_search_at(600.0) == 1800.0  # a 20-minute silence to the 30-minute probe


def test_recovery_is_an_interval_between_the_last_refused_probe_and_the_first_clean_one() -> None:
    ctl = SilentRecoveryController(delays_min=(5.0, 10.0, 15.0, 20.0))
    ctl.observe(at=0.0, clean=False)
    ctl.observe(at=300.0, clean=False)
    ctl.observe(at=600.0, clean=False)
    events = ctl.observe(at=900.0, clean=True)
    assert events[0].kind == "first_clean"
    assert events[0].detail["recovered_between_min"] == [10.0, 15.0]  # not "at 15"
    assert ctl.phase == "confirm" and ctl.first_clean_delay_min == 15.0


def test_one_confirming_probe_follows_the_first_clean_one() -> None:
    ctl = SilentRecoveryController(delays_min=(5.0,), confirm_after_min=10.0)
    ctl.observe(at=0.0, clean=False)
    ctl.observe(at=300.0, clean=True)
    assert ctl.next_search_at(300.0) == 900.0  # 10 minutes after the first clean one
    events = ctl.observe(at=900.0, clean=True)
    assert events[0].kind == "confirmed" and ctl.confirmed is True and ctl.outcome == "recovered"


def test_a_recovery_that_flaps_is_recorded_as_flapped_not_confirmed() -> None:
    ctl = SilentRecoveryController(delays_min=(5.0,))
    ctl.observe(at=0.0, clean=False)
    ctl.observe(at=300.0, clean=True)
    events = ctl.observe(at=900.0, clean=False)
    assert events[0].kind == "flapped" and ctl.confirmed is False and ctl.done


def test_a_schedule_that_runs_out_without_recovery_says_so() -> None:
    ctl = SilentRecoveryController(delays_min=(5.0, 10.0))
    ctl.observe(at=0.0, clean=False)
    ctl.observe(at=300.0, clean=False)
    events = ctl.observe(at=600.0, clean=False)
    assert ctl.done and ctl.outcome == "not_recovered_within_schedule"
    assert events[-1].kind == "not_recovered" and events[-1].detail == {
        "last_probe_after_min": 10.0
    }


def test_a_burst_that_never_provokes_a_throttle_is_a_finding_not_a_hang() -> None:
    ctl = SilentRecoveryController(provoke_cap=3)
    for n in range(3):
        events = ctl.observe(at=n * 120.0, clean=True)
    assert ctl.done and ctl.outcome == "no_break_provoked"
    assert events[-1].kind == "no_break_provoked" and events[-1].detail == {"searches": 3}


@pytest.mark.parametrize(
    "bad",
    [
        {"delays_min": ()},
        {"delays_min": (10.0, 5.0)},
        {"delays_min": (5.0, 5.0)},
        {"delays_min": (0.0, 5.0)},
        {"provoke_gap_min": 0},
        {"provoke_cap": 0},
        {"confirm_after_min": 0},
    ],
)
def test_a_silent_schedule_that_is_not_increasing_or_positive_is_refused(
    bad: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        SilentRecoveryController(**bad)  # type: ignore[arg-type]


# ── Which description a run of asks fits ─────────────────────────────────────────────────────

# Every pattern the 52 run-2-only bucket fits predicted for the ten asks 06:44..07:29
# (docs/evaluation-plan.md section 8): each must be classified as a trickle, or as undecided when
# it holds no answer at all. If one were not, the registered table would not match the code.
PREDICTED_BY_THE_FITS = [
    "TXXXTXXXTX",
    "TXXXTXXXXT",
    "TXXXXTXXXT",
    "TXXXXTXXXX",
    "TXXXXXTXXX",
    "XTXXXXTXXX",
    "XTXXXXXTXX",
    "XTXXXXXXTX",
    "XXTXXXXXTX",
    "XXTXXXXXXT",
    "XXTXXXXXXX",
    "XXXTXXXXXX",
    "XXXXTXXXXX",
    "XXXXXTXXXX",
    "XXXXXXTXXX",
    "XXXXXXXTXX",
    "XXXXXXXXTX",
]


@pytest.mark.parametrize("sequence", PREDICTED_BY_THE_FITS)
def test_every_pattern_the_bucket_fits_predicted_is_classified_a_trickle(sequence: str) -> None:
    assert classify_pattern(sequence) == "trickle"


def test_no_answer_at_all_is_undecided_because_both_descriptions_allow_it() -> None:
    assert classify_pattern("XXXXXXXXXX") == "undecided"


@pytest.mark.parametrize("sequence", ["XXXXXTTXXX", "TTXXXXXXXX", "XXXXXXXXTT", "XXXTTTTXXX"])
def test_two_answers_in_a_row_after_a_dead_stretch_is_the_windows_prediction(sequence: str) -> None:
    assert classify_pattern(sequence) == "window"


@pytest.mark.parametrize(
    "sequence",
    [
        "TXTXXXXXXX",  # isolated, but only ten minutes apart: a trickle is at least twenty
        "TXXTXXXXXX",  # fifteen minutes apart
        "XTXTXTXTXT",  # five isolated answers, ten apart
    ],
)
def test_isolated_answers_closer_than_twenty_minutes_are_neither(sequence: str) -> None:
    assert classify_pattern(sequence) == "neither"


def test_twenty_minutes_apart_is_the_boundary_and_is_allowed() -> None:
    assert classify_pattern("TXXXTXXXXX") == "trickle"  # exactly four asks = 20 minutes
    assert classify_pattern("TXXTXXXXXX") == "neither"  # three asks = 15 minutes


def test_more_than_three_answers_is_neither_even_if_twenty_minutes_apart() -> None:
    # Four answers four asks apart need sixteen asks, so use a longer run: spacing alone is not it.
    assert classify_pattern("TXXXTXXXTXXXTXXX") == "neither"


def test_the_classifier_refuses_anything_but_t_and_x() -> None:
    with pytest.raises(ValueError, match="T and X"):
        classify_pattern("TXO")


# ── Late asks: a hold that slept cannot be certified (2026-10-27) ────────────────────────────


def test_a_gap_over_one_and_a_half_intervals_is_a_late_ask_and_is_recorded() -> None:
    ctl = HoldController()
    ctl.observe(at=0.0, clean=True)
    on_time = ctl.observe(at=600.0, clean=True)
    late = ctl.observe(at=600.0 + 1801.0, clean=True)  # 30 minutes after a 10-minute plan
    assert on_time == [] and [e.kind for e in late] == ["late_ask"]
    assert late[0].detail == {"gap_s": 1801.0, "planned_s": 600.0, "search_number": 3}
    assert ctl.late_gaps_s == [1801.0]


def test_a_gap_at_exactly_one_and_a_half_intervals_is_not_late() -> None:
    ctl = HoldController()
    ctl.observe(at=0.0, clean=True)
    assert ctl.observe(at=900.0, clean=True) == [] and ctl.late_gaps_s == []


def test_a_hold_that_finishes_with_a_late_gap_is_held_with_gaps_not_held() -> None:
    ctl = HoldController(min_searches=3, min_hours=0.1)
    ctl.observe(at=0.0, clean=True)
    ctl.observe(at=600.0, clean=True)  # on time
    events = ctl.observe(at=600.0 + 3600.0, clean=True)  # the machine slept for an hour
    assert ctl.outcome == "held_with_gaps"
    assert [e.kind for e in events] == ["late_ask", "hold_held_with_gaps"]
    assert events[-1].detail["late_gaps_s"] == [3600.0]


def test_a_clean_hold_with_no_late_gap_is_still_plain_held() -> None:
    ctl = HoldController(min_searches=3, min_hours=0.1)
    for n in range(3):
        events = ctl.observe(at=n * 600.0, clean=True)
    assert ctl.outcome == "held" and events[-1].kind == "hold_held"
    assert events[-1].detail["late_gaps_s"] == []


def test_a_break_after_a_late_gap_is_still_a_break_and_says_how_many_gaps_preceded_it() -> None:
    ctl = HoldController()
    ctl.observe(at=0.0, clean=True)
    events = ctl.observe(at=4000.0, clean=False)
    assert ctl.outcome == "broke"
    assert [e.kind for e in events] == ["late_ask", "hold_broke"]
    assert events[-1].detail["late_gaps"] == 1  # more time to refill, and it still broke


# ── Silence from a break that already happened ───────────────────────────────────────────────


def test_a_silence_begun_after_a_break_probes_at_the_delays_measured_from_that_break() -> None:
    ctl = SilentRecoveryController.after_a_break(
        broke_at=1000.0, now=1000.0 + 150.0, delays_min=(5.0, 10.0, 15.0)
    )
    assert ctl.phase == "silent" and ctl.skipped_delays_min == [] and ctl.probe_index == 0
    assert ctl.next_search_at(1150.0) == 1300.0  # 5 minutes after the BREAK, not after "now"


def test_delays_already_past_are_skipped_not_probed_late() -> None:
    ctl = SilentRecoveryController.after_a_break(
        broke_at=1000.0, now=1000.0 + 11 * 60, delays_min=(5.0, 10.0, 15.0, 20.0)
    )
    assert ctl.skipped_delays_min == [5.0, 10.0] and ctl.probe_index == 2
    assert ctl.next_search_at(0.0) == 1000.0 + 15 * 60  # the next delay on the schedule
    events = ctl.observe(at=1000.0 + 15 * 60, clean=True)
    # The lower bound is the break (0), because the skipped probes were never made.
    assert events[0].detail["recovered_between_min"] == [0.0, 15.0]


def test_a_delay_within_the_margin_of_now_is_treated_as_past() -> None:
    ctl = SilentRecoveryController.after_a_break(
        broke_at=0.0, now=300.0 - 3.0, delays_min=(5.0, 10.0), margin_s=5.0
    )
    assert ctl.skipped_delays_min == [5.0]  # 3 s away is inside the margin: not probed on time


def test_a_silence_begun_after_every_delay_has_passed_is_done_not_probing() -> None:
    ctl = SilentRecoveryController.after_a_break(broke_at=0.0, now=10_000.0, delays_min=(5.0, 10.0))
    assert ctl.done and ctl.outcome == "not_recovered_within_schedule"


def test_skipped_delays_are_per_controller_not_shared() -> None:
    a = SilentRecoveryController.after_a_break(
        broke_at=0.0, now=700.0, delays_min=(5.0, 10.0, 30.0)
    )
    b = SilentRecoveryController()
    assert a.skipped_delays_min == [5.0, 10.0] and b.skipped_delays_min == []
