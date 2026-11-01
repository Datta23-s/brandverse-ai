"""A crash cannot inflate the headline metric: spend versus work (owner, 1 November 2026; 0045).

ARR is a count of calls. The killed-worker run on real models showed `runs.llm_call_count` at 12
against 10 completed calls: the dead worker's two in-flight calls stayed `pending` for ever, and A3
and A2 ran again after the recovery. Both are real SPEND (the provider may have been asked) and
neither is WORK. Here a worker is killed with a call in flight, the next worker's sweep orphans it,
the run is recovered, and both tallies are asserted: exact, and adding up.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.evaluation.llm_attempts import llm_calls_by_attempt, totals
from app.orchestrator.checkpoint import open_checkpointer
from app.workers.jobs import recover_pipeline
from app.workers.recovery import orphan_pending_calls
from tests.db.conftest import Seed
from tests.db.graph_support import ListSink, build_pipeline
from tests.db.test_graph_checkpoints import new_run

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


def outcomes(db_url: str, run_id: object) -> dict[str, str]:
    with psycopg.connect(db_url, autocommit=True) as c:
        rows = c.execute(
            "select agent_id, outcome from llm_calls where run_id = %s order by id", (run_id,)
        ).fetchall()
    return {f"{agent}#{i}": outcome for i, (agent, outcome) in enumerate(rows)}


async def test_a_killed_worker_leaves_spend_and_not_work(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed, tmp_path: Path
) -> None:
    user = seed.users["editor_a"]
    run_id = new_run(db_url, seed)
    async with open_checkpointer(db_url) as saver:
        # Worker 1 dies with A3's call in flight: A1 and A2 have finished, A3 is `pending`.
        ready = tmp_path / "ready"
        first = build_pipeline(
            pool, saver, ListSink(), log=tmp_path / "log", hang=ready, calls=True
        )
        task = asyncio.create_task(
            first.start(run_id=run_id, org_id=seed.org_a, user_id=user, product_id=seed.product_a)
        )
        async with asyncio.timeout(60):
            while not ready.exists():  # noqa: ASYNC110 - polls a file the hanging agent writes
                await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sorted(outcomes(db_url, run_id).values()) == ["ok", "ok", "pending"]

        # Worker 2 starts: its sweep ends the dead call's `pending` state; recovery re-runs A3.
        assert await orphan_pending_calls(pool) >= 1
        assert sorted(outcomes(db_url, run_id).values()) == ["ok", "ok", "orphaned"]
        second = build_pipeline(pool, saver, ListSink(), calls=True)
        result = await recover_pipeline({"pipeline": second}, {"run_id": str(run_id)})
        assert result["status"] == "suspended"

    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        rows = [r for r in await llm_calls_by_attempt(conn, [seed.org_a]) if r.run_id == run_id]
    t = totals(rows)
    # Four rows were made: A1, A2, A3 (killed), A3 (re-run). Spend is all four; WORK is three.
    assert t.spend_calls == 4
    assert t.work_calls == 3, "the orphaned call and the re-executed attempt are not work"
    assert t.orphaned_calls == 1
    assert t.resume_duplicate_calls == 0  # the earlier A3 attempt is the ORPHAN, counted once
    assert t.spend_calls == t.work_calls + t.resume_duplicate_calls + t.orphaned_calls
    assert sorted(r.outcome for r in rows) == ["ok", "ok", "ok", "orphaned"]


async def test_an_orphaned_call_is_terminal_and_still_counts_toward_quota_and_the_run_cap(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    from app.llm.ledger import CallContext, CallLedger
    from app.llm.prompts import PromptRef
    from app.llm.registry import Tier
    from tests.llm_fakes import make_registry

    run_id = new_run(db_url, seed)
    ctx = CallContext(org_id=seed.org_a, run_id=run_id, agent_id="test")
    ledger = CallLedger(
        pool,
        make_registry(),
        max_calls_per_run=3,
        max_cost_per_run_usd=100.0,
        pending_expiry_s=900,
    )
    prompt = PromptRef("test_judge", "v1")
    for _ in range(3):
        await ledger.admit(ctx, Tier.STANDARD, prompt, "test", 0.0001)  # three pending reservations
    await orphan_pending_calls(pool)
    # They are orphaned, not erased: the run is still at its cap of three calls.
    from app.errors import BudgetExceeded

    with pytest.raises(BudgetExceeded):
        await ledger.admit(ctx, Tier.STANDARD, prompt, "test", 0.0001)
    # And a late `finish` on an orphaned row is refused: it is terminal.
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "select id from llm_calls where run_id = %s and outcome = 'orphaned' limit 1", (run_id,)
        ).fetchone()
    assert row is not None
    with pytest.raises(RuntimeError, match="was not pending"):
        await ledger.finish(
            row[0],
            outcome="ok",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
            latency_ms=1,
            rate_limit_wait_ms=0,
        )
