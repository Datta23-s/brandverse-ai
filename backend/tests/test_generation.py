"""The resumable generation run (decision-log 2.48, item 3): the checkpoint, the resume, the waves,
and the test the owner asked for: kill the process mid-batch and continue."""

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
from tests.generation_fakes import FileRunner, baseline_items

pytestmark = pytest.mark.anyio

POSITIONS = {r["id"]: r["position"] for r in request_rows()}


def started_keys(checkpoint: Checkpoint) -> list[str]:
    return [e["item"] for e in checkpoint.events() if e["event"] == "start"]


def test_the_waves_partition_the_registered_order_and_keep_its_sequence() -> None:
    order = registered_order()
    baselines = wave_items(order, POSITIONS, "baselines")
    proposed = [wave_items(order, POSITIONS, w) for w in ("proposed:1", "proposed:2", "proposed:3")]  # type: ignore[arg-type]
    assert len(baselines) == 60 and [len(p) for p in proposed] == [10, 10, 10]
    everything = [i.key for i in baselines] + [i.key for p in proposed for i in p]
    assert sorted(everything) == sorted(f"{r}:{a}" for r, a in order)
    # the filter never reorders: each wave is a subsequence of the registered order
    registered = [f"{r}:{a}" for r, a in order]
    for wave in (baselines, *proposed):
        positions = [registered.index(i.key) for i in wave]
        assert positions == sorted(positions)
    assert {i.position for i in proposed[1]} == {2}


async def test_a_finished_batch_is_not_repeated_on_resume(tmp_path: Path) -> None:
    ckpt, items = Checkpoint(tmp_path / "c.jsonl"), baseline_items()
    runner = FileRunner(tmp_path / "db.json")
    first = await generate(items, runner, ckpt, "baselines", stop_after=7)
    assert len(first.started) == 7
    second = await generate(items, FileRunner(tmp_path / "db.json"), ckpt, "baselines")
    assert len(second.skipped_done) == 7 and len(second.started) == 53
    keys = started_keys(ckpt)
    assert len(keys) == 60 and len(set(keys)) == 60
    # a third invocation has nothing left to do
    third = await generate(items, FileRunner(tmp_path / "db.json"), ckpt, "baselines")
    assert third.started == [] and len(third.skipped_done) == 60


def test_killing_the_process_mid_batch_continues_from_the_next_campaign(tmp_path: Path) -> None:
    """The owner's test: a REAL process, killed while campaign 5 is in flight, then resumed."""
    items = baseline_items()
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

    import asyncio

    # the killed run's worker died with it: its run is still `running`, and waiting times out
    dead_run = next(
        e["run_id"] for e in ckpt.events() if e["event"] == "start" and e["item"] == hang
    )
    report = asyncio.run(
        generate(
            items, FileRunner(tmp_path / "db.json", stuck=frozenset({dead_run})), ckpt, "baselines"
        )
    )
    assert len(report.skipped_done) == 4  # 1 to 4 are not run again
    assert report.interrupted == [hang]  # the in-flight one is recorded, then run again
    keys = started_keys(ckpt)
    assert keys.count(hang) == 2  # the interrupted attempt and its replacement
    assert all(keys.count(i.key) == 1 for i in items if i.key != hang)
    settled = {e["item"] for e in ckpt.events() if e["event"] == "end" and e["outcome"] == "parked"}
    assert settled == {i.key for i in items}  # all 60 settled, none lost, none doubled


async def test_a_run_that_finished_while_the_harness_was_dead_is_not_run_again(
    tmp_path: Path,
) -> None:
    ckpt, items = Checkpoint(tmp_path / "c.jsonl"), baseline_items()
    db = tmp_path / "db.json"
    await generate(items, FileRunner(db), ckpt, "baselines", stop_after=2)
    # the harness died after run 3 started and the run parked, before the `end` line was written
    ckpt.write({"event": "intent", "item": items[2].key, "attempt": 1})
    runner = FileRunner(db)
    run_id = await runner.start(items[2])
    await runner.wait(run_id)
    ckpt.write({"event": "start", "item": items[2].key, "attempt": 1, "run_id": run_id})
    report = await generate(items, FileRunner(db), ckpt, "baselines", stop_after=1)
    assert report.reconciled == [items[2].key] and items[2].key not in report.started
    assert started_keys(ckpt).count(items[2].key) == 1


async def test_a_kill_between_the_run_starting_and_its_id_being_recorded_makes_no_second_run(
    tmp_path: Path,
) -> None:
    ckpt, items = Checkpoint(tmp_path / "c.jsonl"), baseline_items()
    db = tmp_path / "db.json"
    with pytest.raises(SystemExit):
        await generate(items, FileRunner(db, die_after_start=items[1].key), ckpt, "baselines")
    runs_before = len(json.loads(db.read_text(encoding="utf-8")))
    runner = FileRunner(db)
    await generate(items, runner, ckpt, "baselines", stop_after=1)
    assert items[1].key not in runner.started  # the orphan was found and adopted, not duplicated
    rows = [
        r for r in json.loads(db.read_text(encoding="utf-8")).values() if r["item"] == items[1].key
    ]
    assert len(rows) == 1 and runs_before == 2


async def test_an_infrastructure_failure_is_rerun_twice_and_the_third_escalates(
    tmp_path: Path,
) -> None:
    ckpt, items = Checkpoint(tmp_path / "c.jsonl"), baseline_items()
    bad = items[0].key
    outage = ("invalidated", "provider_unavailable")
    runner = FileRunner(tmp_path / "db.json", outcomes={bad: [outage, outage, outage]})  # type: ignore[list-item]
    with pytest.raises(GenerationStopped, match="comes to the owner"):
        await generate(items, runner, ckpt, "baselines")
    assert started_keys(ckpt).count(bad) == 3  # the original and two re-runs, no fourth


async def test_two_failures_then_success_is_a_primary_result_after_two_reruns(
    tmp_path: Path,
) -> None:
    ckpt, items = Checkpoint(tmp_path / "c.jsonl"), baseline_items()
    bad = items[0].key
    outage = ("invalidated", "provider_unavailable")
    runner = FileRunner(tmp_path / "db.json", outcomes={bad: [outage, outage]})  # type: ignore[list-item]
    report = await generate(items, runner, ckpt, "baselines", stop_after=1)
    assert report.reruns == [bad, bad] and started_keys(ckpt) == [bad] * 3


async def test_an_interrupted_attempt_does_not_use_up_the_rerun_cap(tmp_path: Path) -> None:
    ckpt, items = Checkpoint(tmp_path / "c.jsonl"), baseline_items()
    key = items[0].key
    for attempt in (1, 2, 3):  # three harness kills on the same campaign
        ckpt.write({"event": "intent", "item": key, "attempt": attempt})
        ckpt.write({"event": "start", "item": key, "attempt": attempt, "run_id": f"dead-{attempt}"})
        ckpt.write({"event": "end", "item": key, "attempt": attempt, "outcome": "interrupted"})
    runner = FileRunner(tmp_path / "db.json")
    await generate(items, runner, ckpt, "baselines", stop_after=1)
    assert runner.started == [key]  # still runs: interruptions are not outages


async def test_a_later_proposed_wave_is_refused_until_the_earlier_one_is_decided(
    tmp_path: Path,
) -> None:
    ckpt = Checkpoint(tmp_path / "c.jsonl")
    items = wave_items(registered_order(), POSITIONS, "proposed:2")
    with pytest.raises(GenerationStopped, match="human decision"):
        await generate(items, FileRunner(tmp_path / "db.json"), ckpt, "proposed:2")
    assert ckpt.events() == []  # refused before anything was written or started
    decided = frozenset({("proposed", 1)})
    report = await generate(
        items, FileRunner(tmp_path / "db.json", decided_positions=decided), ckpt, "proposed:2"
    )
    assert len(report.started) == 10


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
