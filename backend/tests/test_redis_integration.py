"""Against a real Redis: the event log, the ARQ worker, and the API process without a database.

The authenticated API (fake runs, the SSE stream, the org check) is tested with Postgres too,
in tests/db/ (it needs both).
"""

import asyncio
import io
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest

from app.api.main import create_app
from app.core.config import Settings
from app.core.logging import configure_logging
from app.core.redis import create_arq_pool
from app.core.run_events import RunEventLog, stream_key
from app.errors import RunEventCorrupt
from app.models.jobs import PingJob, PingResult
from app.models.runs import (
    FakeRunJob,
    FakeRunRequest,
    RunCompleted,
    RunQueued,
    RunStarted,
    StepEvent,
    StepStatus,
)
from app.workers.jobs import PING, RUN_FAKE_PIPELINE, fake_run_job_id
from app.workers.main import build_worker
from tests.conftest import make_settings

pytestmark = [pytest.mark.anyio, pytest.mark.redis]


# ── Event log ───────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def event_log(settings: Settings) -> AsyncIterator[RunEventLog]:
    redis = await create_arq_pool(settings)
    try:
        yield RunEventLog(
            redis,
            ttl_s=settings.run_events_ttl_s,
            maxlen=settings.run_events_maxlen,
            block_ms=settings.run_events_block_ms,
        )
    finally:
        await redis.aclose(close_connection_pool=True)


async def test_append_sets_ttl_and_replays_in_order(redis_settings: Settings) -> None:
    run_id = uuid4()
    async with event_log(redis_settings) as log:
        ids = [
            await log.append(RunQueued(run_id=run_id, mode="fake", org_id=uuid4())),
            await log.append(RunStarted(run_id=run_id)),
            await log.append(RunCompleted(run_id=run_id)),
        ]
        ttl = await log._redis.ttl(stream_key(run_id))
        replayed = [s async for s in log.follow(run_id)]
    assert 0 < ttl <= redis_settings.run_events_ttl_s
    assert [s.entry_id for s in replayed] == ids
    assert [s.event.type for s in replayed] == ["run.queued", "run.started", "run.completed"]


async def test_follow_delivers_events_as_they_are_written(redis_settings: Settings) -> None:
    run_id = uuid4()
    async with event_log(redis_settings) as log:
        await log.append(RunQueued(run_id=run_id, mode="fake", org_id=uuid4()))
        received: list[tuple[float, str]] = []

        async def subscriber() -> None:
            async for s in log.follow(run_id):
                received.append((time.monotonic(), s.event.type))

        task = asyncio.create_task(subscriber())
        await asyncio.sleep(0.3)
        assert [t for _, t in received] == ["run.queued"]  # waiting, not finished
        await log.append(RunStarted(run_id=run_id))
        await asyncio.sleep(0.3)
        await log.append(RunCompleted(run_id=run_id))
        await asyncio.wait_for(task, timeout=5)
    assert [t for _, t in received] == ["run.queued", "run.started", "run.completed"]
    assert received[2][0] - received[1][0] >= 0.25


async def test_follow_after_the_terminal_event_ends(redis_settings: Settings) -> None:
    run_id = uuid4()
    async with event_log(redis_settings) as log:
        await log.append(RunStarted(run_id=run_id))
        last = await log.append(RunCompleted(run_id=run_id))
        after = [s async for s in log.follow(run_id, after=last)]
    assert after == []


async def test_corrupt_entry_raises(redis_settings: Settings) -> None:
    run_id = uuid4()
    async with event_log(redis_settings) as log:
        await log._redis.xadd(stream_key(run_id), {b"event": b'{"type":"nonsense"}'})
        with pytest.raises(RunEventCorrupt):
            await log.last(run_id)


# ── Queue: a trivial job end to end ─────────────────────────────────────────────────────────


async def test_ping_job_enqueues_and_completes(redis_settings: Settings) -> None:
    pool = await create_arq_pool(redis_settings)
    try:
        job = await pool.enqueue_job(PING, PingJob(message="hello").model_dump())
        assert job is not None
        worker = build_worker(redis_settings, burst=True)
        try:
            await worker.async_run()
        finally:
            await worker.close()
        assert worker.jobs_complete == 1 and worker.jobs_failed == 0
        result = PingResult.model_validate(await job.result(timeout=5))
    finally:
        await pool.aclose(close_connection_pool=True)
    assert result.message == "hello"
    assert result.worker_pid > 0


async def test_jobs_are_stored_as_json_not_pickle(redis_settings: Settings) -> None:
    pool = await create_arq_pool(redis_settings)
    try:
        job = await pool.enqueue_job(PING, {"message": "x"})
        assert job is not None
        raw = await pool.get(f"arq:job:{job.job_id}")
    finally:
        await pool.aclose(close_connection_pool=True)
    assert raw is not None and raw.startswith(b"{")


# ── The API process (no database) ────────────────────────────────────────────────────────


@asynccontextmanager
async def api(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def drain_queue(settings: Settings) -> None:
    worker = build_worker(settings, burst=True)
    try:
        await worker.async_run()
    finally:
        await worker.close()
    assert worker.jobs_failed == 0


async def test_readiness_without_database_is_503(redis_settings: Settings) -> None:
    async with api(redis_settings) as client:
        r = await client.get("/health/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready", "redis": "ok", "database": "not_configured"}


async def test_readiness_with_unreachable_database_is_503(redis_settings: Settings) -> None:
    # Port 1 on 127.0.0.1: refused at once; the pool gives up after db_pool_timeout_s.
    settings = make_settings(
        redis_url=str(redis_settings.redis_url),
        database_url="postgresql://u:p@127.0.0.1:1/db",
        db_pool_timeout_s=1.0,
    )
    async with api(settings) as client:
        r = await client.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["database"] == "error"
    assert r.json()["redis"] == "ok"


def test_step_status_values_are_the_wire_values() -> None:
    assert [s.value for s in StepStatus] == ["started", "completed", "skipped", "failed"]
    assert StepEvent.model_fields["status"].annotation is StepStatus


# ── run_id on ARQ's own log lines (Rules.md P12) ─────────────────────────────────────────────


async def test_arq_log_lines_carry_the_run_id(redis_settings: Settings) -> None:
    run_id = uuid4()
    job = FakeRunJob(run_id=run_id, request=FakeRunRequest(org_id=uuid4(), step_delay_ms=0))
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    captured = io.StringIO()
    pool = await create_arq_pool(redis_settings)
    try:
        configure_logging("INFO", stream=captured)
        await pool.enqueue_job(
            RUN_FAKE_PIPELINE, job.model_dump(mode="json"), _job_id=fake_run_job_id(run_id)
        )
        await pool.enqueue_job(PING, {"message": "no run"})
        await drain_queue(redis_settings)
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        await pool.aclose(close_connection_pool=True)

    lines = [json.loads(line) for line in captured.getvalue().splitlines()]
    arq_lines = [e for e in lines if e["logger"] == "arq.worker"]
    fake = [e for e in arq_lines if fake_run_job_id(run_id) in e["msg"]]
    pings = [e for e in arq_lines if ":ping(" in e["msg"] or ":ping " in e["msg"]]
    # ARQ's start (→) and finish (←) lines for the run's job carry its run_id ...
    assert any("→" in e["msg"] for e in fake) and any("←" in e["msg"] for e in fake)
    assert {e["run_id"] for e in fake} == {str(run_id)}
    # ... as do the job's own lines, and a job that belongs to no run gets none (no leakage).
    assert {e["run_id"] for e in lines if e["logger"] == "app.workers.fake_run"} == {str(run_id)}
    assert pings and {e["run_id"] for e in pings} == {None}
