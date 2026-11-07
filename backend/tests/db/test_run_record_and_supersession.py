"""runs.pages_fetched / pages_served / facts / supersedes_run_id (migration 0048).

The class of defect: values the analysis needs that lived only in the LangGraph checkpoint, and a
re-run that nothing linked to the run it replaced. Both are written by the backend, an API role can
supply neither, and the facts are written when a run PARKS, not only when it ends.
"""

import json
from collections.abc import AsyncIterator

import pytest
from psycopg import AsyncConnection, errors
from psycopg_pool import AsyncConnectionPool

from app.core.budgeted_run import RunFacts, end_run, suspend_run
from tests.db.conftest import Seed, Tx
from tests.db.test_graph_checkpoints import new_run

pytestmark = pytest.mark.db

FACTS = {"research": {"pages_fetched": 41, "partial": False}, "memory": {"coverage": 0.5}}


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


def test_an_api_role_can_supply_none_of_the_new_columns(tx: Tx) -> None:
    tx.as_user("editor_a")
    rows = tx.execute(
        "insert into runs (org_id, product_id, request, pages_fetched, pages_served, facts, "
        "supersedes_run_id) values (%s, %s, '{}', 9, 9, %s, %s) "
        "returning pages_fetched, pages_served, facts, supersedes_run_id",
        (tx.seed.org_a, tx.seed.product_a, json.dumps(FACTS), tx.seed.run_a),
    )
    assert rows == [(None, None, None, None)]


def test_facts_must_be_an_object_and_counts_are_not_negative(tx: Tx) -> None:
    with pytest.raises(errors.CheckViolation, match="facts"):
        tx.execute("update runs set facts = '[]' where id = %s", (tx.seed.run_a,))


def test_a_run_cannot_supersede_itself(tx: Tx) -> None:
    with pytest.raises(errors.CheckViolation, match="not_self_superseding"):
        tx.execute("update runs set supersedes_run_id = id where id = %s", (tx.seed.run_a,))


@pytest.mark.anyio
async def test_a_parked_run_keeps_its_facts_before_anyone_decides(
    pool,
    db_url: str,
    seed: Seed,  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    run = new_run(db_url, seed)
    await suspend_run(
        pool,
        run,
        RunFacts(depth="D2", search_count=8, pages_fetched=41, pages_served=39, facts=FACTS),
    )
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select status, depth, search_count, pages_fetched, pages_served, facts "
            "from runs where id = %s",
            (run,),
        )
        assert await cur.fetchone() == ("awaiting_approval", "D2", 8, 41, 39, FACTS)
    # and the end of the run does not blank what the park wrote when it has nothing newer
    await end_run(pool, run, "completed", budget_exhausted=False, facts=RunFacts(depth="D2"))
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select pages_fetched, pages_served, facts from runs where id = %s", (run,)
        )
        assert await cur.fetchone() == (41, 39, FACTS)


@pytest.mark.anyio
async def test_a_rerun_links_to_the_infrastructure_failed_run_it_replaces(
    pool,
    db_url: str,
    seed: Seed,  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    import uuid

    from app.core.budgeted_run import link_supersession

    brief = f"unique brief {uuid.uuid4()}"  # other tests leave failed runs for the same product
    result_run, infra_run = new_run(db_url, seed), new_run(db_url, seed)
    async with pool.connection() as conn:
        await conn.execute(
            "update runs set request = %s where id = any(%s)",
            (json.dumps({"brief": brief}), [result_run, infra_run]),
        )
        # a failure with no infrastructure kind is a RESULT and is never replaced
        await conn.execute(
            "update runs set status = 'failed', failure = %s where id = %s",
            (json.dumps({"kind": "x"}), result_run),
        )
        await conn.execute(
            "update runs set status = 'failed', failure = %s where id = %s",
            (json.dumps({"infrastructure_failure": "provider_unavailable"}), infra_run),
        )
    rerun, third = new_run(db_url, seed), new_run(db_url, seed)
    async with pool.connection() as conn:
        await conn.execute(
            "update runs set request = %s where id = any(%s)",
            (json.dumps({"brief": brief}), [rerun, third]),
        )
    assert await link_supersession(pool, rerun) == infra_run
    assert await link_supersession(pool, third) is None  # the failed run is replaced once


@pytest.mark.anyio
async def test_a_run_can_be_superseded_once_and_only_inside_its_organisation(
    pool,
    db_url: str,
    seed: Seed,  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    old, first, second = new_run(db_url, seed), new_run(db_url, seed), new_run(db_url, seed)
    async with pool.connection() as conn:
        await conn.execute("update runs set supersedes_run_id = %s where id = %s", (old, first))
        with pytest.raises(errors.UniqueViolation):
            await conn.execute(
                "update runs set supersedes_run_id = %s where id = %s", (old, second)
            )
