"""runs.brief_check (migration 0038): the brief check is kept with the run, written by the backend.

The check is flag-and-record, never a block (app/core/brief_check.py). It must outlive the run's
checkpointed state, because how many briefs carried unbacked properties, and which, is evaluation
data. Like `declared_gaps` (0035) it is written with the terminal status from the run's own state,
and an API role can neither supply it at insert nor store anything that is not an object.
"""

import json
from collections.abc import AsyncIterator

import psycopg
import pytest
from psycopg import AsyncConnection, errors
from psycopg_pool import AsyncConnectionPool

from app.core.budgeted_run import RunFacts, end_run
from tests.db.conftest import Seed, Tx
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


CHECK = {
    "status": "checked",
    "unbacked": ["lightweight walking shoe"],
    "backed": 4,
    "prompt": "claim_extract@v2",
    "brief_sha256": "b" * 64,
    "claims_sha256": "c" * 64,
    "source": "precomputed",
}


def test_an_api_role_cannot_set_a_runs_brief_check(tx: Tx) -> None:
    tx.as_user("editor_a")
    rows = tx.execute(
        "insert into runs (org_id, product_id, request, brief_check) "
        "values (%s, %s, '{}', %s) returning brief_check",
        (tx.seed.org_a, tx.seed.product_a, json.dumps(CHECK)),
    )
    assert rows == [(None,)]


def test_brief_check_must_be_an_object(tx: Tx) -> None:
    with pytest.raises(errors.CheckViolation, match="brief_check"):
        tx.execute("update runs set brief_check = '[]' where id = %s", (tx.seed.run_a,))


@pytest.mark.anyio
async def test_end_run_writes_the_brief_check_and_a_run_without_one_stays_null(
    pool,
    db_url: str,
    seed: Seed,  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    flagged, plain = new_run(db_url, seed), new_run(db_url, seed)
    await end_run(
        pool, flagged, "completed", budget_exhausted=False, facts=RunFacts(brief_check=CHECK)
    )
    await end_run(pool, plain, "completed", budget_exhausted=False, facts=RunFacts())
    with psycopg.connect(db_url, autocommit=True) as c:
        got = c.execute("select brief_check from runs where id = %s", (flagged,)).fetchone()
        none = c.execute("select brief_check from runs where id = %s", (plain,)).fetchone()
    assert got == (CHECK,) and none == (None,)


@pytest.mark.anyio
async def test_a_later_end_without_a_check_keeps_the_one_already_written(
    pool,
    db_url: str,
    seed: Seed,  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    run = new_run(db_url, seed)
    await end_run(pool, run, "partial", budget_exhausted=True, facts=RunFacts(brief_check=CHECK))
    await end_run(pool, run, "partial", budget_exhausted=True, facts=RunFacts())
    with psycopg.connect(db_url, autocommit=True) as c:
        assert c.execute("select brief_check from runs where id = %s", (run,)).fetchone() == (
            CHECK,
        )
