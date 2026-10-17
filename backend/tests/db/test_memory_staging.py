"""Memory write-back is staged and committed only at termination, for a qualifying attempt
(app/core/budgeted_run.py; docs/evaluation-plan.md §2, the attempt rule).

memory_items rows can never be deleted (0010, 0011), so a write that should not have happened
cannot be undone. Each test stages a memory write in an early step, then ends the attempt: the
four infrastructure failures each leave memory_items unchanged — a real process kill included —
and only a completed run commits, in the same transaction as its terminal status.
"""

import asyncio
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from app.core.budgeted_run import MemoryStage, MemoryWrite, Step, run_steps
from app.core.infrastructure import classify_infrastructure_failure
from app.errors import BudgetExceeded, LLMCallFailed, LLMRateLimited, QuotaExhausted
from tests.db.conftest import Seed

pytestmark = [pytest.mark.anyio, pytest.mark.db]

BACKEND = Path(__file__).resolve().parents[2]


class Out(BaseModel):
    step: str


@pytest.fixture
async def pool(db_url: str) -> AsyncIterator[AsyncConnectionPool[AsyncConnection]]:
    p: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        db_url, min_size=0, max_size=4, open=False
    )
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _run(db_url: str, seed: Seed) -> UUID:
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "insert into runs (org_id, product_id, request) values (%s, %s, '{}') returning id",
            (seed.org_a, seed.product_a),
        ).fetchone()
    assert row is not None
    run_id: UUID = row[0]
    return run_id


def _memory(db_url: str, marker: str) -> int:
    with psycopg.connect(db_url) as c:
        row = c.execute(
            "select count(*) from memory_items where provenance = %s", (marker,)
        ).fetchone()
    assert row is not None
    return int(row[0])


def _status(db_url: str, run_id: UUID) -> str:
    with psycopg.connect(db_url) as c:
        row = c.execute("select status from runs where id = %s", (run_id,)).fetchone()
    assert row is not None
    return str(row[0])


def _steps(
    seed: Seed, marker: str, then: Callable[[], Awaitable[BaseModel]], *, brand: UUID | None = None
) -> tuple[list[Step], MemoryStage]:
    stage = MemoryStage()

    async def stage_memory(_: Mapping[str, BaseModel]) -> BaseModel:
        stage.stage(
            MemoryWrite(
                org_id=seed.org_a,
                brand_id=brand or seed.brand_a,
                kind="insight",
                content="founder-story copy outperforms spec-sheet copy",
                provenance=marker,
            )
        )
        return Out(step="stage_memory")

    async def next_step(_: Mapping[str, BaseModel]) -> BaseModel:
        return await then()

    return [Step("stage_memory", stage_memory), Step("next", next_step)], stage


def _raise(exc: BaseException) -> Callable[[], Awaitable[BaseModel]]:
    async def fail() -> BaseModel:
        raise exc

    return fail


def _network_error() -> BaseException:
    try:
        raise httpx.ConnectError("connection refused")
    except httpx.ConnectError as cause:
        error = LLMCallFailed("gemini: ConnectError: connection refused")
        error.__cause__ = cause
        return error


IN_PROCESS_FAILURES = [
    ("rate_limited", LLMRateLimited("still rate-limited after 3 waits")),
    ("quota_exhausted", QuotaExhausted("daily quota used up")),
    ("network_error", _network_error()),
]


@pytest.mark.parametrize(
    ("kind", "exc"), IN_PROCESS_FAILURES, ids=[k for k, _ in IN_PROCESS_FAILURES]
)
async def test_an_infrastructure_failure_mid_run_leaves_memory_unchanged(
    pool: AsyncConnectionPool[AsyncConnection],
    db_url: str,
    seed: Seed,
    kind: str,
    exc: BaseException,
) -> None:
    assert classify_infrastructure_failure(exc) == kind
    marker, run_id = f"test-{uuid4()}", _run(db_url, seed)
    steps, stage = _steps(seed, marker, _raise(exc))
    with pytest.raises(type(exc)):
        await run_steps(steps, pool=pool, run_id=run_id, memory=stage)
    assert len(stage.items) == 1  # it was staged ...
    assert _memory(db_url, marker) == 0  # ... and never reached memory_items
    assert _status(db_url, run_id) == "failed"


async def test_a_cancelled_attempt_leaves_memory_unchanged(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    marker, run_id = f"test-{uuid4()}", _run(db_url, seed)
    started = asyncio.Event()

    async def hang() -> BaseModel:
        started.set()
        await asyncio.sleep(3600)
        return Out(step="never")

    steps, stage = _steps(seed, marker, hang)
    task = asyncio.create_task(run_steps(steps, pool=pool, run_id=run_id, memory=stage))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await task
    assert classify_infrastructure_failure(cancelled.value) == "process_crash"
    assert _memory(db_url, marker) == 0
    assert _status(db_url, run_id) == "failed"


def test_a_killed_process_mid_run_leaves_memory_unchanged(
    db_url: str, seed: Seed, tmp_path: Path
) -> None:
    """process_crash, for real: the worker process is killed (SIGKILL / TerminateProcess) after
    staging, so no cleanup code of any kind runs. The run never terminates; nothing persisted."""
    marker, run_id = f"test-{uuid4()}", _run(db_url, seed)
    ready = tmp_path / "ready"
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, this interpreter
        [
            sys.executable,
            "-m",
            "tests.db.crash_worker",
            db_url,
            str(run_id),
            str(seed.org_a),
            str(seed.brand_a),
            marker,
            str(ready),
        ],
        cwd=BACKEND,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 60
        while not ready.exists():
            assert proc.poll() is None, proc.stdout.read().decode() if proc.stdout else ""
            assert time.monotonic() < deadline, "worker did not stage within 60 s"
            time.sleep(0.05)
        proc.kill()
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert _memory(db_url, marker) == 0
    assert _status(db_url, run_id) == "queued"  # never terminated: Phase 10 classifies it a crash


# ── Controls: what does commit, and what does not ────────────────────────────────────────────


async def test_a_completed_run_commits_its_staged_memory_with_its_status(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    marker, run_id = f"test-{uuid4()}", _run(db_url, seed)

    async def done() -> BaseModel:
        return Out(step="done")

    steps, stage = _steps(seed, marker, done)
    outcome = await run_steps(steps, pool=pool, run_id=run_id, memory=stage)
    assert outcome.status == "completed"
    assert _memory(db_url, marker) == 1
    assert _status(db_url, run_id) == "completed"


async def test_a_budget_stopped_run_is_a_result_but_commits_no_memory(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    marker, run_id = f"test-{uuid4()}", _run(db_url, seed)
    steps, stage = _steps(seed, marker, _raise(BudgetExceeded("call cap")))
    outcome = await run_steps(steps, pool=pool, run_id=run_id, memory=stage)
    assert outcome.status == "partial"
    assert classify_infrastructure_failure(BudgetExceeded("call cap")) is None
    assert _memory(db_url, marker) == 0


async def test_any_other_failure_is_a_result_and_commits_no_memory(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    marker, run_id = f"test-{uuid4()}", _run(db_url, seed)
    steps, stage = _steps(seed, marker, _raise(ValueError("a bug")))
    with pytest.raises(ValueError):
        await run_steps(steps, pool=pool, run_id=run_id, memory=stage)
    assert classify_infrastructure_failure(ValueError("a bug")) is None
    assert _memory(db_url, marker) == 0
    assert _status(db_url, run_id) == "failed"


async def test_status_and_memory_land_together_or_not_at_all(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    # A staged row that cannot be inserted (another org's brand: the composite FK refuses it)
    # rolls back the 'completed' status with it; the run then ends 'failed', with no memory.
    marker, run_id = f"test-{uuid4()}", _run(db_url, seed)

    async def done() -> BaseModel:
        return Out(step="done")

    steps, stage = _steps(seed, marker, done, brand=seed.brand_b)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await run_steps(steps, pool=pool, run_id=run_id, memory=stage)
    assert _memory(db_url, marker) == 0
    assert _status(db_url, run_id) == "failed"
