"""Phases.md Phase 2 exit criterion: a deliberate over-budget run aborts cleanly with partial
output flagged budget_exhausted (Rules.md C3, C4). Mocked provider; real ledger and database.
"""

from collections.abc import AsyncIterator, Mapping

import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from app.core.budgeted_run import Step, run_steps
from app.errors import LLMCallFailed
from app.llm.gateway import LLMGateway
from app.llm.ledger import CallContext, CallLedger
from app.llm.registry import Tier
from tests.db.conftest import Seed
from tests.llm_fakes import FakeCompletion, judge, make_registry, prompt_library, reply

pytestmark = [pytest.mark.anyio, pytest.mark.db]

STEPS = ["normalise_request", "assess_research_necessity", "build_strategy", "generate_creative"]


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


def new_run(db_url: str, seed: Seed) -> CallContext:
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "insert into runs (org_id, product_id, request) values (%s, %s, '{}') returning id",
            (seed.org_a, seed.product_a),
        ).fetchone()
    assert row is not None
    return CallContext(org_id=seed.org_a, run_id=row[0], agent_id="test")


def pipeline(
    pool: AsyncConnectionPool[AsyncConnection],
    ctx: CallContext,
    completion: FakeCompletion,
    *,
    max_calls: int = 20,
    max_cost: float = 2.50,
) -> list[Step]:
    """Four steps, one gateway call each, under the run's caps."""
    registry = make_registry(rpm=1000)
    ledger = CallLedger(
        pool,
        registry,
        max_calls_per_run=max_calls,
        max_cost_per_run_usd=max_cost,
        pending_expiry_s=900,
    )
    gateway = LLMGateway(
        registry,
        ledger,
        prompt_library(),
        api_key="test",
        timeout_s=5,
        max_rate_limit_waits=0,
        max_rate_limit_wait_s=1,
        completion=completion,
    )

    def step(name: str) -> Step:
        async def run(prior: Mapping[str, BaseModel]) -> BaseModel:
            assert list(prior) == STEPS[: STEPS.index(name)]  # each step sees those before it
            return await gateway.structured(
                Out, tier=Tier.CHEAP, prompt=judge(name), ctx=ctx, route_reason="default tier"
            )

        return Step(name, run)

    return [step(name) for name in STEPS]


def run_row(db_url: str, ctx: CallContext) -> tuple[object, ...]:
    with psycopg.connect(db_url) as c:
        row = c.execute(
            "select status, budget_exhausted, llm_call_count, completed_at is not null "
            "from runs where id = %s",
            (ctx.run_id,),
        ).fetchone()
    assert row is not None
    return tuple(row)


def calls(db_url: str, ctx: CallContext) -> list[tuple[object, ...]]:
    with psycopg.connect(db_url) as c:
        return c.execute(
            "select outcome, route_reason from llm_calls where run_id = %s order by id",
            (ctx.run_id,),
        ).fetchall()


def replies(n: int, tokens: int = 100) -> FakeCompletion:
    return FakeCompletion(*[reply(f'{{"step": "{s}"}}', tokens, tokens) for s in STEPS[:n]])


async def test_a_run_over_its_call_cap_ends_partial_and_flagged(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    ctx = new_run(db_url, seed)
    completion = replies(2)  # the fake model would fail the test if called a third time
    outcome = await run_steps(
        pipeline(pool, ctx, completion, max_calls=2), pool=pool, run_id=ctx.run_id
    )

    # Partial output, flagged — not a crash, not a silent truncation (Rules.md C4).
    assert outcome.status == "partial" and outcome.budget_exhausted is True
    assert list(outcome.outputs) == STEPS[:2]
    assert outcome.outputs[STEPS[1]] == Out(step=STEPS[1])
    assert outcome.not_run == STEPS[2:]
    assert outcome.reason is not None and "2 of 2 LLM calls" in outcome.reason
    # The model was never asked for the refused call (pre-flight, not post-hoc).
    assert len(completion.calls) == 2
    # The database says the same: the run is flagged, the refusal logged with its reason.
    assert run_row(db_url, ctx) == ("partial", True, 2, True)
    log = calls(db_url, ctx)
    assert [o for o, _ in log] == ["ok", "ok", "refused"]
    assert "Rules.md C3" in str(log[2][1])


async def test_a_run_over_its_cost_cap_ends_partial_and_flagged(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    ctx = new_run(db_url, seed)
    # Pre-flight estimate per call: about $0.00154 (1,024 output tokens at $1.50/1M). Each call
    # then costs $0.00175 (1,000 in at $0.25/1M + 1,000 out at $1.50/1M). Projected before call
    # 2: 0.00175 + 0.00154 = 0.00329 (admitted); before call 3: 0.0035 + 0.00154 = 0.00504,
    # over the $0.004 cap (refused).
    outcome = await run_steps(
        pipeline(pool, ctx, replies(2, tokens=1000), max_cost=0.004), pool=pool, run_id=ctx.run_id
    )
    assert (outcome.status, outcome.budget_exhausted) == ("partial", True)
    assert outcome.not_run == STEPS[2:]
    assert outcome.reason is not None and "list-price cap" in outcome.reason
    assert run_row(db_url, ctx) == ("partial", True, 2, True)


async def test_a_run_within_budget_completes_unflagged(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    ctx = new_run(db_url, seed)
    outcome = await run_steps(pipeline(pool, ctx, replies(4)), pool=pool, run_id=ctx.run_id)
    assert (outcome.status, outcome.budget_exhausted, outcome.not_run) == ("completed", False, [])
    assert list(outcome.outputs) == STEPS
    assert run_row(db_url, ctx) == ("completed", False, 4, True)


async def test_any_other_error_fails_the_run_and_propagates(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    ctx = new_run(db_url, seed)
    completion = FakeCompletion(reply('{"step": "x"}'), RuntimeError("upstream exploded"))
    with pytest.raises(LLMCallFailed):
        await run_steps(pipeline(pool, ctx, completion), pool=pool, run_id=ctx.run_id)
    assert run_row(db_url, ctx) == ("failed", False, 2, True)


async def test_a_run_over_its_organisations_monthly_cap_ends_partial_and_flagged(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str
) -> None:
    """The org's month-to-date cap (Architecture.md §9) ends a run exactly as the run caps do."""
    with psycopg.connect(db_url, autocommit=True) as c:
        org = c.execute(
            "insert into organisations (name, monthly_budget_cap_usd) values ('cap test', 0.01) "
            "returning id"
        ).fetchone()
        assert org is not None
        earlier, current = (
            c.execute(
                "insert into runs (org_id, request) values (%s, '{}') returning id", (org[0],)
            ).fetchone()
            for _ in range(2)
        )
        assert earlier is not None and current is not None
        # $0.0075 already spent this month by an earlier run of the organisation.
        c.execute(
            "insert into llm_calls (org_id, run_id, model, tier, prompt_id, prompt_version, "
            "route_reason, outcome, cost_usd) values (%s, %s, 'seeded-model', 'cheap', "
            "'test_judge', 'v1', 'seeded', 'ok', 0.0075)",
            (org[0], earlier[0]),
        )
    ctx = CallContext(org_id=org[0], run_id=current[0], agent_id="test")
    # Before call 1: 0.0075 + 0.00154 = 0.00904 (admitted). Call 1 costs 0.00175. Before call 2:
    # 0.00925 + 0.00154 = 0.01079, over the $0.01 cap (refused). The run's own caps never bind.
    outcome = await run_steps(
        pipeline(pool, ctx, replies(1, tokens=1000)), pool=pool, run_id=ctx.run_id
    )
    assert (outcome.status, outcome.budget_exhausted) == ("partial", True)
    assert list(outcome.outputs) == STEPS[:1] and outcome.not_run == STEPS[1:]
    assert outcome.reason is not None and "monthly cap" in outcome.reason
    assert run_row(db_url, ctx) == ("partial", True, 1, True)
    assert [o for o, _ in calls(db_url, ctx)] == ["ok", "refused"]
