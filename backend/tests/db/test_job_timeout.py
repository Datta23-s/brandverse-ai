"""A job that runs out of time ends its run `failed`, and says why and where (migration 0046).

Found in the stage-2 interim (2026-11-04): ARQ killed a run's job at its 600 s limit and the run
stayed `queued` with no error anywhere, indistinguishable, in a 90-campaign batch, from a run that
has not started. These tests drive a REAL job into its deadline against the real ledger, the way
test_orphan_accounting.py drives the real accounting, and assert the run's terminal state.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.errors import JobTimedOut
from app.models.runs import RunFailed
from app.orchestrator.checkpoint import open_checkpointer
from app.workers.jobs import run_pipeline
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


def _payload(seed: Seed, run_id: Any) -> dict[str, Any]:
    return {
        "run_id": str(run_id),
        "request": {
            "org_id": str(seed.org_a),
            "product_id": str(seed.product_a),
            "depth": "D1",
        },
        "by": str(seed.users["editor_a"]),
    }


def _failure(db_url: str, run_id: Any) -> dict[str, Any] | None:
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute("select failure from runs where id = %s", (run_id,)).fetchone()
    assert row is not None
    failure: dict[str, Any] | None = row[0]
    return failure


async def test_a_job_that_runs_out_of_time_ends_its_run_failed_naming_the_step(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed, tmp_path: Path
) -> None:
    run_id = new_run(db_url, seed)
    sink = ListSink()
    # worker_job_timeout_s=5 -> the job's own deadline is 4.75 s, just inside ARQ's 5 s.
    ctx_settings = Settings(_env_file=None, worker_job_timeout_s=5)  # type: ignore[call-arg]
    async with open_checkpointer(db_url) as saver:
        pipeline = build_pipeline(pool, saver, sink, log=tmp_path / "log", hang=tmp_path / "ready")
        with pytest.raises(JobTimedOut, match=r"step 7 run_agents"):
            await run_pipeline(
                {"pipeline": pipeline, "settings": ctx_settings}, _payload(seed, run_id)
            )
        await asyncio.sleep(1.5)  # as below: let the cancelled agent tasks finish unwinding

    # The terminal state: failed, with an ending time, no longer an orphan, and the reason.
    assert run_row(db_url, run_id) == ("failed", True)
    assert run_id not in await orphaned_runs(pool)
    failure = _failure(db_url, run_id)
    assert failure is not None
    assert failure["kind"] == "job_timeout"
    assert (failure["step"], failure["step_name"]) == (7, "run_agents")
    assert failure["after_s"] == 5  # round(4.75)
    assert failure["infrastructure_failure"] == "process_crash"  # the 2026-10-17 attempt rule
    assert "step 7 run_agents" in failure["message"]
    # And on the run's stream, so a watching client is not left waiting.
    last = sink.events[-1]
    assert isinstance(last, RunFailed) and last.step == 7 and "run_agents" in last.error


async def test_a_worker_shutdown_is_not_a_timeout_and_the_run_stays_resumable(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed, tmp_path: Path
) -> None:
    run_id = new_run(db_url, seed)
    ready = tmp_path / "ready"
    ctx_settings = Settings(_env_file=None, worker_job_timeout_s=600)  # type: ignore[call-arg]
    async with open_checkpointer(db_url) as saver:
        pipeline = build_pipeline(pool, saver, ListSink(), log=tmp_path / "log", hang=ready)
        task = asyncio.create_task(
            run_pipeline({"pipeline": pipeline, "settings": ctx_settings}, _payload(seed, run_id))
        )
        async with asyncio.timeout(60):
            while not ready.exists():  # noqa: ASYNC110 - polls a file the hanging agent writes
                await asyncio.sleep(0.1)
        task.cancel()  # what ARQ does at shutdown: a cancellation that is not our deadline
        with pytest.raises(asyncio.CancelledError):
            await task
        # The pipeline's own agent tasks are cancelled with it and are still unwinding their
        # database I/O. Let them finish before the pool and the loop close, or the test runner's
        # teardown meets a closed socket (Windows: WinError 10038), intermittently.
        await asyncio.sleep(1.5)
    assert run_row(db_url, run_id)[0] == "queued"  # nothing written: the checkpoint is the record
    assert _failure(db_url, run_id) is None
    assert run_id in await orphaned_runs(pool)  # the sweep will continue it


def test_an_api_role_cannot_write_failure(db_url: str, seed: Seed) -> None:
    with psycopg.connect(db_url, autocommit=True) as c, c.transaction():
        c.execute(
            "select set_config('role', 'authenticated', true), "
            "set_config('request.jwt.claims', %s, true)",
            (json.dumps({"sub": str(seed.users["editor_a"]), "role": "authenticated"}),),
        )
        row = c.execute(
            "insert into runs (org_id, product_id, request, failure) "
            "values (%s, %s, '{}', '{\"kind\": \"job_timeout\"}') returning failure",
            (seed.org_a, seed.product_a),
        ).fetchone()
    assert row is not None and row[0] is None  # forced null by the insert trigger (0046)


def test_failure_must_be_an_object(db_url: str, seed: Seed) -> None:
    with (
        psycopg.connect(db_url, autocommit=True) as c,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        c.execute(
            "insert into runs (org_id, product_id, request, failure) values (%s, %s, '{}', '[1]')",
            (seed.org_a, seed.product_a),
        )
