"""A provider outage is spend and never work (owner, 5 November 2026; migration 0047).

A call the provider answered with a 5xx is recorded `provider_unavailable`. It uses the quota and
the run's call cap (the provider was asked), and it is excluded from WORK exactly as an orphaned
call is, so a retry can never inflate ARR: the row that finally succeeded is the work. Here the
REAL ledger admits and finishes real rows, and the real attempt query is asserted against them.
"""

from collections.abc import AsyncIterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.evaluation.llm_attempts import llm_calls_by_attempt, totals
from app.llm.ledger import CallContext, CallLedger
from app.llm.prompts import PromptRef
from app.llm.registry import Tier
from tests.db.conftest import Seed
from tests.db.test_graph_checkpoints import new_run
from tests.llm_fakes import make_registry

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


def ledger(pool: AsyncConnectionPool[AsyncConnection], *, max_calls: int = 20) -> CallLedger:
    return CallLedger(
        pool,
        make_registry(rpm=1000),
        max_calls_per_run=max_calls,
        max_cost_per_run_usd=2.50,
        pending_expiry_s=900,
    )


async def one_call(led: CallLedger, ctx: CallContext, outcome: str) -> None:
    admission = await led.admit(ctx, Tier.CHEAP, PromptRef("p", "v1"), "test", 0.0001)
    assert admission.call_id is not None
    await led.finish(
        admission.call_id,
        outcome=outcome,  # type: ignore[arg-type]
        input_tokens=10 if outcome == "ok" else None,
        output_tokens=5 if outcome == "ok" else None,
        cost_usd=0.0001 if outcome == "ok" else None,
        latency_ms=12 if outcome == "ok" else None,
        rate_limit_wait_ms=0,
    )


async def test_the_database_accepts_the_new_outcome_and_still_rejects_others(
    db_url: str, seed: Seed
) -> None:
    run_id = new_run(db_url, seed)
    with psycopg.connect(db_url, autocommit=True) as c:
        definition = c.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid = 'public.llm_calls'::regclass and conname = 'llm_calls_outcome_check'"
        ).fetchone()
        assert definition is not None and "'provider_unavailable'" in definition[0]
        for kept in ("pending", "ok", "invalid_output", "error", "rate_limited", "refused",
                     "orphaned"):  # fmt: skip
            assert f"'{kept}'" in definition[0], kept
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute(
                "insert into llm_calls (org_id, run_id, model, tier, route_reason, prompt_id, "
                "prompt_version, outcome) "
                "values (%s, %s, 'm', 'cheap', 'r', 'p', 'v1', 'nonsense')",
                (seed.org_a, run_id),
            )


async def test_a_retry_is_spend_and_the_success_is_the_work(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    run_id = new_run(db_url, seed)
    ctx = CallContext(
        org_id=seed.org_a, run_id=run_id, agent_id="A1", step=7, revision_round=0,
        execution_id=uuid4(),
    )  # fmt: skip
    led = ledger(pool)
    # What the gateway writes for one call that met two 503s: two failed rows, then the success.
    for outcome in ("provider_unavailable", "provider_unavailable", "ok"):
        await one_call(led, ctx, outcome)
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        rows = [r for r in await llm_calls_by_attempt(conn, [seed.org_a]) if r.run_id == run_id]
    t = totals(rows)
    assert t.spend_calls == 3  # the provider was asked three times
    assert t.work_calls == 1  # ... and one of them is the work: ARR is not inflated
    assert t.unavailable_calls == 2
    assert t.spend_calls == (
        t.work_calls + t.resume_duplicate_calls + t.orphaned_calls + t.unavailable_calls
    )


async def test_retries_use_the_runs_call_cap_because_they_are_spend(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    from app.errors import BudgetExceeded

    run_id = new_run(db_url, seed)
    ctx = CallContext(org_id=seed.org_a, run_id=run_id, agent_id="A1")
    led = ledger(pool, max_calls=2)
    await one_call(led, ctx, "provider_unavailable")
    await one_call(led, ctx, "provider_unavailable")
    with pytest.raises(BudgetExceeded):  # a flaky provider eats the cap; the breaker bounds it
        await led.admit(ctx, Tier.CHEAP, PromptRef("p", "v1"), "test", 0.0001)
