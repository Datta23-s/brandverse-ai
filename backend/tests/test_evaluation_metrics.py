"""The four headline figures and what must be printed beside them (decision-log 2.53).

The property this file exists for, and the owner asked for by name: **nothing is ever computed from
an invalidated or non-primary attempt.** A request whose first attempt an infrastructure failure
ended, and whose re-run succeeded, must contribute the RE-RUN's numbers and only those.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.evaluation.metrics import (
    BASELINE,
    DepthResult,
    RunRecord,
    UnitCosts,
    build_report,
    efficiency,
    ending,
    pair_requests,
    scheduling_defect_lines,
    select_primary,
    sensitivity,
)
from app.evaluation.necessity_decomposition import Bands
from app.evaluation.test_set import request_index, request_inputs

T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
WEIGHTS = {"N": 0.30, "U": 0.25, "S": 0.15}
BANDS = Bands(0.20, 0.32)
UNITS = UnitCosts(
    seconds_per_search=3.41, fetch_wall_s_per_page=6.59, pages_fetched_live=100,
    searches_measured=10, source="the recording",
)  # fmt: skip

# R01, R02, R03 are B01's three campaigns (positions 1, 2, 3).
INDEX = request_index()
BRIEFS = {rid: inp.brief for rid, inp in request_inputs().items()}


def run(
    request_id: str,
    arm: str,
    *,
    minutes: int = 0,
    status: str = "completed",
    failure: dict | None = None,  # type: ignore[type-arg]
    calls: int = 10,
    cost: float = 0.01,
    searches: int | None = 8,
    pages: int | None = 100,
    depth: str | None = "D2",
    model_ms: float = 20_000.0,
    research_ms: float = 5_000.0,
    superseded: bool = False,
    supersedes: UUID | None = None,
    provider_retry: bool = False,
    necessity: dict | None = None,  # type: ignore[type-arg]
) -> RunRecord:
    meta = INDEX[request_id]
    return RunRecord(
        run_id=uuid4(),
        arm=arm,
        request_id=request_id,
        position=meta.position,
        brand_id=meta.brand_id,
        status=status,
        failure=failure,
        created_at=T0 + timedelta(minutes=minutes),
        searches=searches,
        pages_fetched=pages,
        depth=depth,
        timings={"research_other_ms": research_ms},
        necessity=necessity,
        superseded=superseded,
        supersedes_run_id=supersedes,
        provider_retry=provider_retry,
        work_calls=calls,
        work_cost_usd=cost,
        model_ms=model_ms,
    )


# ── what a run's row means ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "failure", "expected"),
    [
        ("completed", None, "result"),
        ("blocked", None, "result"),  # a gate block is a result in its own right
        ("partial", None, "result"),  # a budget stop is a capped run, Rules.md C4
        ("failed", {"kind": "x"}, "result"),  # a failure with no infrastructure kind
        ("failed", {"infrastructure_failure": "network_error"}, "invalidated"),
        ("failed", {"infrastructure_failure": "rate_limited"}, "invalidated"),
        ("queued", None, "unfinished"),
        ("awaiting_approval", None, "unfinished"),
    ],
)
def test_a_runs_ending_decides_whether_it_can_be_a_result(
    status: str,
    failure: dict | None,
    expected: str,  # type: ignore[type-arg]
) -> None:
    assert ending(status, failure) == expected


# ── THE PROPERTY THE OWNER ASKED FOR ─────────────────────────────────────────────────────────


def test_an_invalidated_attempt_and_its_rerun_contribute_only_the_rerun() -> None:
    """R01 proposed: attempt 1 died on a network fault having spent 4 calls and 2 searches;
    the re-run spent 6 calls and 3 searches. Every figure must see 6 and 3, never 10 and 5."""
    invalid = run(
        "R01", "proposed", minutes=0, status="failed",
        failure={"infrastructure_failure": "network_error"}, calls=4, cost=0.004,
        searches=2, pages=20, model_ms=4_000.0, research_ms=1_000.0,
    )  # fmt: skip
    rerun = run(
        "R01", "proposed", minutes=30, calls=6, cost=0.006, searches=3, pages=30,
        model_ms=6_000.0, research_ms=2_000.0, supersedes=invalid.run_id,
    )  # fmt: skip
    base = run("R01", "baseline_b", minutes=10, calls=12, cost=0.02, searches=8, pages=100)
    base_a = run("R01", "baseline_a", minutes=20, calls=2, cost=0.002, searches=0, pages=0)
    selection = select_primary([invalid, rerun, base, base_a])

    assert selection.primary[("R01", "proposed")].run_id == rerun.run_id
    excluded = [e for e in selection.excluded if e.run_id == invalid.run_id]
    assert len(excluded) == 1 and "infrastructure failure" in excluded[0].why

    paired, incomplete = pair_requests(selection)
    assert incomplete == [] and len(paired) == 1
    figures = efficiency(paired, "proposed", unit_costs=UNITS)
    # the totals are the RE-RUN's, and the invalidated attempt's 4 calls / 2 searches are nowhere
    assert figures.proposed_totals.calls == 6
    assert figures.proposed_totals.searches == 3
    assert figures.proposed_totals.pages_fetched == 30
    assert figures.arr == pytest.approx((12 - 6) / 12 * 100)
    assert figures.srr == pytest.approx((8 - 3) / 8 * 100)
    assert figures.cr == pytest.approx((0.02 - 0.006) / 0.02 * 100)
    # and LR's derived parts use the re-run's counts too
    assert figures.lr is not None
    assert figures.lr.search_derived_s == pytest.approx((8 - 3) * 3.41)
    assert figures.lr.fetch_derived_s == pytest.approx((100 - 30) * 6.59)
    assert figures.lr.model_observed_s == pytest.approx((20_000 - 6_000) / 1000)


def test_an_unfinished_run_is_not_a_result_and_breaks_the_pairing_loudly() -> None:
    parked = run("R01", "proposed", status="awaiting_approval")
    base = run("R01", "baseline_b")
    base_a = run("R01", "baseline_a")
    selection = select_primary([parked, base, base_a])
    assert ("R01", "proposed") not in selection.primary
    paired, incomplete = pair_requests(selection)
    assert paired == [] and incomplete == ["R01: no primary attempt for proposed"]


def test_a_second_successful_attempt_does_not_replace_the_first() -> None:
    """The attempt rule: the primary is the FIRST attempt that ended without infrastructure
    failure. A later run of the same pair is excluded, not averaged in."""
    first = run("R01", "proposed", minutes=0, calls=5)
    second = run("R01", "proposed", minutes=60, calls=50)
    selection = select_primary([first, second])
    assert selection.primary[("R01", "proposed")].run_id == first.run_id
    assert [e.run_id for e in selection.excluded] == [second.run_id]


def test_the_stored_linkage_is_cross_checked_and_disagreement_is_reported() -> None:
    """migration 0048's `supersedes_run_id` must agree with the rule. It is never preferred
    silently over the rule, and never ignored: a mismatch is reported."""
    invalid = run(
        "R01", "proposed", minutes=0, status="failed",
        failure={"infrastructure_failure": "process_crash"},
    )  # fmt: skip
    rerun = run("R01", "proposed", minutes=30)  # no supersedes link recorded
    disagreements = select_primary([invalid, rerun]).linkage_disagreements
    assert len(disagreements) == 1 and "no run records it" in disagreements[0]
    # the primary itself marked superseded is the other direction
    odd = run("R01", "baseline_b", superseded=True)
    assert "is the primary attempt by the rule" in select_primary([odd]).linkage_disagreements[0]


# ── pairing, positions, sensitivity ──────────────────────────────────────────────────────────


def complete_set() -> list[RunRecord]:
    """B01's three campaigns in all three arms: the Proposed arm reduces as the campaigns go on."""
    rows: list[RunRecord] = []
    for request_id, depth, calls, searches, pages in (
        ("R01", "D2", 10, 8, 100),
        ("R02", "D1", 8, 4, 50),
        ("R03", "D0", 6, 1, 0),
    ):
        rows.append(
            run(
                request_id,
                "proposed",
                calls=calls,
                searches=searches,
                pages=pages,
                depth=depth,
                necessity={"novelty": 0.2, "u": 0.0 if depth == "D0" else 0.5, "s_sector": 0.0},
            )  # fmt: skip
        )
        rows.append(run(request_id, "baseline_b", calls=12, searches=8, pages=100, depth="D2"))
        rows.append(run(request_id, "baseline_a", calls=2, searches=0, pages=0, depth="D0"))
    return rows


def test_the_figures_are_broken_down_by_campaign_position() -> None:
    report = build_report(complete_set(), bands=BANDS, weights=WEIGHTS, unit_costs=UNITS)
    positions = report.positions["proposed"]
    assert set(positions) == {1, 2, 3}
    assert positions[1].arr == pytest.approx((12 - 10) / 12 * 100)
    assert positions[3].arr == pytest.approx((12 - 6) / 12 * 100)  # the saving grows with memory
    assert positions[3].srr == pytest.approx((8 - 1) / 8 * 100)


def test_every_figure_is_reported_with_and_without_the_provider_retry_campaigns() -> None:
    rows = complete_set()
    flagged = [r for r in rows if r.request_id == "R03" and r.arm == "proposed"][0]
    rows[rows.index(flagged)] = run(
        "R03", "proposed", calls=6, searches=1, pages=0, depth="D0", provider_retry=True
    )
    paired, _ = pair_requests(select_primary(rows))
    sens = sensitivity(paired, "proposed", unit_costs=UNITS)
    assert sens.flagged_requests == 1
    assert sens.all_requests.requests == 3 and sens.without_flagged.requests == 2
    assert sens.all_requests.arr != sens.without_flagged.arr
    text = "\n".join(sens.lines())
    assert "with every paired request" in text and "after a provider 5xx" in text


def test_a_zero_baseline_is_undefined_and_never_printed_as_a_percentage() -> None:
    rows = [
        run("R01", "proposed", searches=0),
        run("R01", "baseline_b", searches=0),
        run("R01", "baseline_a", searches=0),
    ]
    paired, _ = pair_requests(select_primary(rows))
    figures = efficiency(paired, "proposed")
    assert figures.srr is None
    assert "undefined (baseline is zero)" in "\n".join(figures.lines())


def test_a_run_with_no_recorded_count_is_named_as_unknown_not_counted_as_zero() -> None:
    rows = [
        run("R01", "proposed", searches=None, pages=None),
        run("R01", "baseline_b"),
        run("R01", "baseline_a"),
    ]
    paired, _ = pair_requests(select_primary(rows))
    figures = efficiency(paired, "proposed")
    assert figures.proposed_totals.searches_unknown == 1
    assert "INCOMPLETE INPUTS" in "\n".join(figures.lines())


# ── reduction, separation and the attribution are inseparable ────────────────────────────────


def test_depth_reduction_and_separation_and_the_attribution_print_together() -> None:
    report = build_report(complete_set(), bands=BANDS, weights=WEIGHTS, unit_costs=UNITS)
    assert report.depth.depths_by_brand == {"B01": ["D2", "D1", "D0"]}
    assert report.depth.reduced == {"B01": True}
    assert report.depth.separated == {"B01": True}
    block = "\n".join(report.depth.lines())
    for required in ("depth REDUCTION", "three-band SEPARATION", "memory versus novelty"):
        assert required in block
    # and the whole report carries them, with the sample declaration and the scheduling line
    text = "\n".join(report.lines())
    assert "depth REDUCTION" in text and "three-band SEPARATION" in text
    assert "45 campaigns per rater" in text
    assert "SCHEDULING DEFECTS (rate limits that bound): none" in text


def test_the_attribution_reads_only_the_proposed_arms_later_campaigns() -> None:
    report = build_report(complete_set(), bands=BANDS, weights=WEIGHTS)
    assert report.depth.attribution.campaigns == 2  # positions 2 and 3, Proposed only


def test_a_brand_missing_a_campaign_is_not_counted_as_reduced_or_separated() -> None:
    rows = [r for r in complete_set() if r.request_id != "R03"]
    result = DepthResult(
        {"B01": ["D2", "D1"]}, build_report(rows, bands=BANDS, weights=WEIGHTS).depth.attribution
    )
    assert result.brands_complete == 0 and result.reduced == {} and result.separated == {}


# ── the scheduling defect ────────────────────────────────────────────────────────────────────


def test_every_invalidation_is_reported_with_its_window_and_its_rerun() -> None:
    """`reruns.invalidations` was written on 5 November and called by NOTHING: an invalidated run
    would have been re-run and never reported (decision-log 2.53 item 6)."""
    from app.evaluation.metrics import invalidation_lines
    from app.evaluation.outages import OutageWindow
    from app.evaluation.reruns import invalidations as list_invalidations

    assert invalidation_lines([]) == ["INVALIDATED RUNS (re-run, in no figure): none"]
    window = OutageWindow(T0, T0 + timedelta(minutes=40), 12)
    invalid, replacement, no_window = uuid4(), uuid4(), uuid4()
    failure = {
        "kind": "infrastructure_gap",
        "invalidated": True,
        "infrastructure_failure": "network_error",
        "gaps": [{"step": 7, "agent": "A2", "error": "LLMCallFailed"}],
    }
    found = list_invalidations(
        [
            (invalid, T0 + timedelta(minutes=5), T0 + timedelta(minutes=9), failure, replacement),
            (no_window, T0 + timedelta(hours=9), T0 + timedelta(hours=10), failure, None),
        ],
        [window],
    )
    text = "\n".join(invalidation_lines(found))
    assert f"{invalid} network_error" in text and f"re-run {replacement}" in text
    # the two things that must never pass quietly
    assert "NO OUTAGE WINDOW OVERLAPPED: look at this" in text
    assert "NO RE-RUN RECORDED" in text


def test_the_report_always_carries_the_invalidation_block() -> None:
    report = build_report(complete_set(), bands=BANDS, weights=WEIGHTS)
    assert "INVALIDATED RUNS (re-run, in no figure): none" in "\n".join(report.lines())


def test_a_rate_limit_that_bound_is_reported_as_a_scheduling_defect_with_its_arm() -> None:
    from app.evaluation.metrics import SchedulingDefect

    lines = "\n".join(
        scheduling_defect_lines(
            [
                SchedulingDefect("baseline_b", uuid4(), T0, "A6"),
                SchedulingDefect("baseline_b", uuid4(), T0, "A2"),
                SchedulingDefect("proposed", uuid4(), T0, "A6"),
            ]
        )
    )
    assert "UNEVEN ACROSS ARMS" in lines and "'baseline_b': 2" in lines
    assert "pace the schedule" in lines


# ── the unit costs come from the recording, not from a person ────────────────────────────────


def test_the_fetch_unit_cost_is_read_from_the_committed_recording_lines(tmp_path: Path) -> None:
    committed = tmp_path / "committed.jsonl"
    committed.write_text(
        '{"request": "R13", "fetch_wall_s": 100.0, "pages_fetched_live": 20, "searches_new": 8}\n'
        '{"request": "R14", "fetch_wall_s": 50.0, "pages_fetched_live": 5, "searches_new": 8}\n'
        '{"request": "R01", "searches_new": 8}\n',  # recorded before the instrumentation: skipped
        encoding="utf-8",
    )
    units = UnitCosts.from_snapshot(committed, seconds_per_search=3.41)
    assert units.fetch_wall_s_per_page == pytest.approx(150.0 / 25)
    assert units.pages_fetched_live == 25


def test_a_recording_with_no_fetch_instrumentation_refuses_to_give_a_unit_cost(
    tmp_path: Path,
) -> None:
    committed = tmp_path / "committed.jsonl"
    committed.write_text('{"request": "R01", "searches_new": 8}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="fetch unit cost cannot be measured"):
        UnitCosts.from_snapshot(committed, seconds_per_search=3.41)


def test_every_report_line_is_printable_on_a_cp1252_console() -> None:
    """Found running the report on the dry run's own rows: a box-drawing rule raised
    UnicodeEncodeError on this Windows console and killed the print half way through. A figure a
    reader cannot print is not reported, so the lines are ASCII by test."""
    report = build_report(complete_set(), bands=BANDS, weights=WEIGHTS, unit_costs=UNITS)
    for line in report.lines():
        line.encode("cp1252")  # raises if a character cannot be printed here
        assert line.isascii(), line


def test_the_baseline_is_baseline_b_unless_stated() -> None:
    assert BASELINE == "baseline_b"
    report = build_report(complete_set(), bands=BANDS, weights=WEIGHTS)
    assert report.sensitivities["proposed"].all_requests.baseline_arm == "baseline_b"
    assert "baseline_a" in report.sensitivities  # the other arm is reported against it too
