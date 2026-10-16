"""The ARQ worker: configuration, startup/shutdown, and the process entry point.

Runs on the laptop (Architecture.md §11, worker-host decision). Start it with
`uv run python -m app.workers` from `backend/`, with Redis up (`docker compose up -d redis`).
"""

import asyncio
import logging
import signal
from typing import Any

from arq.worker import Worker, func

from app.core.config import Settings, get_settings
from app.core.db import create_db_pool, ping_database
from app.core.logging import configure_logging
from app.core.redis import arq_redis_settings, json_deserializer, json_serializer
from app.core.run_events import RunEventLog
from app.workers.jobs import PING, RUN_FAKE_PIPELINE, bind_job_run_id, ping, run_fake_pipeline

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


async def on_shutdown(ctx: dict[str, Any]) -> None:
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
    log.info(
        "worker starting",
        extra={
            "max_jobs": settings.worker_max_jobs,
            "db_pool_max_size": settings.db_pool_worker_max_size,
        },
    )
    # Selector loop: psycopg's async mode refuses Windows' default Proactor loop.
    asyncio.run(run(settings), loop_factory=asyncio.SelectorEventLoop)
