"""The registered interleaved run order and the outage windows (owner ruling, 5 November 2026).

The order file is committed BEFORE any evaluation call; this test recomputes it from the code, the
seed and the test set, so the file cannot drift from the rule and a later edit is visible as a
failure, not a quiet change. The outage helpers are pure functions over timestamps.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import yaml

from app.evaluation.outages import OutageWindow, active_spans, outage_windows, runs_touching
from app.evaluation.schedule import ARMS, RUN_ORDER, SEED, interleaved_order, registered_order

REGISTERED = yaml.safe_load(RUN_ORDER.read_text(encoding="utf-8"))
TEST_SET = Path(__file__).resolve().parents[2] / "evaluation" / "test_set.yaml"


def test_the_committed_order_is_exactly_what_the_code_computes() -> None:
    assert REGISTERED["seed"] == SEED == 20261105
    assert sorted(REGISTERED["arms"]) == sorted(ARMS)
    computed = [f"{r}:{a}" for r, a in registered_order()]
    assert REGISTERED["order"] == computed
    assert REGISTERED["runs"] == len(computed) == 90
    digest = hashlib.sha256("\n".join(computed).encode()).hexdigest()
    assert REGISTERED["sha256_of_order_lines"] == digest


def test_every_request_runs_all_three_arms_adjacently() -> None:
    order = registered_order()
    assert len(order) == 90 and len(set(order)) == 90
    for i in range(0, 90, 3):
        block = order[i : i + 3]
        assert len({request for request, _ in block}) == 1  # one request, three adjacent runs
        assert sorted(arm for _, arm in block) == sorted(ARMS)


def test_a_brands_campaigns_keep_their_registered_positions() -> None:
    """Campaign 2 and 3 read the memory the earlier approved campaigns wrote: never reordered."""
    requests = yaml.safe_load(TEST_SET.read_text(encoding="utf-8"))["requests"]
    brand_of = {r["id"]: (r["brand"], r["position"]) for r in requests}
    seen: dict[str, list[int]] = {}
    for request, _ in registered_order():
        brand, position = brand_of[request]
        if not seen.get(brand) or seen[brand][-1] != position:
            seen.setdefault(brand, []).append(position)
    assert all(positions == [1, 2, 3] for positions in seen.values())
    # and a brand's three campaigns are contiguous: the brand blocks are what is shuffled
    blocks = [brand_of[r][0] for r, _ in registered_order()]
    collapsed = [b for i, b in enumerate(blocks) if i == 0 or blocks[i - 1] != b]
    assert sorted(collapsed) == sorted(set(collapsed)) and len(collapsed) == 10
    assert collapsed == REGISTERED["brand_order"]


def test_no_arm_is_systematically_first() -> None:
    first = {arm: 0 for arm in ARMS}
    for i in range(0, 90, 3):
        first[registered_order()[i][1]] += 1
    assert all(count >= 5 for count in first.values()), first  # of 30 requests, none starved


def test_the_seed_changes_the_order_so_the_seed_is_what_is_registered() -> None:
    requests = yaml.safe_load(TEST_SET.read_text(encoding="utf-8"))["requests"]
    assert interleaved_order(requests, seed=SEED) != interleaved_order(requests, seed=SEED + 1)
    assert interleaved_order(requests, seed=SEED) == registered_order()  # and it is reproducible


# ── Outage windows ────────────────────────────────────────────────────────────────────────────

T0 = datetime(2026, 11, 5, 12, 54, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_provider_unavailable_calls_cluster_into_windows() -> None:
    times = [at(0), at(1), at(5), at(30), at(31), at(120)]
    windows = outage_windows(times, gap=timedelta(minutes=15))
    assert [(w.start, w.end, w.calls) for w in windows] == [
        (at(0), at(5), 3),
        (at(30), at(31), 2),
        (at(120), at(120), 1),
    ]


def test_no_outage_means_no_windows() -> None:
    assert outage_windows([]) == []


def test_a_run_that_overlapped_a_window_is_identified_and_the_others_are_not() -> None:
    window = OutageWindow(at(0), at(40), 12)
    inside, straddling, before, after = (uuid4() for _ in range(4))
    spans = [
        (inside, at(5), at(15)),
        (straddling, at(-10), at(2)),
        (before, at(-30), at(-1)),
        (after, at(41), at(60)),
    ]
    assert set(runs_touching([window], spans)) == {inside, straddling}


def test_the_time_a_run_waits_for_its_approver_is_not_time_it_could_be_hit_by_an_outage() -> None:
    """R3 (owner, 2026-11-07): a run parked for hours across an outage window was reported as
    having touched it, which flags nearly every parked run. Its active time excludes the wait."""
    window = OutageWindow(at(100), at(110), 4)
    # runs 0..10, parks 10..300 (the outage is inside the wait), resumes and finishes 300..305
    spans = active_spans(at(0), at(305), [(at(10), at(300))])
    assert spans == [(at(0), at(10)), (at(300), at(305))]
    run = uuid4()
    assert runs_touching([window], [(run, a, b) for a, b in spans]) == {}
    # the same run with no wait recorded WOULD be flagged: the wait is what separates them
    assert set(runs_touching([window], [(run, at(0), at(305))])) == {run}
    # an outage during the active part still counts, and a run in several spans is listed once
    hit = OutageWindow(at(3), at(4), 1)
    assert runs_touching([hit, window], [(run, a, b) for a, b in spans]) == {run: [hit]}


def test_active_spans_handle_an_open_wait_several_waits_and_none() -> None:
    assert active_spans(at(0), at(50), []) == [(at(0), at(50))]
    assert active_spans(at(0), at(50), [(at(20), None)]) == [(at(0), at(20))]  # still parked
    two = [(at(30), at(35)), (at(10), at(15))]  # unsorted on purpose
    assert active_spans(at(0), at(50), two) == [(at(0), at(10)), (at(15), at(30)), (at(35), at(50))]
