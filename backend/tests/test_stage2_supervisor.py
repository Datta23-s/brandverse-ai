"""The stage-2 worker supervisor: a STOPGAP for the interim only (decision-log 2.25).

The worker has died of access violations in native code, silently: the run stays `queued` and
nothing parks. The supervisor records each death (with the campaign and the step reached), starts a
new worker, and gives up after a cap. These tests drive it with fake processes; no worker starts.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.phase7_stage2 import WorkerSupervisor, last_step_in

pytestmark = pytest.mark.anyio

ACCESS_VIOLATION = -1073741819  # 0xC0000005


class FakeProc:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 1

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode


def step_line(msg: str, run_id: str = "r-1") -> str:
    return json.dumps({"ts": "2026-11-04T10:00:00.000+00:00", "msg": msg, "run_id": run_id})


class Launcher:
    """Hands out fake workers; each writes a log that says it is ready and what it was doing."""

    def __init__(self, tmp: Path, lines: list[str] | None = None) -> None:
        self.tmp, self.procs, self.lines = tmp, [], lines or []

    def __call__(self, incarnation: int) -> tuple[Any, Path]:
        proc = FakeProc()
        self.procs.append(proc)
        path = self.tmp / f"worker{incarnation}.log"
        path.write_text("\n".join([step_line("pipeline ready"), *self.lines]), encoding="utf-8")
        return proc, path


def make(tmp: Path, **kw: Any) -> tuple[WorkerSupervisor, Launcher, dict[str, Any]]:
    lines = [
        step_line("step 5 assess_research_necessity: done in 0.0 s"),
        step_line("step 6 research: start"),
    ]
    launcher = Launcher(tmp, lines)
    campaign: dict[str, Any] = {"request": "R01", "campaign": 1}
    sup = WorkerSupervisor(
        launcher, crash_log=tmp / "crashes.jsonl", poll_s=0.01, campaign=campaign, **kw
    )
    return sup, launcher, campaign


async def _until(condition: Any) -> None:
    async with asyncio.timeout(5):
        while not condition():  # noqa: ASYNC110 - polls a fake
            await asyncio.sleep(0.01)


def test_last_step_in_reads_the_last_pipeline_step_line() -> None:
    text = "\n".join(
        [
            step_line("step 1 normalise_request: start", "a"),
            step_line("some other message", "a"),
            step_line("step 6 research: start", "b"),
            "not json at all",
        ]
    )
    found = last_step_in(text)
    assert found is not None and found["line"] == "step 6 research: start"
    assert found["run_id"] == "b"
    assert last_step_in("nothing relevant\n") is None


async def test_a_dead_worker_is_recorded_and_started_again(tmp_path: Path) -> None:
    sup, launcher, campaign = make(tmp_path)
    assert await sup.start()
    watcher = asyncio.create_task(sup.watch(None))
    launcher.procs[0].returncode = ACCESS_VIOLATION  # the worker dies mid-research
    await _until(lambda: len(launcher.procs) >= 2)
    assert sup.incarnation == 1 and sup.gave_up is None
    assert len(sup.crashes) == 1
    crash = sup.crashes[0]
    assert crash["exit_code"] == ACCESS_VIOLATION
    assert crash["campaign"] == campaign  # which campaign it was running
    assert crash["last_step"]["line"] == "step 6 research: start"  # and the step it had reached
    # And it is on disk, so the rate can be measured later without this process.
    written = [json.loads(x) for x in (tmp_path / "crashes.jsonl").read_text().splitlines()]
    assert len(written) == 1 and written[0]["exit_code"] == ACCESS_VIOLATION
    sup.stop()
    await watcher


async def test_each_incarnation_has_its_own_log_so_the_dead_ones_survive(tmp_path: Path) -> None:
    sup, launcher, _ = make(tmp_path)
    await sup.start()
    watcher = asyncio.create_task(sup.watch(None))
    launcher.procs[0].returncode = ACCESS_VIOLATION
    await _until(lambda: len(launcher.procs) >= 2)
    assert [p.name for p in sup.logs] == ["worker0.log", "worker1.log"]
    assert all(p.exists() for p in sup.logs)
    # Two workers each started one research step, so two are counted: the rate's denominator.
    assert sup.research_starts() == 2
    sup.stop()
    await watcher


async def test_it_gives_up_after_the_cap_and_cancels_the_main_task(tmp_path: Path) -> None:
    sup, launcher, _ = make(tmp_path, max_restarts=2)
    await sup.start()

    async def main() -> None:
        await asyncio.sleep(60)

    main_task = asyncio.create_task(main())
    watcher = asyncio.create_task(sup.watch(main_task))
    deaths = 0
    async with asyncio.timeout(10):
        while sup.gave_up is None:
            if launcher.procs[-1].returncode is None:
                launcher.procs[-1].returncode = ACCESS_VIOLATION
                deaths += 1
            await asyncio.sleep(0.01)
    await watcher
    assert deaths == 3 and len(sup.crashes) == 3  # the cap is 2 restarts: the third death ends it
    assert sup.gave_up is not None and "over the 2 allowed" in sup.gave_up
    with pytest.raises(asyncio.CancelledError):
        await main_task


async def test_a_deliberate_stop_is_not_a_crash(tmp_path: Path) -> None:
    sup, launcher, _ = make(tmp_path)
    await sup.start()
    watcher = asyncio.create_task(sup.watch(None))
    sup.stop()
    await asyncio.wait_for(watcher, 5)
    assert launcher.procs[0].terminated
    assert sup.crashes == [] and len(launcher.procs) == 1


async def test_a_worker_that_will_not_start_ends_the_attempt(tmp_path: Path) -> None:
    sup, launcher, _ = make(tmp_path)
    await sup.start()
    watcher = asyncio.create_task(sup.watch(None))

    def dead_on_arrival(incarnation: int) -> tuple[Any, Path]:
        proc = FakeProc()
        proc.returncode = 1
        path = tmp_path / f"never{incarnation}.log"
        path.write_text("starting\n", encoding="utf-8")  # never says "pipeline ready"
        return proc, path

    sup._launch = dead_on_arrival  # noqa: SLF001 - the restart itself fails
    launcher.procs[0].returncode = ACCESS_VIOLATION
    await asyncio.wait_for(watcher, 5)
    assert sup.gave_up is not None and "would not start again" in sup.gave_up
