"""A recovery never runs beside a run (app/workers/gate.py), and a job's deadline is its own.

THE HAZARD these tests pin (stage-2 interim, 2026-11-04): the worker's start-up sweep resumed a
crashed run in parallel with a live one; their research fought over the CPU, the live run's step 3
took 277 s instead of 8 s, and ARQ killed its job at 600 s. Here the two jobs are driven for real
through `run_pipeline` and `recover_pipeline` with fake pipelines, and the order of events is
asserted: no overlap in either direction.
"""

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.logging import unbuffer_stdio
from app.errors import JobTimedOut
from app.orchestrator.runner import PipelineOutcome
from app.workers.gate import ExecutionGate
from app.workers.jobs import recover_pipeline, resume_pipeline, run_pipeline

pytestmark = pytest.mark.anyio


class FakePipeline:
    """Records when each job's work starts and ends, and can be told to wait."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.release: dict[str, asyncio.Event] = {}
        self.timed_out: list[tuple[Any, float]] = []

    def hold(self, name: str) -> asyncio.Event:
        self.release[name] = asyncio.Event()
        return self.release[name]

    async def _work(self, name: str) -> PipelineOutcome:
        self.events.append(f"{name}:start")
        if name in self.release:
            await self.release[name].wait()
        self.events.append(f"{name}:end")
        return PipelineOutcome("completed", {})

    async def start(self, **kw: Any) -> PipelineOutcome:
        return await self._work(f"run-{kw['brief']}")

    async def resume(self, run_id: Any, *a: Any, **kw: Any) -> PipelineOutcome:
        return await self._work("resume")

    async def recover(self, run_id: Any) -> PipelineOutcome:
        return await self._work("recover")

    async def fail_timed_out(self, run_id: Any, after_s: float) -> dict[str, Any]:
        self.timed_out.append((run_id, after_s))
        return {"message": "fake: job exceeded its limit"}


def _run_payload(brief: str) -> dict[str, Any]:
    return {
        "run_id": str(uuid4()),
        "request": {"org_id": str(uuid4()), "product_id": str(uuid4()), "brief": brief},
        "by": str(uuid4()),
    }


def _ctx(p: FakePipeline, gate: ExecutionGate, timeout_s: int = 1800) -> dict[str, Any]:
    return {
        "pipeline": p,
        "gate": gate,
        "settings": Settings(_env_file=None, worker_job_timeout_s=timeout_s),  # type: ignore[call-arg]
    }


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


# ── The gate itself ───────────────────────────────────────────────────────────────────────────


async def test_run_jobs_share_the_gate() -> None:
    gate = ExecutionGate()
    async with gate.shared(), gate.shared():
        assert gate.running == 2


async def test_a_recovery_waits_for_a_running_run_and_then_runs_alone() -> None:
    p, gate = FakePipeline(), ExecutionGate()
    release_run = p.hold("run-a")
    release_recover = p.hold("recover")
    run = asyncio.create_task(run_pipeline(_ctx(p, gate), _run_payload("a")))
    await _settle()
    recover = asyncio.create_task(recover_pipeline(_ctx(p, gate), {"run_id": str(uuid4())}))
    await _settle()
    assert p.events == ["run-a:start"]  # the recovery is queued behind the running run
    release_run.set()
    await run
    await _settle()
    assert p.events == ["run-a:start", "run-a:end", "recover:start"]
    release_recover.set()
    await recover
    assert p.events[-1] == "recover:end"


async def test_no_run_starts_while_a_recovery_is_running() -> None:
    p, gate = FakePipeline(), ExecutionGate()
    release_recover = p.hold("recover")
    recover = asyncio.create_task(recover_pipeline(_ctx(p, gate), {"run_id": str(uuid4())}))
    await _settle()
    run = asyncio.create_task(run_pipeline(_ctx(p, gate), _run_payload("b")))
    await _settle()
    assert p.events == ["recover:start"]  # the live run waits for the recovery to finish
    release_recover.set()
    await recover
    await run
    assert p.events == ["recover:start", "recover:end", "run-b:start", "run-b:end"]


async def test_a_waiting_recovery_is_not_starved_by_a_stream_of_new_runs() -> None:
    p, gate = FakePipeline(), ExecutionGate()
    release_a = p.hold("run-a")
    run_a = asyncio.create_task(run_pipeline(_ctx(p, gate), _run_payload("a")))
    await _settle()
    recover = asyncio.create_task(recover_pipeline(_ctx(p, gate), {"run_id": str(uuid4())}))
    await _settle()
    run_b = asyncio.create_task(run_pipeline(_ctx(p, gate), _run_payload("b")))
    await _settle()
    assert p.events == ["run-a:start"]  # b queued behind the waiting recovery, not beside a
    release_a.set()
    await asyncio.gather(run_a, recover, run_b)
    assert p.events.index("recover:end") < p.events.index("run-b:start")


async def test_a_cancelled_recovery_wait_does_not_leave_runs_blocked() -> None:
    p, gate = FakePipeline(), ExecutionGate()
    release_a = p.hold("run-a")
    run_a = asyncio.create_task(run_pipeline(_ctx(p, gate), _run_payload("a")))
    await _settle()
    recover = asyncio.create_task(recover_pipeline(_ctx(p, gate), {"run_id": str(uuid4())}))
    await _settle()
    recover.cancel()
    with pytest.raises(asyncio.CancelledError):
        await recover
    run_b = asyncio.create_task(run_pipeline(_ctx(p, gate), _run_payload("b")))
    await _settle()
    assert "run-b:start" in p.events  # not stuck behind a recovery that is gone
    release_a.set()
    await asyncio.gather(run_a, run_b)


async def test_a_resume_is_a_run_job_and_shares_the_gate() -> None:
    p, gate = FakePipeline(), ExecutionGate()
    release_run = p.hold("run-a")
    run = asyncio.create_task(run_pipeline(_ctx(p, gate), _run_payload("a")))
    await _settle()
    payload = {"run_id": str(uuid4()), "outcome": "approve", "by": str(uuid4()), "reason": None}
    await resume_pipeline(_ctx(p, gate), payload)
    assert p.events[:3] == ["run-a:start", "resume:start", "resume:end"]
    release_run.set()
    await run


# ── The job's own deadline ────────────────────────────────────────────────────────────────────


async def test_a_job_past_its_own_deadline_fails_its_run_and_raises_job_timed_out() -> None:
    p, gate = FakePipeline(), ExecutionGate()
    p.hold("run-slow")  # never released
    with pytest.raises(JobTimedOut, match="fake: job exceeded"):
        await run_pipeline(_ctx(p, gate, timeout_s=1), _run_payload("slow"))
    assert len(p.timed_out) == 1
    assert p.timed_out[0][1] == pytest.approx(0.95)  # 1 s minus 5 percent
    assert not isinstance(JobTimedOut("x"), TimeoutError)  # never classified network_error


async def test_the_works_own_timeout_is_not_mistaken_for_the_jobs_deadline() -> None:
    p, gate = FakePipeline(), ExecutionGate()

    async def own_timeout(**kw: Any) -> PipelineOutcome:
        raise TimeoutError("a provider call timed out")

    p.start = own_timeout  # type: ignore[method-assign]
    with pytest.raises(TimeoutError, match="provider call"):
        await run_pipeline(_ctx(p, gate), _run_payload("x"))
    assert p.timed_out == []  # the run is not marked as a job timeout


async def test_a_job_without_settings_has_no_deadline_and_still_runs() -> None:
    p = FakePipeline()
    out = await run_pipeline({"pipeline": p}, _run_payload("plain"))  # the shape older tests use
    assert out["status"] == "completed"


# ── Settings and logging ──────────────────────────────────────────────────────────────────────


def test_the_production_job_timeout_is_1800_seconds() -> None:
    """Basis: research measured at 205 to 359 s, so 600 s gave under a 2x margin and contention
    consumed it; 1800 s is about 5x the worst seen (owner, 2026-11-04)."""
    assert Settings(_env_file=None).worker_job_timeout_s == 1800  # type: ignore[call-arg]


def test_unbuffer_stdio_turns_on_line_buffering_for_a_redirected_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import io
    import sys

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="utf-8", line_buffering=False, write_through=False)
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    assert stream.line_buffering is False
    unbuffer_stdio()
    assert stream.line_buffering is True
    stream.write("a line\n")
    # Reached the underlying file without a close or flush (Windows writes the newline as \r\n).
    assert raw.getvalue().rstrip() == b"a line"
