"""ARQ job functions. Each validates its JSON payload with Pydantic before doing anything."""

import logging
import os
from typing import Any
from uuid import UUID

from app.core.logging import bind_run_id, set_run_id
from app.core.run_events import RunEventLog
from app.models.jobs import PingJob, PingResult
from app.models.runs import FakeRunJob, RunFailed
from app.workers.fake_run import execute_fake_run

log = logging.getLogger(__name__)

PING = "ping"
RUN_FAKE_PIPELINE = "run_fake_pipeline"

# A run's job ID carries its run ID, so the worker can bind run_id before ARQ logs anything.
_FAKE_RUN_JOB_PREFIX = "fake-run:"


def fake_run_job_id(run_id: UUID) -> str:
    return f"{_FAKE_RUN_JOB_PREFIX}{run_id}"


def run_id_from_job_id(job_id: str) -> UUID | None:
    """The run ID in a run's job ID; None for jobs that belong to no run (e.g. `ping`)."""
    if not job_id.startswith(_FAKE_RUN_JOB_PREFIX):
        return None
    try:
        return UUID(job_id.removeprefix(_FAKE_RUN_JOB_PREFIX))
    except ValueError:
        return None


async def bind_job_run_id(ctx: dict[str, Any]) -> None:
    """ARQ `on_job_start` hook: put the job's run_id on every log line of the job (Rules.md P12).

    ARQ runs each job in its own task and calls this hook inside it, before logging the job's
    start (`→`) and before creating the job function's task, which inherits the context. So
    ARQ's start, finish (`←`) and failure lines and everything the job logs carry run_id; the
    setting dies with the task. Not covered: lines ARQ logs before this hook (job expired,
    undecodable, max retries exceeded), which have no run_id.
    """
    run_id = run_id_from_job_id(ctx["job_id"])
    if run_id is not None:
        set_run_id(str(run_id))


async def ping(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Trivial job: proves a job can be enqueued, picked up by the worker and completed."""
    job = PingJob.model_validate(payload)
    log.info("ping job", extra={"job_id": ctx.get("job_id")})
    return PingResult(message=job.message, worker_pid=os.getpid()).model_dump(mode="json")


async def run_fake_pipeline(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    job = FakeRunJob.model_validate(payload)
    events: RunEventLog = ctx["events"]
    with bind_run_id(str(job.run_id)):
        try:
            completed = await execute_fake_run(job, events)
        except Exception as e:
            # Never leave a subscriber waiting on a run that died: report it, then let ARQ record
            # the failure too (Rules.md P13).
            log.exception("fake run crashed")
            await events.append(
                RunFailed(run_id=job.run_id, error=f"internal error: {type(e).__name__}")
            )
            raise
    return {"run_id": str(job.run_id), "status": "completed" if completed else "failed"}
