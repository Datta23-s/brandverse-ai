"""ARQ job functions. Each validates its JSON payload with Pydantic before doing anything."""

import logging
import os
from typing import Any
from uuid import UUID

from app.core.campaigns import audit
from app.core.logging import bind_run_id, set_run_id
from app.core.run_events import RunEventLog
from app.models.campaigns import DecisionJob, EditOutJob, PipelineRunJob, RecoverJob
from app.models.jobs import PingJob, PingResult
from app.models.runs import FakeRunJob, RunFailed
from app.orchestrator.runner import EditOutRefused, NotAwaitingApproval, NotRecoverable, Pipeline
from app.workers.fake_run import execute_fake_run

log = logging.getLogger(__name__)

PING = "ping"
RUN_FAKE_PIPELINE = "run_fake_pipeline"
RUN_PIPELINE = "run_pipeline"
RESUME_PIPELINE = "resume_pipeline"
EDIT_OUT_PIPELINE = "edit_out_pipeline"
RECOVER_PIPELINE = "recover_pipeline"

# A run's job ID carries its run ID, so the worker can bind run_id before ARQ logs anything.
_FAKE_RUN_JOB_PREFIX = "fake-run:"
_RUN_JOB_PREFIX = "pipeline-run:"
_RESUME_JOB_PREFIX = "resume:"
_EDIT_OUT_JOB_PREFIX = "edit-out:"
_RECOVER_JOB_PREFIX = "recover:"
_PREFIXES = (
    _FAKE_RUN_JOB_PREFIX,
    _RUN_JOB_PREFIX,
    _RESUME_JOB_PREFIX,
    _EDIT_OUT_JOB_PREFIX,
    _RECOVER_JOB_PREFIX,
)


def fake_run_job_id(run_id: UUID) -> str:
    return f"{_FAKE_RUN_JOB_PREFIX}{run_id}"


def pipeline_run_job_id(run_id: UUID) -> str:
    return f"{_RUN_JOB_PREFIX}{run_id}"


def resume_job_id(run_id: UUID, revision_round: int) -> str:
    """One continuation per (run, round): ARQ refuses a second job under the same id, so a decision
    submitted twice cannot resume the run twice. The `approvals` row is keyed the same way."""
    return f"{_RESUME_JOB_PREFIX}{run_id}:{revision_round}"


def edit_out_job_id(run_id: UUID, nonce: str) -> str:
    return f"{_EDIT_OUT_JOB_PREFIX}{run_id}:{nonce}"


def recover_job_id(run_id: UUID, boot: str) -> str:
    """One recovery per (run, worker start): a sweep that sees the same run twice queues it once."""
    return f"{_RECOVER_JOB_PREFIX}{run_id}:{boot}"


def run_id_from_job_id(job_id: str) -> UUID | None:
    """The run ID in a run's job ID; None for jobs that belong to no run (e.g. `ping`)."""
    for prefix in _PREFIXES:
        if job_id.startswith(prefix):
            try:
                return UUID(job_id.removeprefix(prefix).split(":")[0])
            except ValueError:
                return None
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


async def run_pipeline(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Start a real run. The pipeline reports its own failures (`run.failed`) before re-raising."""
    job = PipelineRunJob.model_validate(payload)
    pipeline: Pipeline = ctx["pipeline"]
    with bind_run_id(str(job.run_id)):
        outcome = await pipeline.start(
            run_id=job.run_id,
            org_id=job.request.org_id,
            user_id=job.by,
            product_id=job.request.product_id,
            brief=job.request.brief,
            depth=job.request.depth,
            audience=job.request.audience,
        )
    return {"run_id": str(job.run_id), "status": outcome.status}


async def resume_pipeline(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Continue a parked run with the approver's recorded decision."""
    job = DecisionJob.model_validate(payload)
    pipeline: Pipeline = ctx["pipeline"]
    with bind_run_id(str(job.run_id)):
        try:
            outcome = await pipeline.resume(job.run_id, job.outcome, by=job.by, reason=job.reason)
        except NotAwaitingApproval:
            # Already continued (the job id dedupes within ARQ's result window, not beyond it).
            log.warning("decision job found the run no longer waiting; nothing done")
            return {"run_id": str(job.run_id), "status": "already_resumed"}
    return {"run_id": str(job.run_id), "status": outcome.status}


async def edit_out_pipeline(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Remove the named words from a parked campaign's copy and re-run the gates."""
    job = EditOutJob.model_validate(payload)
    pipeline: Pipeline = ctx["pipeline"]
    pool = ctx["db_pool"]
    with bind_run_id(str(job.run_id)):
        try:
            validation = await pipeline.edit_out(job.run_id, job.statement)
        except (EditOutRefused, NotAwaitingApproval) as e:
            log.warning("edit-out refused", extra={"reason": str(e)})
            return {"run_id": str(job.run_id), "status": "refused", "reason": str(e)}
        async with pool.connection() as conn:
            cur = await conn.execute("select org_id from public.runs where id = %s", (job.run_id,))
            row = await cur.fetchone()
        if row is not None:
            await audit(
                pool,
                org_id=row[0],
                actor_id=job.by,
                action="campaign.edited_out",
                run_id=job.run_id,
                details={"statement": job.statement, "gate_outcome": validation.get("outcome")},
            )
    return {"run_id": str(job.run_id), "status": "edited"}


async def recover_pipeline(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Continue a run a dead worker left, from its last checkpoint (app/workers/recovery.py).
    Steps that finished are not run again; the step that was running is (at-least-once)."""
    job = RecoverJob.model_validate(payload)
    pipeline: Pipeline = ctx["pipeline"]
    with bind_run_id(str(job.run_id)):
        try:
            outcome = await pipeline.recover(job.run_id)
        except NotRecoverable as e:
            log.warning("recovery found nothing to continue", extra={"reason": str(e)})
            return {"run_id": str(job.run_id), "status": "not_recoverable", "reason": str(e)}
    return {"run_id": str(job.run_id), "status": outcome.status}
