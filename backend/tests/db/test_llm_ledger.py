"""Budget and quota accounting derive from llm_calls alone (Rules.md A1, C3, C9, C10).

Real Postgres with the migrations (0013 included). Each test uses model IDs of its own
(tests/llm_fakes.make_registry), so daily and per-minute counts never mix across tests.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from app.errors import BudgetExceeded, QuotaExhausted
from app.llm.gateway import LLMGateway
from app.llm.ledger import CallContext, CallLedger, Usage
from app.llm.prompts import PromptRef
from app.llm.registry import ModelRegistry, Tier
from tests.db.conftest import Seed
from tests.llm_fakes import FakeCompletion, judge, make_registry, prompt_library, reply

pytestmark = [pytest.mark.anyio, pytest.mark.db]

P = PromptRef("test_judge", "v1")


@pytest.fixture
async def pool(db_url: str) -> AsyncIterator[AsyncConnectionPool[AsyncConnection]]:
    p: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        db_url, min_size=0, max_size=6, open=False
    )
    await p.open()
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture
def run_ctx(db_url: str, seed: Seed) -> CallContext:
    """A fresh run in org A, so per-run counts start at zero."""
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "insert into runs (org_id, product_id, request) values (%s, %s, '{}') returning id",
            (seed.org_a, seed.product_a),
        ).fetchone()
    assert row is not None
    return CallContext(org_id=seed.org_a, run_id=row[0], agent_id="test")


def ledger(
    pool: AsyncConnectionPool[AsyncConnection], registry: ModelRegistry, **caps: float
) -> CallLedger:
    return CallLedger(
        pool,
        registry,
        max_calls_per_run=int(caps.get("calls", 20)),
        max_cost_per_run_usd=caps.get("cost", 2.50),
        pending_expiry_s=int(caps.get("expiry", 900)),
    )


def insert_calls(
    db_url: str,
    ctx: CallContext,
    model: str,
    n: int,
    *,
    outcome: str = "ok",
    cost: float = 0.001,
    age: str = "0 seconds",
) -> None:
    with psycopg.connect(db_url, autocommit=True) as c:
        for _ in range(n):
            c.execute(
                "insert into llm_calls (org_id, run_id, model, tier, prompt_id, prompt_version, "
                "route_reason, outcome, cost_usd, created_at) "
                "values (%s, %s, %s, 'cheap', 'test_judge', 'v1', 'seeded', %s, %s, "
                "now() - %s::interval)",
                (ctx.org_id, ctx.run_id, model, outcome, cost, age),
            )


def rows(db_url: str, run_id: UUID) -> list[tuple[object, ...]]:
    with psycopg.connect(db_url) as c:
        return c.execute(
            "select outcome, route_reason, input_tokens, output_tokens, cost_usd, latency_ms, "
            "rate_limit_wait_ms, finished_at is not null from llm_calls where run_id = %s "
            "and route_reason <> 'seeded' order by id",
            (run_id,),
        ).fetchall()


async def test_admission_writes_a_pending_row_and_finish_completes_it(
    pool: AsyncConnectionPool[AsyncConnection], run_ctx: CallContext, db_url: str
) -> None:
    led = ledger(pool, make_registry())
    admission = await led.admit(run_ctx, Tier.CHEAP, P, "default tier", 0.001)
    assert admission.call_id is not None
    assert rows(db_url, run_ctx.run_id) == [
        ("pending", "default tier", None, None, None, None, 0, False)
    ]
    await led.finish(
        admission.call_id,
        outcome="ok",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.00002,
        latency_ms=321,
        rate_limit_wait_ms=1500,
    )
    (row,) = rows(db_url, run_ctx.run_id)
    assert row[0] == "ok" and row[2:4] == (10, 5) and row[5:] == (321, 1500, True)


async def test_the_run_call_cap_is_counted_from_llm_calls(
    pool: AsyncConnectionPool[AsyncConnection], run_ctx: CallContext, db_url: str
) -> None:
    registry = make_registry()
    insert_calls(db_url, run_ctx, registry.model(Tier.CHEAP).id, 20)
    with pytest.raises(BudgetExceeded, match="20 of 20 LLM calls"):
        await ledger(pool, registry).admit(run_ctx, Tier.CHEAP, P, "default tier", 0.001)
    # The refusal is itself logged, with its reason (Rules.md C9) — and is not a call.
    assert [r[0] for r in rows(db_url, run_ctx.run_id)] == ["refused"]


async def test_refusals_are_not_counted_as_calls(
    pool: AsyncConnectionPool[AsyncConnection], run_ctx: CallContext, db_url: str
) -> None:
    registry = make_registry(rpm=1000)  # this test is about counting, not RPM pacing
    insert_calls(db_url, run_ctx, registry.model(Tier.CHEAP).id, 19)
    insert_calls(db_url, run_ctx, registry.model(Tier.CHEAP).id, 30, outcome="refused")
    admission = await ledger(pool, registry).admit(run_ctx, Tier.CHEAP, P, "default tier", 0.001)
    assert admission.call_id is not None


async def test_the_run_cost_cap_includes_the_estimate_and_pending_calls(
    pool: AsyncConnectionPool[AsyncConnection], run_ctx: CallContext, db_url: str
) -> None:
    registry = make_registry()
    insert_calls(db_url, run_ctx, registry.model(Tier.CHEAP).id, 1, cost=2.45)
    led = ledger(pool, registry)
    first = await led.admit(run_ctx, Tier.CHEAP, P, "default tier", 0.02)  # 2.45 + 0.02
    assert first.call_id is not None
    # Still pending: counted at the estimate. 2.45 + 2 x 0.04 would pass 2.50.
    with pytest.raises(BudgetExceeded, match=r"\$2\.5300 of \$2\.50"):
        await led.admit(run_ctx, Tier.CHEAP, P, "default tier", 0.04)


async def test_the_daily_quota_counts_since_midnight_pacific_only(
    pool: AsyncConnectionPool[AsyncConnection], run_ctx: CallContext, db_url: str
) -> None:
    registry = make_registry(strong_rpd=3)
    model = registry.model(Tier.STRONG).id
    with psycopg.connect(db_url) as c:
        since_reset = c.execute(
            "select extract(epoch from now() - (date_trunc('day', now() at time zone "
            "'America/Los_Angeles') at time zone 'America/Los_Angeles'))"
        ).fetchone()
    assert since_reset is not None
    before_reset = f"{int(since_reset[0]) + 60} seconds"
    insert_calls(db_url, run_ctx, model, 50, age=before_reset)  # yesterday's: not counted
    insert_calls(db_url, run_ctx, model, 2)
    led = ledger(pool, registry, calls=1000, cost=1000)
    assert (await led.admit(run_ctx, Tier.STRONG, P, "high risk", 0.0)).call_id is not None
    with pytest.raises(QuotaExhausted, match="3 of 3 requests since the daily reset"):
        await led.admit(run_ctx, Tier.STRONG, P, "high risk", 0.0)


async def test_rpm_pacing_asks_for_a_wait_and_writes_nothing(
    pool: AsyncConnectionPool[AsyncConnection], run_ctx: CallContext, db_url: str
) -> None:
    registry = make_registry(rpm=5)
    insert_calls(db_url, run_ctx, registry.model(Tier.CHEAP).id, 5, age="20 seconds")
    admission = await ledger(pool, registry).admit(run_ctx, Tier.CHEAP, P, "default tier", 0.0)
    assert admission.call_id is None
    assert 39 <= admission.wait_s <= 41  # the oldest call in the window leaves it in ~40 s
    assert rows(db_url, run_ctx.run_id) == []


async def test_concurrent_admissions_never_exceed_the_quota(
    pool: AsyncConnectionPool[AsyncConnection], run_ctx: CallContext, db_url: str
) -> None:
    registry = make_registry(strong_rpd=20, rpm=1000)
    insert_calls(db_url, run_ctx, registry.model(Tier.STRONG).id, 17)

    class SlowLedger(CallLedger):
        # Pause between reading usage and writing the reservation, so every admission is in
        # the race window at once. Only the admission lock keeps the count right.
        async def usage(self, conn: AsyncConnection, ctx: CallContext, tier: Tier) -> Usage:
            found = await super().usage(conn, ctx, tier)
            await asyncio.sleep(0.05)
            return found

    led = SlowLedger(
        pool, registry, max_calls_per_run=1000, max_cost_per_run_usd=1000, pending_expiry_s=900
    )

    async def one() -> bool:
        try:
            return (await led.admit(run_ctx, Tier.STRONG, P, "high risk", 0.0)).call_id is not None
        except QuotaExhausted:
            return False

    admitted = await asyncio.gather(*(one() for _ in range(12)))
    assert sum(admitted) == 3  # exactly the 3 left, whatever the interleaving
    outcomes = [r[0] for r in rows(db_url, run_ctx.run_id)]
    assert outcomes.count("pending") == 3 and outcomes.count("refused") == 9


class Answer(BaseModel):
    answer: str


class TimedCompletion(FakeCompletion):
    """The fake provider, noting when each reply is handed back to the gateway."""

    def __init__(self, *script: object) -> None:
        super().__init__(*script)
        self.returned_at: list[float] = []

    async def __call__(self, **kwargs: object) -> object:
        response = await super().__call__(**kwargs)
        self.returned_at.append(time.perf_counter())
        return response


COST_VISIBILITY_CALLS = 20


async def test_cost_is_in_the_database_within_one_second_of_every_call(
    pool: AsyncConnectionPool[AsyncConnection], run_ctx: CallContext, db_url: str
) -> None:
    """Phases.md Phase 2 exit criterion: "cost per call appears in the database within one
    second" — over 20 mocked calls, not one observation.

    Each interval runs from the moment the provider's reply reaches the gateway to the moment a
    SEPARATE connection has read that call's row with its cost: an upper bound on the time to
    visibility, independent of any clock difference between the app and Postgres.
    """
    registry = make_registry(rpm=1000)
    completion = TimedCompletion(
        *[reply('{"answer": "42"}', 200 + i, 30) for i in range(COST_VISIBILITY_CALLS)]
    )
    gateway = LLMGateway(
        registry,
        ledger(pool, registry, calls=1000, cost=1000),
        prompt_library(),
        api_key="test",
        timeout_s=5,
        max_rate_limit_waits=0,
        max_rate_limit_wait_s=1,
        completion=completion,
    )
    intervals_ms: list[float] = []
    with psycopg.connect(db_url, autocommit=True) as observer:
        for i in range(COST_VISIBILITY_CALLS):
            returned = await gateway.structured(
                Answer, tier=Tier.CHEAP, prompt=judge(), ctx=run_ctx, route_reason="default tier"
            )
            row = observer.execute(
                "select outcome, input_tokens, output_tokens, cost_usd, finished_at is not null "
                "from llm_calls where run_id = %s order by id desc limit 1",
                (run_ctx.run_id,),
            ).fetchone()
            intervals_ms.append((time.perf_counter() - completion.returned_at[i]) * 1000)
            assert returned == Answer(answer="42")
            assert row is not None
            assert row[0] == "ok" and row[1:3] == (200 + i, 30) and row[4] is True
            expected = ((200 + i) * 0.25 + 30 * 1.50) / 1e6
            assert float(row[3]) == pytest.approx(expected, abs=5e-7)  # numeric(10,6)

    intervals_ms.sort()
    print(  # noqa: T201 — the measurement is recorded in Architecture.md §4.4
        f"\ncost visible after provider reply, {len(intervals_ms)} calls: "
        f"min {intervals_ms[0]:.1f} ms, median {intervals_ms[len(intervals_ms) // 2]:.1f} ms, "
        f"max {intervals_ms[-1]:.1f} ms"
    )
    assert intervals_ms[-1] < 1000


async def test_abandoned_reservations_stop_counting_after_the_expiry(
    pool: AsyncConnectionPool[AsyncConnection], run_ctx: CallContext, db_url: str
) -> None:
    """A process that dies between admission and finish must not hold a slot for ever."""
    registry = make_registry(strong_rpd=3, rpm=1000)
    model = registry.model(Tier.STRONG).id
    led = ledger(pool, registry, calls=1000, cost=1000, expiry=900)
    insert_calls(db_url, run_ctx, model, 3, outcome="pending", age="901 seconds")  # abandoned
    assert (await led.admit(run_ctx, Tier.STRONG, P, "high risk", 0.0)).call_id is not None
    insert_calls(db_url, run_ctx, model, 2, outcome="pending", age="899 seconds")  # still live
    with pytest.raises(QuotaExhausted, match="3 of 3"):
        await led.admit(run_ctx, Tier.STRONG, P, "high risk", 0.0)


async def test_the_daily_report_counts_refusals_and_abandoned_calls(
    pool: AsyncConnectionPool[AsyncConnection], run_ctx: CallContext, db_url: str
) -> None:
    """Rules.md A2: refused and failed work is reportable per model per day, not dropped.

    Reports a fixed past day, with rows placed relative to its Pacific midnight, so the result
    does not depend on the time of day the test runs.
    """
    registry = make_registry()
    model = registry.model(Tier.CHEAP).id
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "select (now() at time zone 'America/Los_Angeles')::date - 3, "
            "((now() at time zone 'America/Los_Angeles')::date - 3)::timestamp "
            "at time zone 'America/Los_Angeles'"
        ).fetchone()
        assert row is not None
        day, midnight = row

        def add(outcome: str, n: int, *, cost: float = 0.001, offset: str = "12 hours") -> None:
            for _ in range(n):
                c.execute(
                    "insert into llm_calls (org_id, run_id, model, tier, prompt_id, "
                    "prompt_version, route_reason, outcome, cost_usd, created_at) "
                    "values (%s, %s, %s, 'cheap', 'test_judge', 'v1', 'seeded', %s, %s, "
                    "%s::timestamptz + %s::interval)",
                    (run_ctx.org_id, run_ctx.run_id, model, outcome, cost, midnight, offset),
                )

        add("ok", 4, cost=0.01)
        add("invalid_output", 1)
        add("error", 1)
        add("refused", 2, cost=0)
        add("pending", 1)  # never finished: abandoned long ago
        add("ok", 5, offset="-1 second")  # the day before: not this day
        add("ok", 7, offset="1 day")  # the day after: not this day
    report = {r.model: r for r in await ledger(pool, registry).daily_report(day)}
    d = report[model]
    assert (d.ok, d.invalid_output, d.error, d.refused, d.abandoned, d.pending) == (
        4,
        1,
        1,
        2,
        1,
        0,
    )
    assert d.calls == 6  # refusals and abandoned reservations are reported, not counted
    assert d.cost_usd == pytest.approx(4 * 0.01 + 3 * 0.001)


def fresh_org(db_url: str, cap: float = 50.0) -> UUID:
    """An organisation of its own, so its month-to-date spend starts at zero."""
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "insert into organisations (name, monthly_budget_cap_usd) values ('ledger test', %s) "
            "returning id",
            (cap,),
        ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def new_run(db_url: str, org_id: UUID) -> CallContext:
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "insert into runs (org_id, request) values (%s, '{}') returning id", (org_id,)
        ).fetchone()
    assert row is not None
    return CallContext(org_id=org_id, run_id=row[0], agent_id="test")


async def test_locks_are_per_org_run_and_model_not_global(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str
) -> None:
    """Admissions in other organisations on other models must not wait; the org's must."""
    registry = make_registry(rpm=1000)
    led = ledger(pool, registry, calls=1000, cost=1000)
    org, other_org = fresh_org(db_url), fresh_org(db_url)
    held_run, same_org_run, other_org_run = (
        new_run(db_url, org),
        new_run(db_url, org),
        new_run(db_url, other_org),
    )
    holder = await psycopg.AsyncConnection.connect(db_url)
    try:
        async with holder.transaction():
            # Hold what an in-flight admission holds: its org, its run and the cheap model.
            for key in (
                f"llm:org:{org}",
                f"llm:run:{held_run.run_id}",
                f"llm:model:{registry.model(Tier.CHEAP).id}",
            ):
                await holder.execute(
                    "select pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,)
                )
            # Another organisation, another model: proceeds at once.
            free = await asyncio.wait_for(led.admit(other_org_run, Tier.STANDARD, P, "x", 0.0), 2)
            assert free.call_id is not None
            # The held org (any run, any model) and the held model (any org) must wait.
            for blocked in (
                led.admit(held_run, Tier.STANDARD, P, "x", 0.0),
                led.admit(same_org_run, Tier.STANDARD, P, "x", 0.0),
                led.admit(other_org_run, Tier.CHEAP, P, "x", 0.0),
            ):
                task = asyncio.create_task(blocked)
                await asyncio.sleep(0.5)
                assert not task.done(), "admission did not wait for the held lock"
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    finally:
        await holder.close()


def insert_org_calls(
    db_url: str, ctx: CallContext, cost: float, *, outcome: str = "ok", at: str = "now()"
) -> None:
    """One row of `cost` in ctx's run, at `at` ('now()' or 'month_start - 1 second')."""
    when = {
        "now()": "select now()",
        "last month": "select date_trunc('month', now() at time zone 'UTC') at time zone 'UTC' "
        "- interval '1 second'",
        "abandoned": "select now() - interval '901 seconds'",
    }[at]
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(when).fetchone()
        assert row is not None
        c.execute(
            "insert into llm_calls (org_id, run_id, model, tier, prompt_id, prompt_version, "
            "route_reason, outcome, cost_usd, created_at) values (%s, %s, 'seeded-model', "
            "'cheap', 'test_judge', 'v1', 'seeded', %s, %s, %s)",
            (ctx.org_id, ctx.run_id, outcome, cost, row[0]),
        )


async def test_the_monthly_cap_counts_the_whole_organisation_this_month(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str
) -> None:
    """Architecture.md §9: the estimate must fit the org's remaining month-to-date budget."""
    org = fresh_org(db_url, cap=1.00)
    insert_org_calls(db_url, new_run(db_url, org), 0.97)  # spent by an earlier run this month
    led = ledger(pool, make_registry(), calls=1000, cost=1000)
    run = new_run(db_url, org)
    assert (await led.admit(run, Tier.CHEAP, P, "default tier", 0.02)).call_id is not None
    # 0.97 + this call's estimate for the pending one + 0.02 = 1.01 > 1.00
    with pytest.raises(BudgetExceeded, match=r"month-to-date cost would reach \$1\.0100 of its"):
        await led.admit(run, Tier.CHEAP, P, "default tier", 0.02)
    with psycopg.connect(db_url) as c:
        log = c.execute(
            "select outcome, route_reason from llm_calls where run_id = %s order by id",
            (run.run_id,),
        ).fetchall()
    assert [o for o, _ in log] == ["pending", "refused"]
    assert "organisations.monthly_budget_cap_usd" in log[1][1]


async def test_the_monthly_cap_ignores_last_month_refusals_abandoned_and_other_orgs(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str
) -> None:
    org = fresh_org(db_url, cap=1.00)
    earlier = new_run(db_url, org)
    insert_org_calls(db_url, earlier, 5.00, at="last month")
    insert_org_calls(db_url, earlier, 5.00, outcome="refused")
    insert_org_calls(db_url, earlier, 5.00, outcome="pending", at="abandoned")
    insert_org_calls(db_url, new_run(db_url, fresh_org(db_url, cap=1.00)), 5.00)  # another org
    led = ledger(pool, make_registry(), calls=1000, cost=1000)
    assert (await led.admit(new_run(db_url, org), Tier.CHEAP, P, "x", 0.99)).call_id is not None


async def test_concurrent_runs_in_one_org_never_exceed_its_monthly_cap(db_url: str) -> None:
    """Thirty runs admit at once at $0.10 each under a $1.00 cap: exactly ten may pass.

    Spread over all three models and run on thirty connections, so neither the model locks nor
    the pool serialise them: only the org lock does. Checked 2026-10-17 with the org lock
    removed: 12 of 30 admitted in 2 of 3 tries, so a missing lock is usually caught, not always.
    """
    org = fresh_org(db_url, cap=1.00)
    runs = [new_run(db_url, org) for _ in range(30)]
    tiers = [Tier.CHEAP, Tier.STANDARD, Tier.STRONG] * 10
    big: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        db_url, min_size=30, max_size=30, open=False
    )
    await big.open(wait=True)
    try:
        led = ledger(big, make_registry(rpm=1000), calls=1000, cost=1000)

        async def attempt(ctx: CallContext, tier: Tier) -> bool:
            try:
                return (await led.admit(ctx, tier, P, "x", 0.10)).call_id is not None
            except BudgetExceeded:
                return False

        admitted = await asyncio.gather(*(attempt(r, t) for r, t in zip(runs, tiers, strict=True)))
    finally:
        await big.close()
    assert sum(admitted) == 10


async def test_every_row_records_its_prompt_refusals_included(
    pool: AsyncConnectionPool[AsyncConnection], run_ctx: CallContext, db_url: str
) -> None:
    """Migration 0014: which template produced each call (Rules.md A5, A6)."""
    registry = make_registry()
    led = ledger(pool, registry, calls=1)
    other = PromptRef("test_other", "v7")
    await led.admit(run_ctx, Tier.CHEAP, P, "default tier", 0.0)
    with pytest.raises(BudgetExceeded):
        await led.admit(run_ctx, Tier.CHEAP, other, "default tier", 0.0)
    await led.refuse(run_ctx, Tier.STANDARD, other, "refused: test")
    with psycopg.connect(db_url) as c:
        got = c.execute(
            "select outcome, prompt_id, prompt_version from llm_calls where run_id = %s "
            "order by id",
            (run_ctx.run_id,),
        ).fetchall()
    assert got == [
        ("pending", "test_judge", "v1"),
        ("refused", "test_other", "v7"),
        ("refused", "test_other", "v7"),
    ]


@pytest.mark.parametrize(
    ("prompt_id", "prompt_version"),
    [(None, "v1"), ("test_judge", None), ("Bad-Id", "v1"), ("test_judge", "1"), ("x", "v0")],
)
def test_the_database_refuses_a_call_without_a_well_formed_prompt(
    db_url: str, run_ctx: CallContext, prompt_id: str | None, prompt_version: str | None
) -> None:
    with (
        psycopg.connect(db_url) as c,
        pytest.raises((psycopg.errors.NotNullViolation, psycopg.errors.CheckViolation)),
    ):
        c.execute(
            "insert into llm_calls (org_id, run_id, model, tier, prompt_id, prompt_version, "
            "route_reason) values (%s, %s, 'm', 'cheap', %s, %s, 'r')",
            (run_ctx.org_id, run_ctx.run_id, prompt_id, prompt_version),
        )


async def test_the_ledger_reports_whether_an_organisation_is_flagged_practice(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str
) -> None:
    practice = fresh_org(db_url)
    ordinary = fresh_org(db_url)
    with psycopg.connect(db_url, autocommit=True) as c:
        c.execute("update organisations set is_practice = true where id = %s", (practice,))
    led = ledger(pool, make_registry())
    assert await led.organisation_is_practice(practice) is True
    assert await led.organisation_is_practice(ordinary) is False
    assert await led.organisation_is_practice(uuid4()) is False  # no such organisation
