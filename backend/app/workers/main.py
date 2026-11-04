"""The ARQ worker: configuration, startup/shutdown, and the process entry point.

Runs on the laptop (Architecture.md §11, worker-host decision). Start it with
`uv run python -m app.workers` from `backend/`, with Redis up (`docker compose up -d redis`).
"""

import asyncio
import faulthandler
import logging
import signal
from contextlib import AsyncExitStack
from typing import Any
from uuid import uuid4

from arq.worker import Worker, func

from app.core.config import Settings, get_settings
from app.core.db import create_db_pool, ping_database
from app.core.logging import configure_logging
from app.core.redis import arq_redis_settings, json_deserializer, json_serializer
from app.core.run_events import RunEventLog
from app.models.campaigns import RecoverJob
from app.workers.jobs import (
    EDIT_OUT_PIPELINE,
    PING,
    RECOVER_PIPELINE,
    RESUME_PIPELINE,
    RUN_FAKE_PIPELINE,
    RUN_PIPELINE,
    bind_job_run_id,
    edit_out_pipeline,
    ping,
    recover_job_id,
    recover_pipeline,
    resume_pipeline,
    run_fake_pipeline,
    run_pipeline,
)
from app.workers.lease import LEASE_HELD_EXIT, LeaseHeld, acquire_worker_lease
from app.workers.pipeline_factory import open_pipeline
from app.workers.recovery import orphan_pending_calls, orphaned_runs

log = logging.getLogger(__name__)


async def on_startup(ctx: dict[str, Any]) -> None:
    settings: Settings = ctx["settings"]
    ctx["events"] = RunEventLog(
        ctx["redis"],
        ttl_s=settings.run_events_ttl_s,
        maxlen=settings.run_events_maxlen,
        block_ms=settings.run_events_block_ms,
    )
    ctx["db_pool"] = None
    if settings.database_url is None:
        log.warning("DATABASE_URL not set: worker database pool disabled")
        return
    pool = create_db_pool(settings, "worker")
    await pool.open()
    ctx["db_pool"] = pool
    try:
        await ping_database(pool)
        log.info("database reachable", extra={"pool_max_size": pool.max_size})
    except Exception:
        # No chunk-A job needs the database, so the worker keeps serving the queue; the failure
        # is logged, not swallowed (Rules.md P13).
        log.exception("database unreachable at worker startup")
        return
    stack = AsyncExitStack()
    ctx["stack"] = stack
    try:
        ctx["pipeline"] = await open_pipeline(settings, pool, ctx["events"], stack)
        log.info("pipeline ready", extra={"agents": settings.pipeline_agents})
    except Exception:
        # Real runs fail visibly (no `pipeline` in ctx); the fake run and ping keep working.
        log.exception("pipeline could not be built at worker startup")
        return
    await queue_recoveries(ctx, pool)


async def queue_recoveries(ctx: dict[str, Any], pool: Any) -> None:
    """A worker that has just started takes the previous one to have died (one worker: see
    app/workers/recovery.py) and queues a `recover_pipeline` job for each run it left behind."""
    try:
        orphaned_calls = await orphan_pending_calls(pool)
        if orphaned_calls:
            log.warning(
                "pending calls of a dead worker marked orphaned", extra={"n": orphaned_calls}
            )
        orphans = await orphaned_runs(pool)
        boot = uuid4().hex[:8]
        for run_id in orphans:
            await ctx["redis"].enqueue_job(
                RECOVER_PIPELINE,
                RecoverJob(run_id=run_id).model_dump(mode="json"),
                _job_id=recover_job_id(run_id, boot),
            )
        if orphans:
            log.warning("queued recovery for runs a dead worker left", extra={"runs": len(orphans)})
    except Exception:
        # A failed sweep must not stop the worker serving the queue; it is an error to see.
        log.exception("the recovery sweep failed")


async def on_shutdown(ctx: dict[str, Any]) -> None:
    stack = ctx.get("stack")
    if stack is not None:
        await stack.aclose()
    pool = ctx.get("db_pool")
    if pool is not None:
        await pool.close()


def build_worker(settings: Settings, *, burst: bool = False) -> Worker:
    """Must be called inside a running event loop: ARQ binds the worker to the current loop."""
    return Worker(
        functions=[
            func(ping, name=PING),
            # A crashed fake run reports `run.failed` itself; re-running it would replay events.
            func(run_fake_pipeline, name=RUN_FAKE_PIPELINE, max_tries=1),
            # A real run reports its own failure; ARQ must not start it a second time.
            func(run_pipeline, name=RUN_PIPELINE, max_tries=1),
            func(resume_pipeline, name=RESUME_PIPELINE, max_tries=1),
            func(edit_out_pipeline, name=EDIT_OUT_PIPELINE, max_tries=1),
            func(recover_pipeline, name=RECOVER_PIPELINE, max_tries=1),
        ],
        redis_settings=arq_redis_settings(settings),
        on_startup=on_startup,
        on_job_start=bind_job_run_id,
        on_shutdown=on_shutdown,
        ctx={"settings": settings},
        burst=burst,
        max_jobs=settings.worker_max_jobs,
        job_timeout=settings.worker_job_timeout_s,
        keep_result=3600,
        job_serializer=json_serializer,
        job_deserializer=json_deserializer,
    )


async def run(settings: Settings) -> None:
    worker = build_worker(settings)
    try:
        await worker.async_run()
    finally:
        # On Ctrl-C asyncio.run cancels this task; cancel in-flight jobs, then close cleanly.
        worker.handle_sig(signal.SIGINT)
        await worker.close()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.worker_stack_dump_s is not None:
        # Repeating all-thread stack dump to stderr, from a C watchdog thread that needs no GIL.
        faulthandler.enable()
        faulthandler.dump_traceback_later(settings.worker_stack_dump_s, repeat=True)
        log.info("stack dumps armed", extra={"every_s": settings.worker_stack_dump_s})
    log.info(
        "worker starting",
        extra={
            "max_jobs": settings.worker_max_jobs,
            "db_pool_max_size": settings.db_pool_worker_max_size,
        },
    )
    # ONE worker (app/workers/lease.py): the recovery sweep takes a starting worker to mean the
    # previous one died, so a second live worker is refused here, before it can touch anything.
    try:
        lease = acquire_worker_lease(settings.worker_lease_port)
    except LeaseHeld as e:
        log.error(str(e))
        raise SystemExit(LEASE_HELD_EXIT) from e
    try:
        # Selector loop: psycopg's async mode refuses Windows' default Proactor loop.
        asyncio.run(run(settings), loop_factory=asyncio.SelectorEventLoop)
    finally:
        lease.close()
