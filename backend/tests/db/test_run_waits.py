"""The wait for a person is a recorded interval, so latency can be measured without it
(migration 0036; PRD N1/N2; app/evaluation/latency.py).

"You cannot subtract a wait you did not time": the park and the decision's arrival used to
exist only as timestamps inside the checkpointer's private tables. Now each wait is a row,
written by the backend when the run parks and closed when the decision arrives or the run
ends, and `runs.latency_ms` is the active time, derived in the statement that ends the run.
"""

import asyncio
from collections.abc import AsyncIterator

import psycopg
import pytest
from psycopg import AsyncConnection, errors
from psycopg_pool import AsyncConnectionPool

from app.core.budgeted_run import end_run, suspend_run
from app.evaluation.latency import run_latencies
from app.orchestrator.checkpoint import open_checkpointer
from tests.db.conftest import Seed, Tx
from tests.db.graph_support import ListSink, build_pipeline
from tests.db.test_graph_checkpoints import new_run

pytestmark = pytest.mark.db


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


def waits(db_url: str, run_id: object) -> list[tuple[bool, float | None]]:
    """[(closed, seconds waited)] for the run's waits, oldest first."""
    with psycopg.connect(db_url, autocommit=True) as c:
        rows = c.execute(
            "select resumed_at is not null, extract(epoch from (resumed_at - parked_at))::float8 "
            "from run_waits where run_id = %s order by id",
            (run_id,),
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


@pytest.mark.anyio
async def test_each_wait_is_recorded_and_latency_is_the_time_without_them(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    run_id = new_run(db_url, seed)
    user, approver = seed.users["editor_a"], seed.users["approver_a"]
    async with open_checkpointer(db_url) as saver:
        pipeline = build_pipeline(pool, saver, ListSink())
        await pipeline.start(
            run_id=run_id, org_id=seed.org_a, user_id=user, product_id=seed.product_a
        )
        assert waits(db_url, run_id) == [(False, None)]  # parked: the wait is open
        await asyncio.sleep(0.6)
        await pipeline.resume(run_id, "revise", by=approver, reason="tone")
        first = waits(db_url, run_id)
        assert first[0][0] is True and first[0][1] is not None and first[0][1] >= 0.6
        assert first[1:] == [(False, None)]  # the regenerated draft parked it again
        await asyncio.sleep(0.6)
        await pipeline.resume(run_id, "approve", by=approver)
    both = waits(db_url, run_id)
    assert len(both) == 2 and all(closed for closed, _ in both)
    waited = sum(s or 0 for _, s in both)
    assert waited >= 1.2

    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        (lat,) = [r for r in await run_latencies(conn, [seed.org_a]) if r.run_id == run_id]
    assert lat.waits == 2 and lat.open_waits == 0 and lat.waited_ms >= 1200
    assert lat.recorded_ms is not None
    assert abs(lat.recorded_ms - lat.active_ms) <= 2, "the column and the raw intervals agree"
    assert lat.wall_ms - lat.recorded_ms >= 1200, "the wait is not in the latency"
    assert lat.recorded_ms < 5000  # the work itself took well under the 1.2 s of waiting x4


@pytest.mark.anyio
async def test_suspending_twice_opens_one_wait_and_ending_closes_an_open_one(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    run_id = new_run(db_url, seed)
    await suspend_run(pool, run_id)
    await suspend_run(pool, run_id)  # idempotent while parked
    assert waits(db_url, run_id) == [(False, None)]
    await end_run(pool, run_id, "failed", budget_exhausted=False)  # ended while parked
    assert [closed for closed, _ in waits(db_url, run_id)] == [True]
    with pytest.raises(RuntimeError, match="has ended"):
        await suspend_run(pool, run_id)


def test_a_run_has_at_most_one_open_wait(tx: Tx) -> None:
    tx.execute(
        "insert into run_waits (org_id, run_id) values (%s, %s)", (tx.seed.org_a, tx.seed.run_a)
    )
    with pytest.raises(errors.UniqueViolation), tx.conn.transaction():
        tx.execute(
            "insert into run_waits (org_id, run_id) values (%s, %s)",
            (tx.seed.org_a, tx.seed.run_a),
        )


def test_members_read_their_waits_and_no_api_role_writes_them(tx: Tx) -> None:
    tx.execute(
        "insert into run_waits (org_id, run_id) values (%s, %s)", (tx.seed.org_a, tx.seed.run_a)
    )
    tx.as_user("viewer_a")
    assert tx.execute("select count(*) from run_waits where run_id = %s", (tx.seed.run_a,)) == [
        (1,)
    ]
    for statement in (
        "insert into run_waits (org_id, run_id) values (%s, %s)",
        "update run_waits set resumed_at = now() where run_id = %s",
        "delete from run_waits where run_id = %s",
    ):
        params = (
            (tx.seed.org_a, tx.seed.run_a) if statement.startswith("insert") else (tx.seed.run_a,)
        )
        with pytest.raises(errors.InsufficientPrivilege), tx.conn.transaction():
            tx.execute(statement, params)


def test_another_organisations_member_reads_none_of_them(tx: Tx) -> None:
    tx.execute(
        "insert into run_waits (org_id, run_id) values (%s, %s)", (tx.seed.org_a, tx.seed.run_a)
    )
    tx.as_user("approver_b")
    assert tx.execute("select count(*) from run_waits") == [(0,)]
