"""A worker that dies mid-run leaves a run that something now continues (Phases.md chunk F).

`Pipeline.recover` always worked from a checkpoint (test_graph_checkpoints.py kills a real
subprocess), but nothing called it: the run stayed `queued` for ever. The sweep finds those runs
and the job continues them. Here the death is a cancelled agent (nothing is written, as when a
process is killed outright), which leaves exactly the state a kill leaves: checkpoints, no ending.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.orchestrator.checkpoint import open_checkpointer
from app.workers.jobs import recover_job_id, recover_pipeline, run_id_from_job_id
from app.workers.recovery import orphaned_runs
from tests.db.conftest import Seed
from tests.db.graph_support import ListSink, build_pipeline
from tests.db.test_graph_checkpoints import new_run, run_row

pytestmark = [pytest.mark.anyio, pytest.mark.db]


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


async def test_a_run_a_dead_worker_left_is_found_and_continued_to_its_ending(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed, tmp_path: Path
) -> None:
    user = seed.users["editor_a"]
    dead = new_run(db_url, seed)
    never_started = new_run(db_url, seed)  # queued, but no worker ever touched it: no checkpoint
    parked = new_run(db_url, seed)
    async with open_checkpointer(db_url) as saver:
        # The worker dies in the agent fan-out: A3 hangs until the task is cancelled from outside
        # (a killed process writes nothing either); the checkpoint is the record.
        ready = tmp_path / "ready"
        killed = build_pipeline(pool, saver, ListSink(), log=tmp_path / "log", hang=ready)
        task = asyncio.create_task(
            killed.start(run_id=dead, org_id=seed.org_a, user_id=user, product_id=seed.product_a)
        )
        async with asyncio.timeout(60):
            while not ready.exists():  # noqa: ASYNC110 - polls a file the hanging agent writes
                await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # A run parked for its approver is waiting for a person, not for a worker.
        healthy = build_pipeline(pool, saver, ListSink())
        await healthy.start(
            run_id=parked, org_id=seed.org_a, user_id=user, product_id=seed.product_a
        )

        assert run_row(db_url, dead)[0] == "queued"
        found = await orphaned_runs(pool)
        assert dead in found
        assert never_started not in found and parked not in found

        # The next worker starts, finds it, and the job continues it from the checkpoint.
        outcome = await recover_pipeline({"pipeline": healthy}, {"run_id": str(dead)})
        assert outcome == {"run_id": str(dead), "status": "suspended"}
    assert run_row(db_url, dead)[0] == "awaiting_approval"
    assert dead not in await orphaned_runs(pool)  # continued: no longer an orphan


async def test_recovering_a_run_that_cannot_be_recovered_says_so_instead_of_failing(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    run_id = new_run(db_url, seed)  # no checkpoint
    async with open_checkpointer(db_url) as saver:
        pipeline = build_pipeline(pool, saver, ListSink())
        result = await recover_pipeline({"pipeline": pipeline}, {"run_id": str(run_id)})
    assert result["status"] == "not_recoverable"
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute("select status from runs where id = %s", (run_id,)).fetchone()
    assert row == ("queued",)  # untouched


def test_a_recovery_job_id_carries_its_run_id_and_is_fixed_per_worker_start() -> None:
    run_id = uuid4()
    job_id = recover_job_id(run_id, "abc123")
    assert run_id_from_job_id(job_id) == run_id  # the worker binds run_id before logging
    assert recover_job_id(run_id, "abc123") == job_id  # a sweep seeing it twice queues it once
