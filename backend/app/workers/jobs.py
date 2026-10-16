"""ARQ job functions. Each validates its JSON payload with Pydantic before doing anything."""

import logging
import os
from typing import Any

from app.core.logging import bind_run_id
from app.core.run_events import RunEventLog
from app.models.jobs import PingJob, PingResult
from app.models.runs import FakeRunJob, RunFailed
from app.workers.fake_run import execute_fake_run

log = logging.getLogger(__name__)

PING = "ping"
RUN_FAKE_PIPELINE = "run_fake_pipeline"


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
