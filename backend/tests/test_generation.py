"""The resumable generation run (decision-log 2.48, 2.50, 2.52): the checkpoint, the resume, the
rounds, and the test the owner asked for: kill the process mid-batch and continue."""

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.evaluation.generation import (
    Checkpoint,
    GenerationStopped,
    Item,
    generate,
    wave_items,
)
from app.evaluation.schedule import registered_order
from app.evaluation.schedule import test_set_requests as request_rows
from tests.generation_fakes import FileRunner, round_one_items

pytestmark = pytest.mark.anyio

POSITIONS = {r["id"]: r["position"] for r in request_rows()}


def started_keys(checkpoint: Checkpoint) -> list[str]:
    return [e["item"] for e in checkpoint.events() if e["event"] == "start"]


def test_the_rounds_partition_the_registered_order_and_keep_the_arms_adjacent() -> None:
    order = registered_order()
    rounds = [wave_items(order, POSITIONS, w) for w in ("round:1", "round:2", "round:3")]  # type: ignore[arg-type]
    assert [len(r) for r in rounds] == [30, 30, 30]
    everything = [i.key for r in rounds for i in r]
    assert sorted(everything) == sorted(f"{r}:{a}" for r, a in order)
    registered = [f"{r}:{a}" for r, a in order]
    for n, rnd in enumerate(rounds, start=1):
        assert {i.position for i in rnd} == {n}
        positions = [registered.index(i.key) for i in rnd]
        assert positions == sorted(positions)  # a subsequence: never reordered
        # THE POINT OF THE ROUND (owner, 7 November): the three arms of a request are adjacent, so
        # an outage lands across arms. Triples of consecutive items share one request id.
        for k in range(0, 30, 3):
            assert len({i.request_id for i in rnd[k : k + 3]}) == 1
            assert {i.arm for i in rnd[k : k + 3]} == {"baseline_a", "baseline_b", "proposed"}


async def test_a_finished_batch_is_not_repeated_on_resume(tmp_path: Path) -> None:
    ckpt, items = Checkpoint(tmp_path / "c.jsonl"), round_one_items()
    runner = FileRunner(tmp_path / "db.json")
    first = await generate(items, runner, ckpt, "round:1", stop_after=7)
    assert len(first.started) == 7
    second = await generate(items, FileRunner(tmp_path / "db.json"), ckpt, "round:1")
    assert len(second.skipped_done) == 7 and len(second.started) == 23
    keys = started_keys(ckpt)
    assert len(keys) == 30 and len(set(keys)) == 30
    third = await generate(items, FileRunner(tmp_path / "db.json"), ckpt, "round:1")
    assert third.started == [] and len(third.skipped_done) == 30


def test_killing_the_process_mid_batch_continues_from_the_next_campaign(tmp_path: Path) -> None:
    """The owner's test: a REAL process, killed while campaign 5 is in flight, then resumed. The
    killed run's worker is still alive (as in the dry run), so the resume WAITS for it and does
    not start a second run."""
    items = round_one_items()
    hang = items[4].key
    backend = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, this interpreter
        [sys.executable, "-m", "tests.generation_fakes", str(tmp_path), hang], cwd=backend
    )
    ckpt = Checkpoint(tmp_path / "checkpoint.jsonl")
    deadline = time.time() + 60
    while time.time() < deadline and hang not in started_keys(ckpt):
        time.sleep(0.1)
    assert hang in started_keys(ckpt), "the batch never reached the item the test kills it on"
    proc.kill()
    proc.wait()
    done_before = [e["item"] for e in ckpt.events() if e["event"] == "end"]
    assert done_before == [i.key for i in items[:4]]  # four finished, the fifth was in flight

    report = asyncio.run(generate(items, FileRunner(tmp_path / "db.json"), ckpt, "round:1"))
    assert len(report.skipped_done) == 4  # 1 to 4 are not run again
    assert report.reconciled == [hang]  # the in-flight run is waited for, not repeated
    keys = started_keys(ckpt)
    assert all(keys.count(i.key) == 1 for i in items)  # none doubled
    settled = {e["item"] for e in ckpt.events() if e["event"] == "end" and e["outcome"] == "parked"}
    assert settled == {i.key for i in items}  # all 30 settled, none lost


async def test_a_run_that_does_not_settle_stops_the_batch_and_is_never_started_beside(
    tmp_path: Path,
) -> None:
    """R9 (owner, 7 November): a wait that times out used to start a SECOND run for the item."""
    ckpt, items = Checkpoint(tmp_path / "c.jsonl"), round_one_items()
    db = tmp_path / "db.json"
    key = items[0].key
    runner = FileRunner(db, stuck=frozenset({"run-1"}))
    with pytest.raises(GenerationStopped, match="did not settle"):
        await generate(items, runner, ckpt, "round:1")
    assert runner.started == [key]  # one run, and the batch stopped there
    ends = [e for e in ckpt.events() if e["event"] == "end"]
    assert [(e["item"], e["outcome"], e["run_id"]) for e in ends] == [(key, "interrupted", "run-1")]
    # resumed while it is STILL unsettled: stops again, starts nothing
    again = FileRunner(db, stuck=frozenset({"run-1"}))
    with pytest.raises(GenerationStopped, match="did not settle"):
        await generate(items, again, ckpt, "round:1")
    assert again.started == []
    # the worker recovers and the run settles: the resume records it and goes on, no second run
    settler = FileRunner(db)
    await settler.wait("run-1")
    report = await generate(items, FileRunner(db), ckpt, "round:1", stop_after=2)
    assert key not in started_keys(ckpt)[1:]  # started once, ever
    assert report.reconciled == [key]


async def test_a_run_that_finished_while_the_harness_was_dead_is_not_run_again(
    tmp_path: Path,
) -> None:
    ckpt, items = Checkpoint(tmp_path / "c.jsonl"), round_one_items()
    db = tmp_path / "db.json"
    await generate(items, FileRunner(db), ckpt, "round:1", stop_after=2)
    # the harness died after run 3 started and the run parked, before the `end` line was written
    ckpt.write({"event": "intent", "item": items[2].key, "attempt": 1})
    runner = FileRunner(db)
    run_id = await runner.start(items[2])
    await runner.wait(run_id)
    ckpt.write({"event": "start", "item": items[2].key, "attempt": 1, "run_id": run_id})
    report = await generate(items, FileRunner(db), ckpt, "round:1", stop_after=1)
    assert report.reconciled == [items[2].key] and items[2].key not in report.started
    assert started_keys(ckpt).count(items[2].key) == 1


async def test_a_kill_between_the_run_starting_and_its_id_being_recorded_makes_no_second_run(
    tmp_path: Path,
) -> None:
    ckpt, items = Checkpoint(tmp_path / "c.jsonl"), round_one_items()
    db = tmp_path / "db.json"
    with pytest.raises(SystemExit):
        await generate(items, FileRunner(db, die_after_start=items[1].key), ckpt, "round:1")
    runs_before = len(json.loads(db.read_text(encoding="utf-8")))
    runner = FileRunner(db)
    await generate(items, runner, ckpt, "round:1", stop_after=1)
    assert items[1].key not in runner.started  # the orphan was found and adopted, not duplicated
    rows = [
        r for r in json.loads(db.read_text(encoding="utf-8")).values() if r["item"] == items[1].key
    ]
    assert len(rows) == 1 and runs_before == 2


async def test_an_infrastructure_failure_is_rerun_twice_and_the_third_escalates(
    tmp_path: Path,
) -> None:
    ckpt, items = Checkpoint(tmp_path / "c.jsonl"), round_one_items()
    bad = items[0].key
    outage = ("invalidated", "provider_unavailable")
    runner = FileRunner(tmp_path / "db.json", outcomes={bad: [outage, outage, outage]})  # type: ignore[list-item]
    with pytest.raises(GenerationStopped, match="comes to the owner"):
        await generate(items, runner, ckpt, "round:1")
    assert started_keys(ckpt).count(bad) == 3  # the original and two re-runs, no fourth


async def test_two_failures_then_success_is_a_primary_result_after_two_reruns(
    tmp_path: Path,
) -> None:
    ckpt, items = Checkpoint(tmp_path / "c.jsonl"), round_one_items()
    bad = items[0].key
    outage = ("invalidated", "provider_unavailable")
    runner = FileRunner(tmp_path / "db.json", outcomes={bad: [outage, outage]})  # type: ignore[list-item]
    report = await generate(items, runner, ckpt, "round:1", stop_after=1)
    assert report.reruns == [bad, bad] and started_keys(ckpt) == [bad] * 3


async def test_an_interrupted_attempt_does_not_use_up_the_rerun_cap(tmp_path: Path) -> None:
    ckpt, items = Checkpoint(tmp_path / "c.jsonl"), round_one_items()
    key = items[0].key
    for attempt in (1, 2, 3):  # three harness kills before any run id came back
        ckpt.write({"event": "intent", "item": key, "attempt": attempt})
        ckpt.write({"event": "end", "item": key, "attempt": attempt, "outcome": "interrupted"})
    runner = FileRunner(tmp_path / "db.json")
    await generate(items, runner, ckpt, "round:1", stop_after=1)
    assert runner.started == [key]  # still runs: interruptions are not outages


async def test_a_later_round_is_refused_whole_until_the_earlier_one_is_decided(
    tmp_path: Path,
) -> None:
    ckpt = Checkpoint(tmp_path / "c.jsonl")
    items = wave_items(registered_order(), POSITIONS, "round:2")
    with pytest.raises(GenerationStopped, match="human decision"):
        await generate(items, FileRunner(tmp_path / "db.json"), ckpt, "round:2")
    assert ckpt.events() == []  # refused before anything was written or started
    decided = frozenset({("proposed", 1)})
    report = await generate(
        items, FileRunner(tmp_path / "db.json", decided_positions=decided), ckpt, "round:2"
    )
    assert len(report.started) == 30  # the Baselines of the round went with the Proposed arm


def test_a_line_cut_off_by_the_kill_is_ignored_and_one_in_the_middle_is_not(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    good = json.dumps({"at": "t", "event": "intent", "item": "R01:baseline_a", "attempt": 1})
    path.write_text(good + "\n" + '{"at": "t", "event": "sta', encoding="utf-8")
    assert len(Checkpoint(path).events()) == 1
    path.write_text('{"broken\n' + good + "\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        Checkpoint(path).events()


def test_an_item_is_identified_by_request_and_arm() -> None:
    assert Item("R01", "baseline_a", 1).key == "R01:baseline_a"
