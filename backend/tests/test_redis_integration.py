"""End to end against a real Redis: the event log, the ARQ worker, and the SSE stream.

Each piece runs for real — Redis Streams, an ARQ worker consuming the queue, FastAPI serving SSE —
with only the pipeline itself faked (Phases.md Phase 1).
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from app.api.main import create_app
from app.core.config import Settings
from app.core.pipeline import PIPELINE_STEPS
from app.core.redis import create_arq_pool
from app.core.run_events import RunEventLog, stream_key
from app.errors import RunEventCorrupt
from app.models.jobs import PingJob, PingResult
from app.models.runs import RunCompleted, RunQueued, RunStarted, StepEvent, StepStatus
from app.workers.jobs import PING
from app.workers.main import build_worker
from tests.conftest import make_settings
from tests.sse import SseMessage, parse_stream

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
            await log.append(RunQueued(run_id=run_id, mode="fake")),
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
        await log.append(RunQueued(run_id=run_id, mode="fake"))
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


# ── The fake run through the API ────────────────────────────────────────────────────────────


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


async def start_fake_run(client: httpx.AsyncClient, **body: object) -> UUID:
    r = await client.post("/api/v1/runs/fake", json={"step_delay_ms": 0, **body})
    assert r.status_code == 202, r.text
    accepted = r.json()
    assert accepted["stream_url"] == f"/api/v1/runs/{accepted['run_id']}/stream"
    return UUID(accepted["run_id"])


async def read_stream(
    client: httpx.AsyncClient, run_id: UUID, last_event_id: str | None = None
) -> tuple[httpx.Response, list[SseMessage]]:
    headers = {"Last-Event-ID": last_event_id} if last_event_id else {}
    r = await client.get(f"/api/v1/runs/{run_id}/stream", headers=headers)
    return r, parse_stream(r.text)


async def test_fake_run_streams_all_fifteen_steps(redis_settings: Settings) -> None:
    async with api(redis_settings) as client:
        run_id = await start_fake_run(client)
        await drain_queue(redis_settings)
        r, messages = await read_stream(client, run_id)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert [m.event for m in messages[:2]] == ["run.queued", "run.started"]
    assert messages[-1].event == "run.completed"
    steps = [
        (m.data["step"], m.data["name"], m.data["status"]) for m in messages if m.event == "step"
    ]
    assert steps == [
        (s.number, s.name, status) for s in PIPELINE_STEPS for status in ("started", "completed")
    ]
    # Every message is a valid, named, identified event for this run.
    assert all(m.event == m.data["type"] and m.data["run_id"] == str(run_id) for m in messages)
    ids = [tuple(map(int, m.id.split("-"))) for m in messages if m.id]
    assert len(ids) == len(messages) == 33
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert messages[0].retry == 2000


async def test_simulated_failure_streams_run_failed(redis_settings: Settings) -> None:
    async with api(redis_settings) as client:
        run_id = await start_fake_run(client, fail_at_step=3)
        await drain_queue(redis_settings)
        _, messages = await read_stream(client, run_id)
    assert [m.data.get("status") for m in messages[-2:]] == ["failed", None]
    assert messages[-1].event == "run.failed"
    assert messages[-1].data["step"] == 3


async def test_reconnect_resumes_after_last_event_id(redis_settings: Settings) -> None:
    async with api(redis_settings) as client:
        run_id = await start_fake_run(client, depth="D0")
        await drain_queue(redis_settings)
        _, full = await read_stream(client, run_id)
        _, resumed = await read_stream(client, run_id, last_event_id=full[9].id)
    # Same events, same IDs; `retry:` rides on the first message of every connection.
    assert [(m.event, m.id, m.data) for m in resumed] == [
        (m.event, m.id, m.data) for m in full[10:]
    ]
    assert resumed[0].retry == 2000


async def test_reconnect_after_final_event_gets_204(redis_settings: Settings) -> None:
    async with api(redis_settings) as client:
        run_id = await start_fake_run(client)
        await drain_queue(redis_settings)
        _, full = await read_stream(client, run_id)
        r, _ = await read_stream(client, run_id, last_event_id=full[-1].id)
    assert r.status_code == 204
    assert r.content == b""


async def test_malformed_last_event_id_replays_from_start(redis_settings: Settings) -> None:
    async with api(redis_settings) as client:
        run_id = await start_fake_run(client)
        await drain_queue(redis_settings)
        _, full = await read_stream(client, run_id)
        _, replay = await read_stream(client, run_id, last_event_id="not-an-id")
    assert [(m.event, m.id, m.data) for m in replay] == [(m.event, m.id, m.data) for m in full]


async def test_unknown_run_is_404(redis_settings: Settings) -> None:
    async with api(redis_settings) as client:
        r = await client.get(f"/api/v1/runs/{uuid4()}/stream")
    assert r.status_code == 404


async def test_invalid_fake_run_request_is_422(redis_settings: Settings) -> None:
    async with api(redis_settings) as client:
        r = await client.post("/api/v1/runs/fake", json={"fail_at_step": 16})
    assert r.status_code == 422


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


# ── Live, over the wire ─────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def live_server(app: FastAPI) -> AsyncIterator[str]:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, lifespan="on")
    )
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            # uvicorn exposes only a flag, no event, for "started".
            while not server.started:  # noqa: ASYNC110
                await asyncio.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


async def test_events_arrive_live_while_the_run_progresses(redis_settings: Settings) -> None:
    """A real HTTP client sees each step as it happens, not one buffered body at the end."""
    delay_ms = 150
    worker = build_worker(redis_settings)
    worker_task = asyncio.create_task(worker.async_run())
    try:
        async with (
            live_server(create_app(redis_settings)) as base,
            httpx.AsyncClient(base_url=base, timeout=30) as client,
        ):
            r = await client.post("/api/v1/runs/fake", json={"step_delay_ms": delay_ms})
            run_id = r.json()["run_id"]
            arrivals: list[tuple[float, SseMessage]] = []
            async with client.stream("GET", f"/api/v1/runs/{run_id}/stream") as resp:
                assert resp.status_code == 200
                buffer = ""
                async for chunk in resp.aiter_text():
                    buffer += chunk
                    *blocks, buffer = buffer.replace("\r\n", "\n").split("\n\n")
                    now = time.monotonic()
                    arrivals += [(now, m) for m in parse_stream("\n\n".join(blocks))]
    finally:
        worker.handle_sig(2)
        await worker.close()
        worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)

    events = [m for _, m in arrivals]
    assert events[-1].event == "run.completed"
    assert sum(1 for m in events if m.event == "step") == 30
    first_step = next(t for t, m in arrivals if m.event == "step")
    completed = arrivals[-1][0]
    # 15 steps x 150 ms: if the body were buffered, every event would arrive at the same moment.
    assert completed - first_step >= 15 * delay_ms / 1000 * 0.8
    distinct_arrival_times = {round(t, 2) for t, m in arrivals if m.event == "step"}
    assert len(distinct_arrival_times) >= 10


def test_step_status_values_are_the_wire_values() -> None:
    assert [s.value for s in StepStatus] == ["started", "completed", "skipped", "failed"]
    assert StepEvent.model_fields["status"].annotation is StepStatus
