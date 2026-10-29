"""A step run again after its worker died (the checkpointer is at-least-once): what that does to the
log and to the run record, and that the evaluation can tell it from everything else.

docs/evaluation-plan.md, "Re-executed steps"; migration 0035. The questions, each answered here:
does a re-executed step double count (yes, in every row-derived total); can the evaluation tell a
re-execution from a revision round or the gateway's repair call (yes, by (step, revision_round,
execution_id)); does the run record tell a run that declared a gap from a whole one (yes); and what
else a re-executed node does outside the process (audit rows: one per run).
"""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import AsyncConnection, errors
from psycopg_pool import AsyncConnectionPool

from app.core.budgeted_run import RunFacts, end_run
from app.errors import BudgetExceeded
from app.evaluation.llm_attempts import LLM_CALLS_BY_ATTEMPT_SQL, CallRow, totals
from app.evaluation.run_outcomes import run_outcomes_by_arm
from app.memory.writeback import Refusal, record_refusals
from app.orchestrator.checkpoint import open_checkpointer
from app.orchestrator.runner import NotRecoverable, PoolRunLedger
from tests.db.conftest import Seed, Tx
from tests.db.graph_support import ListSink, build_pipeline
from tests.db.test_graph_checkpoints import new_run, run_row

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


# ── The log can tell a re-execution from a revision round and a repair call ──────────────────

T0 = datetime(2026, 10, 29, 9, 0, 0).astimezone()


def call(
    tx: Tx,
    agent: str | None,
    step: int | None,
    round_: int | None,
    execution: UUID | None,
    minute: int,
    *,
    outcome: str = "ok",
    cost: float | None = 0.001,
    prompt: str = "judge",
) -> None:
    tx.execute(
        "insert into llm_calls (org_id, run_id, agent_id, model, tier, route_reason, prompt_id, "
        "prompt_version, outcome, step, revision_round, execution_id, cost_usd, created_at) "
        "values (%s, %s, %s, 'm', 'cheap', 'test', %s, 'v1', %s, %s, %s, %s, %s, %s)",
        (
            tx.seed.org_a,
            tx.seed.run_a,
            agent,
            prompt,
            outcome,
            step,
            round_,
            execution,
            cost,
            T0 + timedelta(minutes=minute),
        ),
    )


def mine(tx: Tx) -> list[tuple[object, ...]]:
    """The attempt read for this test's own run: other tests commit llm_calls rows to the same
    organisation (the kill test does), and a transaction's rows must not be confused with them."""
    rows = tx.execute(LLM_CALLS_BY_ATTEMPT_SQL, {"org_ids": [tx.seed.org_a]})
    return [r for r in rows if r[1] == tx.seed.run_a]


def test_a_call_carries_all_of_its_execution_identity_or_none(tx: Tx) -> None:
    for step, round_, execution in ((7, 0, None), (None, 0, uuid4()), (7, None, uuid4())):
        # each refusal in its own savepoint: a failed statement aborts the transaction otherwise
        with (
            pytest.raises(errors.CheckViolation, match="llm_calls_execution_identity"),
            tx.conn.transaction(),
        ):
            call(tx, "A3", step, round_, execution, 0)
    call(tx, None, None, None, None, 0)  # outside the graph: all three null


def test_a_re_execution_is_told_from_a_revision_round_and_a_repair_call(tx: Tx) -> None:
    a3_first, a3_second, a1_first, a1_second = uuid4(), uuid4(), uuid4(), uuid4()
    call(tx, "A3", 7, 0, a3_first, 0, outcome="pending", cost=None)  # killed mid-call
    call(tx, "A3", 7, 0, a3_second, 5)  # the retry from the checkpoint
    call(tx, "A1", 7, 0, a1_first, 0)  # finished, but its result never reached the checkpoint
    call(tx, "A1", 7, 0, a1_second, 6)  # so it ran again, and was paid for twice
    for round_ in range(3):  # three drafts: three EXECUTIONS, each its own work
        call(tx, "A6", 10, round_, uuid4(), 10 + round_)
    repair = uuid4()
    call(tx, "A7", 11, 0, repair, 20)  # one execution, two rows: the gateway's repair call
    call(tx, "A7", 11, 0, repair, 20, prompt="structured_repair")
    call(tx, None, None, None, None, 30)  # outside the graph: its own work and its own spend

    rows = mine(tx)
    by = {}
    for r in rows:
        by.setdefault((r[4], r[2], r[3]), []).append((r[6], r[7], r[8]))
    # (agent, step, round) -> [(attempt, last_attempt, outcome)]
    assert sorted(by[("A3", 7, 0)]) == [(1, False, "pending"), (2, True, "ok")]
    assert sorted(by[("A1", 7, 0)]) == [(1, False, "ok"), (2, True, "ok")]
    for round_ in range(3):
        assert by[("A6", 10, round_)] == [(1, True, "ok")], "a revision round is not a duplicate"
    assert by[("A7", 11, 0)] == [(1, True, "ok"), (1, True, "ok")], "a repair is the same execution"
    assert by[(None, None, None)] == [(1, True, "ok")]


def test_spend_counts_every_row_and_work_counts_the_last_attempt(tx: Tx) -> None:
    a1_first, a1_second = uuid4(), uuid4()
    call(tx, "A1", 7, 0, a1_first, 0)
    call(tx, "A1", 7, 0, a1_second, 6)
    call(tx, "A6", 10, 0, uuid4(), 10)
    call(tx, "A3", 7, 0, uuid4(), 0, outcome="refused", cost=None)  # never reached the provider
    t = totals([CallRow(*r) for r in mine(tx)])
    assert (t.spend_calls, t.work_calls, t.resume_duplicate_calls) == (3, 2, 1)
    assert (t.spend_cost_usd, t.work_cost_usd, t.resume_duplicate_cost_usd) == (0.003, 0.002, 0.001)


def test_the_attempt_read_excludes_practice_and_test_organisations(tx: Tx) -> None:
    call(tx, "A1", 7, 0, uuid4(), 0)
    assert tx.execute(LLM_CALLS_BY_ATTEMPT_SQL, {"org_ids": [tx.seed.org_practice]}) == []
    assert len(mine(tx)) == 1


# ── The run record tells a run that declared a gap, and an API role cannot forge one ─────────


def test_an_api_role_cannot_set_a_runs_declared_gaps(tx: Tx) -> None:
    tx.as_user("editor_a")
    rows = tx.execute(
        "insert into runs (org_id, product_id, request, declared_gaps) "
        "values (%s, %s, '{}', '[{\"agent\": \"A2\"}]') returning declared_gaps",
        (tx.seed.org_a, tx.seed.product_a),
    )
    assert rows == [([],)]


def test_declared_gaps_must_be_a_list(tx: Tx) -> None:
    with pytest.raises(errors.CheckViolation, match="declared_gaps"):
        tx.execute("update runs set declared_gaps = '{}' where id = %s", (tx.seed.run_a,))


@pytest.mark.anyio
async def test_end_run_writes_the_run_facts_with_the_status_and_the_report_counts_gaps(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    whole, gappy = new_run(db_url, seed), new_run(db_url, seed)
    await end_run(pool, whole, "completed", budget_exhausted=False, facts=RunFacts(depth="D1"))
    await end_run(
        pool,
        gappy,
        "completed",
        budget_exhausted=False,
        facts=RunFacts(
            depth="D2",
            research_score=0.4,
            selected_agents=("A1", "A3"),
            memory_hit_count=2,
            search_count=8,
            declared_gaps=({"agent": "A2", "step": 7, "error": "RuntimeError"},),
        ),
    )
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "select status, depth, research_score, selected_agents, memory_hit_count, "
            "search_count, declared_gaps from runs where id = %s",
            (gappy,),
        ).fetchone()
        assert row == (
            "completed",
            "D2",
            Decimal("0.400"),
            ["A1", "A3"],
            2,
            8,
            [{"agent": "A2", "step": 7, "error": "RuntimeError"}],
        )
        assert c.execute("select declared_gaps from runs where id = %s", (whole,)).fetchone() == (
            [],
        )
        org = c.execute("select org_id from runs where id = %s", (gappy,)).fetchone()
    assert org is not None
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        report = {a.arm: a for a in await run_outcomes_by_arm(conn, [org[0]])}
    assert report["proposed"].with_gaps >= 1  # this run; other tests' runs share the organisation
    assert report["proposed"].by_status["completed"] >= 2  # and `completed` alone cannot tell


@pytest.mark.anyio
async def test_a_graph_run_records_its_gap_and_a_budget_stop_is_partial(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    gappy, stopped = new_run(db_url, seed), new_run(db_url, seed)
    async with open_checkpointer(db_url) as saver:
        pipeline = build_pipeline(pool, saver, ListSink(), failing={"A2": RuntimeError("down")})
        user = seed.users["editor_a"]
        await pipeline.start(
            run_id=gappy, org_id=seed.org_a, user_id=user, product_id=seed.product_a
        )
        await pipeline.resume(gappy, "approve", by=seed.users["approver_a"])

        halted = build_pipeline(pool, saver, ListSink(), failing={"A1": BudgetExceeded("cap")})
        out = await halted.start(
            run_id=stopped, org_id=seed.org_a, user_id=user, product_id=seed.product_a
        )
    assert out.status == "partial"
    with psycopg.connect(db_url, autocommit=True) as c:
        done = c.execute(
            "select status, budget_exhausted, declared_gaps, depth, selected_agents, "
            "completed_at is not null from runs where id = %s",
            (gappy,),
        ).fetchone()
        part = c.execute(
            "select status, budget_exhausted, depth, completed_at is not null "
            "from runs where id = %s",
            (stopped,),
        ).fetchone()
    assert done == (
        "completed",
        False,
        [{"agent": "A2", "step": 7, "error": "RuntimeError"}],
        "D1",
        ["A1", "A2", "A3", "A6", "A7"],
        True,
    )
    assert part == ("partial", True, "D1", True), "the run record tells a budget stop by itself"


# ── What else a re-executed node does outside the process ────────────────────────────────────


@pytest.mark.anyio
async def test_step_15_writes_its_refusal_audit_row_once_however_often_it_ran(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    run_id = new_run(db_url, seed)
    refused = [Refusal("fact", None, "text", ("too_long",), 500)]
    async with pool.connection() as conn:
        await record_refusals(conn, run_id, refused)
        await record_refusals(conn, run_id, refused)  # the node ran again after a crash
        with pytest.raises(RuntimeError, match="not found"):
            await record_refusals(conn, uuid4(), refused)  # a missing run is still an error
    with psycopg.connect(db_url, autocommit=True) as c:
        rows = c.execute(
            "select details from audit_log where entity_type = 'run' and entity_id = %s "
            "and action = 'memory.candidates_refused'",
            (str(run_id),),
        ).fetchall()
    assert len(rows) == 1 and json.loads(json.dumps(rows[0][0]))["refused"][0]["reasons"] == [
        "too_long"
    ]


@pytest.mark.anyio
async def test_a_finished_graph_whose_run_was_never_ended_is_ended_by_recover(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    run_id = new_run(db_url, seed)

    class DiesBeforeTheEnding(PoolRunLedger):
        async def end(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            raise asyncio.CancelledError  # the process is gone before the status is written

    async with open_checkpointer(db_url) as saver:
        pipeline = build_pipeline(pool, saver, ListSink())
        user = seed.users["editor_a"]
        await pipeline.start(
            run_id=run_id, org_id=seed.org_a, user_id=user, product_id=seed.product_a
        )
        pipeline._ledger = DiesBeforeTheEnding(pool)  # type: ignore[attr-defined]
        with pytest.raises(asyncio.CancelledError):
            await pipeline.resume(run_id, "approve", by=seed.users["approver_a"])
        assert run_row(db_url, run_id) == ("awaiting_approval", False)  # finished, never ended

        fresh = build_pipeline(pool, saver, ListSink())
        out = await fresh.recover(run_id)
        assert out.status == "completed" and "memory was lost" in (out.reason or "")
        assert run_row(db_url, run_id) == ("completed", True)
        with pytest.raises(NotRecoverable, match="already ended"):
            await fresh.recover(run_id)
