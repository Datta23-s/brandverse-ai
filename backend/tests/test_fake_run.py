"""The fake run's event sequence, without Redis: a list sink and a recorded sleep."""

from uuid import uuid4

import pytest

from app.core.pipeline import PIPELINE_STEPS
from app.models.runs import FakeRunJob, FakeRunRequest, RunEvent, StepEvent
from app.workers.fake_run import execute_fake_run
from app.workers.jobs import fake_run_job_id, run_id_from_job_id

pytestmark = pytest.mark.anyio


class ListSink:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append(self, event: RunEvent) -> str:
        self.events.append(event)
        return f"0-{len(self.events)}"


class RecordedSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


async def run(**request: object) -> tuple[bool, list[RunEvent], list[float]]:
    sink, sleep = ListSink(), RecordedSleep()
    job = FakeRunJob(
        run_id=uuid4(), request=FakeRunRequest.model_validate({"org_id": str(uuid4()), **request})
    )
    ok = await execute_fake_run(job, sink, sleep)
    assert all(e.run_id == job.run_id for e in sink.events)
    return ok, sink.events, sleep.calls


def steps(events: list[RunEvent]) -> list[tuple[int, str, str]]:
    return [(e.step, e.name, e.status.value) for e in events if isinstance(e, StepEvent)]


async def test_default_run_emits_every_step_started_then_completed() -> None:
    ok, events, sleeps = await run()
    assert ok
    assert events[0].type == "run.started"
    assert events[-1].type == "run.completed"
    expected = [
        (s.number, s.name, status) for s in PIPELINE_STEPS for status in ("started", "completed")
    ]
    assert steps(events) == expected
    assert sleeps == [0.4] * 15


async def test_d0_skips_research_without_waiting() -> None:
    ok, events, sleeps = await run(depth="D0", step_delay_ms=50)
    assert ok
    step6 = [s for s in steps(events) if s[0] == 6]
    assert step6 == [(6, "research", "skipped")]
    assert sleeps == [0.05] * 14


@pytest.mark.parametrize(("depth", "node"), [("D1", "focused_research"), ("D2", "deep_research")])
async def test_research_detail_names_the_node(depth: str, node: str) -> None:
    _, events, _ = await run(depth=depth)
    started6 = next(e for e in events if isinstance(e, StepEvent) and e.step == 6)
    assert started6.detail == f"fake: {node}"


async def test_approval_is_labelled_as_simulated() -> None:
    _, events, _ = await run()
    step12 = next(e for e in events if isinstance(e, StepEvent) and e.step == 12)
    assert step12.detail is not None and "no human action recorded" in step12.detail


async def test_simulated_failure_stops_the_run() -> None:
    ok, events, _ = await run(fail_at_step=4)
    assert not ok
    assert steps(events)[-2:] == [
        (4, "retrieve_memory", "started"),
        (4, "retrieve_memory", "failed"),
    ]
    assert events[-1].type == "run.failed"
    assert events[-1].step == 4  # type: ignore[union-attr]
    assert not any(isinstance(e, StepEvent) and e.step > 4 for e in events)


def test_run_id_round_trips_through_the_job_id() -> None:
    run_id = uuid4()
    assert run_id_from_job_id(fake_run_job_id(run_id)) == run_id
    assert run_id_from_job_id("4897066c975940bda7d098f4fe9880d4") is None  # e.g. a ping job
    assert run_id_from_job_id("fake-run:not-a-uuid") is None
