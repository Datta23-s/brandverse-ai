"""The recorder's session loop: adaptive pacing, refused requests discarded and redone, a clean end.

What must hold because the recording rests on it: live searches are spaced by the gap and a refusal
stretches it; an answer already in the store costs no wait; a refused request commits nothing and is
redone after a delay that grows and is capped; a dead night ends at the dark-period ceiling, not in
a crash; a committed request is never captured twice (resume); and the session reports what the
first recording exists to measure: the degraded-search rate, its causes, and the achieved rate.
"""

import json
import random
from dataclasses import replace
from pathlib import Path

import pytest

from app.errors import SearchRateLimited
from app.memory.scoring import Research, load_scoring
from app.research.recorder import Pacer, RecorderConfig, record_session
from app.research.search import EngineFailure, SearchResponse
from app.research.snapshot import SnapshotStore
from tests.snapshot_support import (
    CFG,
    DECLARED,
    EXTRACTOR,
    TODAY,
    LivePages,
    LiveSearch,
    plan_for,
    urls_for,
)

pytestmark = pytest.mark.anyio

CONFIG = RecorderConfig(
    search_gap_s=120,
    gap_ceiling_s=900,
    gap_increase_factor=2.0,
    gap_decrease_step_s=15,
    clean_run_to_speed_up=3,
    backoff_base_s=60,
    backoff_max_s=300,
    backoff_jitter=0.0,
    dark_ceiling_s=3600,
    wall_limit_s=100_000,
    snapshot_page_retries=2,
)


class FakeClock:
    """A clock whose sleep advances it, so a session that would take hours runs instantly."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class Timed(LiveSearch):
    """Records the clock at every live call, and can answer degraded or throttled by call number."""

    def __init__(
        self, plan, urls, clock, fail_calls=None, degrade_calls=None, always_throttle=False
    ):
        super().__init__(plan, urls)
        self.clock, self.times = clock, []
        self.fail_calls, self.degrade_calls = fail_calls or set(), degrade_calls or {}
        self.always_throttle, self.n = always_throttle, 0
        self.degrade_reason = "HTTP error"  # the engine errored; not a rate refusal

    async def search(self, query, *, limit=10):
        self.n += 1
        self.times.append(self.clock())
        if self.always_throttle or self.n in self.fail_calls:
            raise SearchRateLimited("too many requests")
        r = await super().search(query, limit=limit)
        if self.n in self.degrade_calls:
            return SearchResponse(
                r.results,
                failed_engines=(EngineFailure(self.degrade_calls[self.n], self.degrade_reason),),
            )
        return r


def new_store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore.create(tmp_path / "evaluation-snapshot")


async def session(store, plans, search, clock, cfg=CONFIG, log_path=None):
    return await record_session(
        plans,
        store=store,
        search=search,
        pages=LivePages(),
        extractor=EXTRACTOR,
        source_cfg=CFG.source,
        declared=DECLARED,
        cfg=cfg,
        today=TODAY,
        log_path=log_path,
        clock=clock,
        sleep=clock.sleep,
        rng=random.Random(1),  # noqa: S311 - a seeded test jitter, not a secret
    )


def two_requests():
    a, b = plan_for("neckband earphones"), plan_for("smartwatch")
    urls = {**urls_for(a)}
    return {"R01": a, "R04": b}, a, b, urls


# ── The pacing ───────────────────────────────────────────────────────────────────────────────


async def test_the_pacer_spaces_live_searches_stretches_on_refusal_and_never_beats_the_estimate():
    clock = FakeClock()
    pacer = Pacer(CONFIG, clock, clock.sleep)
    await pacer.wait()
    assert clock.slept == []  # the first search waits for nothing
    await pacer.wait()
    assert clock.slept == [120]
    pacer.rate_refusal()
    assert pacer.gap_s == 240
    pacer.rate_refusal()
    pacer.rate_refusal()
    pacer.rate_refusal()
    assert pacer.gap_s == 900  # capped at the ceiling
    for _ in range(3):  # a run of 3 clean searches shortens it by one step
        pacer.clean()
    assert pacer.gap_s == 885
    for _ in range(300):
        pacer.clean()
    assert pacer.gap_s == 120  # never faster than the starting gap


# ── A session ────────────────────────────────────────────────────────────────────────────────


async def test_a_session_captures_every_pending_request_in_order_spaced_by_the_gap(tmp_path):
    clock, store = FakeClock(), new_store(tmp_path)
    plans, a, b, _ = two_requests()
    live = Timed(a, {**urls_for(a), **{r: u for r, u in urls_for(b).items()}}, clock)
    live.by_text.update({q.text: q.rank for q in b.queries})
    report = await session(store, plans, live, clock)
    assert report.ended == "complete" and report.requests_committed == ["R01", "R04"]
    assert report.requests_pending == [] and report.searches_clean == 15
    gaps = [t2 - t1 for t1, t2 in zip(live.times, live.times[1:], strict=False)]
    # 15 and not 16: both requests are in one sector, whose rules query is the same text, so the
    # second request reuses it from the store and spends no live search on it
    assert all(g >= 120 for g in gaps) and len(live.times) == 15
    assert report.degraded_search_rate == 0.0 and report.achieved_clean_per_hour


async def test_a_resumed_session_skips_what_is_committed_and_never_captures_it_twice(tmp_path):
    clock, store = FakeClock(), new_store(tmp_path)
    plans, a, b, _ = two_requests()
    urls = {**urls_for(a)}
    live = Timed(a, urls, clock)
    live.by_text.update({q.text: q.rank for q in b.queries})
    await session(store, {"R01": a}, live, clock)
    calls_before = live.n
    again = await session(store, plans, live, clock)
    assert again.requests_committed == ["R04"]  # only the pending one
    assert live.n - calls_before == 7  # R04's 8 queries, one of them already stored from R01


async def test_an_answer_already_in_the_store_costs_no_live_search_and_no_wait(tmp_path):
    clock, store = FakeClock(), new_store(tmp_path)
    a = plan_for()
    urls = urls_for(a)
    first = Timed(a, urls, clock)
    await session(store, {"R01": a}, first, clock)
    clock.slept.clear()
    repeat = Timed(a, urls, clock)  # R03: the same queries
    report = await session(store, {"R01": a, "R03": a}, repeat, clock)
    assert report.requests_committed == ["R03"] and repeat.n == 0
    assert report.searches_reused == 8 and clock.slept == []  # nothing live, nothing waited


# ── Refusal: discard, wait, redo ─────────────────────────────────────────────────────────────


async def test_a_refused_request_is_redone_carrying_its_clean_searches_and_the_wait_grows_then_caps(
    tmp_path,
):
    clock, store = FakeClock(), new_store(tmp_path)
    a = plan_for()
    # live calls 3, 5, 7 and 9 are throttled; the fifth attempt gets through
    live = Timed(a, urls_for(a), clock, fail_calls={3, 5, 7, 9})
    log_path = tmp_path / "s.jsonl"
    report = await session(store, {"R01": a}, live, clock, log_path=log_path)
    assert report.requests_committed == ["R01"] and report.aborted_attempts == 4
    assert report.attempts == 5 and report.searches_throttled == 4
    lines = [json.loads(x) for x in log_path.read_text().splitlines()]
    aborted = [x for x in lines if x["kind"] == "attempt" and x["outcome"] == "aborted"]
    assert [x["waited_next_s"] for x in aborted] == [60, 120, 240, 300]  # doubles, then capped
    assert report.waited_s == 60 + 120 + 240 + 300
    # each clean search was captured ONCE and reused by every later attempt: 8 clean, none lost
    assert report.searches_clean == 8 and live.n == 8 + 4
    assert report.searches_carried == 2 + 3 + 4 + 5 and report.searches_wasted == 0
    assert [x["carry_held"] for x in aborted] == [2, 3, 4, 5]


async def test_nothing_is_carried_across_sessions_and_the_loss_is_what_wasted_measures(tmp_path):
    clock, store = FakeClock(), new_store(tmp_path)
    a = plan_for()
    # session 1 captures two clean searches, then the engine goes dark until the session ends
    dark = Timed(a, urls_for(a), clock, fail_calls=set(range(3, 400)))
    first = await session(store, {"R01": a}, dark, clock)
    assert first.ended == "dark_period_ceiling" and first.requests_committed == []
    assert first.searches_wasted == 2  # the two clean searches held for a request left in flight
    assert store.committed_requests() == set()
    # session 2 starts clean: those two searches are fetched again, all eight are live
    live = Timed(a, urls_for(a), clock)
    second = await session(store, {"R01": a}, live, clock)
    assert second.requests_committed == ["R01"] and live.n == 8
    assert second.searches_carried == 0 and second.searches_wasted == 0


async def test_each_request_records_its_capture_span_and_the_session_reports_max_and_median(
    tmp_path,
):
    clock, store = FakeClock(), new_store(tmp_path)
    plans, a, b, _ = two_requests()
    live = Timed(a, urls_for(a), clock)
    live.by_text.update({q.text: q.rank for q in b.queries})
    report = await session(store, plans, live, clock)
    assert len(report.spans_s) == 2
    summary = report.summary()
    assert (
        summary["search_span_s"]["max"] is not None
        and summary["search_span_s"]["median"] is not None
    )
    assert summary["search_span_all_s"]["max"] >= summary["search_span_s"]["max"]
    for line in store.committed.values():
        assert (
            line["search_span_s"] is not None
            and line["first_search_utc"] <= line["last_search_utc"]
        )
    spans = store.manifest_summary()
    assert (
        spans["search_span_s"]["max"] is not None
        and spans["search_span_all_s"]["median"] is not None
    )


async def test_the_retry_delay_is_jittered_within_its_bound(tmp_path):
    cfg = replace(CONFIG, backoff_jitter=0.25, dark_ceiling_s=5000)
    clock, store = FakeClock(), new_store(tmp_path)
    a = plan_for()
    live = Timed(a, urls_for(a), clock, always_throttle=True)
    log_path = tmp_path / "s.jsonl"
    await session(store, {"R01": a}, live, clock, cfg=cfg, log_path=log_path)
    lines = [json.loads(x) for x in log_path.read_text().splitlines()]
    waits = [x["waited_next_s"] for x in lines if x["kind"] == "attempt"]
    bases = [min(300, 60 * 2**i) for i in range(len(waits))]
    assert waits and all(b <= w <= b * 1.25 for w, b in zip(waits, bases, strict=True))
    assert any(w > b for w, b in zip(waits, bases, strict=True))  # the jitter is really applied


# ── The clean ends ───────────────────────────────────────────────────────────────────────────


async def test_a_dead_night_ends_at_the_dark_period_ceiling_with_nothing_committed(tmp_path):
    clock, store = FakeClock(), new_store(tmp_path)
    a = plan_for()
    live = Timed(a, urls_for(a), clock, always_throttle=True)
    report = await session(store, {"R01": a}, live, clock)
    assert report.ended == "dark_period_ceiling" and report.requests_committed == []
    assert report.requests_pending == ["R01"] and store.committed_requests() == set()
    assert clock.now - 1000 >= CONFIG.dark_ceiling_s  # it waited out the ceiling, no longer
    assert clock.now - 1000 < CONFIG.dark_ceiling_s + CONFIG.backoff_max_s + 900


async def test_the_wall_limit_ends_a_session_cleanly(tmp_path):
    clock, store = FakeClock(), new_store(tmp_path)
    a = plan_for()
    cfg = RecorderConfig(
        **{**{k: getattr(CONFIG, k) for k in CONFIG.__slots__}, "wall_limit_s": 10}
    )
    live = Timed(a, urls_for(a), clock)
    report = await session(store, {"R01": a, "R02": a}, live, clock, cfg=cfg)
    assert report.ended == "wall_limit" and report.requests_pending == ["R02"]
    assert report.requests_committed == ["R01"]  # the in-flight request finished; none was started


# ── What the first session exists to measure ─────────────────────────────────────────────────


async def test_the_degraded_search_rate_and_its_causes_are_reported(tmp_path):
    clock, store = FakeClock(), new_store(tmp_path)
    a = plan_for()
    live = Timed(a, urls_for(a), clock, degrade_calls={2: "yep", 4: "yahoo", 6: "yep"})
    report = await session(store, {"R01": a}, live, clock)
    assert report.requests_committed == ["R01"] and report.aborted_attempts == 3
    assert report.searches_degraded == 3 and report.degraded_causes == {"yahoo": 1, "yep": 2}
    assert report.degraded_search_rate == 3 / report.searches_live
    assert report.summary()["degraded_causes"] == {"yahoo": 1, "yep": 2}


async def test_the_session_log_has_a_line_per_attempt_with_the_achieved_rate(tmp_path):
    clock, store = FakeClock(), new_store(tmp_path)
    a = plan_for()
    live = Timed(a, urls_for(a), clock, fail_calls={4})
    log_path = tmp_path / "session.jsonl"
    await session(store, {"R01": a}, live, clock, log_path=log_path)
    lines = [json.loads(x) for x in log_path.read_text().splitlines()]
    kinds = [x["kind"] for x in lines]
    assert kinds == ["session_start", "attempt", "attempt", "session_end"]
    first, second = lines[1], lines[2]
    assert first["outcome"] == "aborted" and "throttled" in first["abort_reason"]
    assert first["waited_next_s"] == 60 and first["gap_s"] == 240  # the gap stretched
    assert second["outcome"] == "committed" and second["achieved_clean_per_hour"]
    assert lines[-1]["ended"] == "complete" and lines[-1]["config"]["search_gap_s"] == 120


# ── Configuration ────────────────────────────────────────────────────────────────────────────


def test_the_shipped_recorder_configuration_loads_and_starts_at_the_measured_gap():
    cfg = RecorderConfig.from_research(load_scoring().research)
    assert cfg.search_gap_s == 120 and cfg.snapshot_page_retries == 2
    assert cfg.gap_ceiling_s >= cfg.search_gap_s and cfg.backoff_max_s >= cfg.backoff_base_s


def test_recorder_bounds_must_be_ordered():
    raw = load_scoring().research.model_dump()
    with pytest.raises(ValueError, match="gap_ceiling"):
        Research.model_validate({**raw, "recorder_gap_ceiling_s": 10})
    with pytest.raises(ValueError, match="backoff_max"):
        Research.model_validate({**raw, "recorder_backoff_max_s": 1})


# ── The gap moves for a rate refusal and nothing else (owner, 2026-10-28) ────────────────────


async def test_a_fault_leaves_the_gap_where_it_is_and_a_rate_refusal_stretches_it():
    clock = FakeClock()
    pacer = Pacer(CONFIG, clock, clock.sleep)
    for _ in range(10):  # ten service faults in a row
        pacer.fault()
    assert (pacer.gap_s, pacer.faults, pacer.rate_refusals) == (120, 10, 0)
    pacer.rate_refusal()  # and one engine saying "too many requests"
    assert (pacer.gap_s, pacer.rate_refusals) == (240, 1)


async def test_a_fault_neither_resets_the_clean_run_nor_counts_as_a_clean_search():
    clock = FakeClock()
    pacer = Pacer(CONFIG, clock, clock.sleep)
    pacer.rate_refusal()
    assert pacer.gap_s == 240
    for _ in range(4):
        pacer.clean()
    pacer.fault()  # does not break the run of clean searches ...
    pacer.clean()
    assert pacer.gap_s == 225  # ... so the fifth clean search still speeds the gap up
    assert pacer.last_clean_at is not None


async def test_a_degraded_search_does_not_stretch_the_gap_but_a_rate_refused_one_does(tmp_path):
    """The live path, not the Pacer alone: a degraded answer from an engine that errored leaves the
    session's pace alone, and the same answer from an engine that refused the rate doubles it."""
    for reason, expected_gap, refused in (
        ("HTTP error", 120, 0),
        ("Suspended: too many requests", 240, 1),
    ):
        clock = FakeClock()
        store = SnapshotStore.create(tmp_path / reason[:4] / "evaluation-snapshot")
        plan = plan_for()
        search = Timed(plan, urls_for(plan), clock, degrade_calls={2: "yep"})
        search.degrade_reason = reason
        log_path = store.root / "session.jsonl"
        report = await session(store, {"R01": plan}, search, clock, log_path=log_path)
        aborted = next(
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("outcome") == "aborted"
        )
        assert report.searches_degraded == 1
        assert report.searches_rate_refused == refused
        assert report.searches_degraded_not_throttled == 1 - refused
        assert aborted["gap_s"] == expected_gap  # the gap in force for the redo
        assert report.requests_committed == ["R01"]  # aborted and redone either way


async def test_the_session_report_separates_rate_refusals_from_faults(tmp_path):
    clock = FakeClock()
    store = new_store(tmp_path)
    plan = plan_for()
    search = Timed(plan, urls_for(plan), clock, fail_calls={2}, degrade_calls={5: "yahoo"})
    report = await session(store, {"R01": plan}, search, clock)
    summary = report.summary()
    assert summary["searches_rate_refused"] == 1  # the SearchRateLimited at search 2
    assert summary["searches_degraded_not_throttled"] == 1  # the degraded answer at search 5
    assert summary["searches_degraded"] == 1 and summary["searches_throttled"] == 1


# ── The wait between attempts moves for a rate refusal and nothing else (owner, 2026-10-29) ──


def attempt_lines(log_path: Path) -> list[dict]:
    lines = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()]
    return [x for x in lines if x["kind"] == "attempt" and x["outcome"] == "aborted"]


async def test_aborts_from_one_engines_faults_are_redone_after_the_unchanged_base_wait(tmp_path):
    clock, store = FakeClock(), new_store(tmp_path)
    a = plan_for()
    live = Timed(a, urls_for(a), clock, degrade_calls={n: "yahoo" for n in range(1, 7)})
    log_path = tmp_path / "s.jsonl"
    report = await session(store, {"R01": a}, live, clock, log_path=log_path)
    aborted = attempt_lines(log_path)
    assert report.requests_committed == ["R01"] and len(aborted) == 6
    assert [x["waited_next_s"] for x in aborted] == [60] * 6  # did not double
    assert {x["abort_kind"] for x in aborted} == {"fault"}
    assert (report.aborts_fault, report.aborts_rate_refused) == (6, 0)


async def test_a_rate_refusal_lengthens_the_wait_and_a_later_fault_keeps_it_where_it_is(tmp_path):
    clock, store = FakeClock(), new_store(tmp_path)
    a = plan_for()
    # calls 1 and 2 are throttled, call 3 is one engine erroring; the next attempt gets through
    live = Timed(a, urls_for(a), clock, fail_calls={1, 2}, degrade_calls={3: "yahoo"})
    log_path = tmp_path / "s.jsonl"
    report = await session(store, {"R01": a}, live, clock, log_path=log_path)
    aborted = attempt_lines(log_path)
    assert [x["waited_next_s"] for x in aborted] == [60, 120, 120]
    assert [x["abort_kind"] for x in aborted] == ["rate_refusal", "rate_refusal", "fault"]
    assert (report.aborts_rate_refused, report.aborts_fault) == (2, 1)


async def test_a_degraded_answer_that_names_a_rate_refusal_lengthens_the_wait(tmp_path):
    clock, store = FakeClock(), new_store(tmp_path)
    a = plan_for()
    live = Timed(a, urls_for(a), clock, degrade_calls={1: "yep", 2: "yep"})
    live.degrade_reason = "Suspended: too many requests"
    log_path = tmp_path / "s.jsonl"
    report = await session(store, {"R01": a}, live, clock, log_path=log_path)
    assert [x["waited_next_s"] for x in attempt_lines(log_path)] == [60, 120]
    assert report.aborts_rate_refused == 2 and report.aborts_fault == 0


async def test_a_sustained_outage_of_one_engine_keeps_attempting_until_the_ceiling(tmp_path):
    """The first full session's end, replayed: one declared engine down, every attempt dying on
    query 1. With the wait doubling for each abort the session made seven attempts in 104 minutes
    and ended on the dark-period ceiling; at the unchanged wait it keeps trying, each attempt
    spaced by the gap, so it would carry on the moment the engine returned."""
    clock, store = FakeClock(), new_store(tmp_path)
    a = plan_for()
    live = Timed(a, urls_for(a), clock, degrade_calls={n: "yahoo" for n in range(1, 500)})
    report = await session(store, {"R01": a}, live, clock)
    assert report.ended == "dark_period_ceiling" and report.requests_committed == []
    assert report.attempts >= 25  # doubling to the cap gives about 14 in the same time
    assert report.aborts_rate_refused == 0 and report.aborts_fault == report.attempts


async def test_the_failed_page_share_divides_by_the_pages_fetched_not_the_pages_in_the_requests():
    from app.research.recorder import SessionReport

    # the first full session's figures: 3790 pages across the requests, 1622 of them already stored
    report = SessionReport(pages_total=3790, pages_new_ok=1680, pages_frozen_failed=488)
    assert report.pages_fetched == 2168
    assert report.frozen_failed_share_of_fetched == pytest.approx(0.2251, abs=1e-4)
    summary = report.summary()
    assert "frozen_failed_share" not in summary  # the mis-denominated key is gone, not kept
    assert summary["frozen_failed_share_of_fetched"] == report.frozen_failed_share_of_fetched


async def test_a_real_session_counts_only_live_fetches_in_the_failure_share(tmp_path):
    clock, store = FakeClock(), new_store(tmp_path)
    plans, a, b, _ = two_requests()
    live = Timed(a, urls_for(a), clock)
    live.by_text.update({q.text: q.rank for q in b.queries})
    report = await session(store, plans, live, clock)
    assert report.pages_reused > 0  # R04 found pages already stored
    assert report.pages_fetched == report.pages_total - report.pages_reused
