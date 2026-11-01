"""A run records where its local time went, and the evaluation reads it in parts with a named
residual (migration 0044; app/evaluation/latency.py).

The run is driven by the mock pipeline, whose research and recall are not the timed ones, so the
figures written here are set by the test: what is pinned is the PLUMBING (state -> run record ->
read) and the ARITHMETIC of the residual, not the milliseconds of a real encoder.
"""

import json
from collections.abc import AsyncIterator

import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.budgeted_run import RunFacts, end_run
from app.evaluation.latency import RunLatency, run_latencies
from tests.db.conftest import Seed
from tests.db.test_graph_checkpoints import new_run

pytestmark = [pytest.mark.anyio, pytest.mark.db]


@pytest.fixture
async def pool(db_url: str) -> AsyncIterator[AsyncConnectionPool[AsyncConnection]]:
    p: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        db_url, min_size=0, max_size=3, open=False
    )
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def test_timings_are_written_with_the_terminal_status(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    run_id = new_run(db_url, seed)
    timings = {"search_ms": 3.0, "research_other_ms": 40.0, "encoder_recall_ms": 12.5}
    await end_run(
        pool, run_id, "completed", budget_exhausted=False, facts=RunFacts(timings=timings)
    )
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute("select timings from runs where id = %s", (run_id,)).fetchone()
    assert row is not None and row[0] == timings


async def test_a_run_ended_without_timings_keeps_them_unknown_not_zero(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    run_id = new_run(db_url, seed)
    await end_run(pool, run_id, "completed", budget_exhausted=False)
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute("select timings from runs where id = %s", (run_id,)).fetchone()
    assert row is not None and row[0] is None


def test_an_api_role_cannot_write_timings(db_url: str, seed: Seed) -> None:
    with psycopg.connect(db_url, autocommit=True) as c, c.transaction():
        c.execute(
            "select set_config('role', 'authenticated', true), "
            "set_config('request.jwt.claims', %s, true)",
            (json.dumps({"sub": str(seed.users["editor_a"]), "role": "authenticated"}),),
        )
        row = c.execute(
            "insert into runs (org_id, product_id, request, timings) "
            "values (%s, %s, '{}', '{\"search_ms\": 1}') returning timings",
            (seed.org_a, seed.product_a),
        ).fetchone()
    assert row is not None and row[0] is None  # forced null by the insert trigger (0044)


def latency(timings: dict[str, float] | None, *, model_ms: int, recorded_ms: int) -> RunLatency:
    return RunLatency(
        run_id=__import__("uuid").uuid4(),
        arm="proposed",
        depth="D1",
        status="completed",
        wall_ms=recorded_ms + 500,
        waited_ms=500,
        open_waits=0,
        waits=1,
        recorded_ms=recorded_ms,
        model_ms=model_ms,
        timings=timings,
    )


def test_the_residual_is_what_nothing_measured_signed_and_never_clamped() -> None:
    t = {"search_ms": 1.0, "research_other_ms": 100.0, "encoder_recall_ms": 50.0,
         "encoder_writeback_ms": 25.0}  # fmt: skip
    parts = latency(t, model_ms=300, recorded_ms=1000).components()
    assert parts["waits_ms"] == 500.0
    # 1000 active - (300 model + 1 + 100 + 50 + 25) = 524 unaccounted for: its own bucket.
    assert parts["residual_ms"] == pytest.approx(524.0)
    # Concurrent model calls overlap: summed model latency can exceed the time they took.
    over = latency(t, model_ms=2000, recorded_ms=1000).components()
    assert over["residual_ms"] == pytest.approx(1000 - (2000 + 176))
    assert over["residual_ms"] < 0  # reported as it is, not folded into any named component


def test_a_run_with_no_timings_has_unknown_local_parts_and_no_residual() -> None:
    parts = latency(None, model_ms=300, recorded_ms=1000).components()
    assert parts["model_ms"] == 300.0 and parts["waits_ms"] == 500.0
    for name in ("search_ms", "research_other_ms", "encoder_recall_ms", "encoder_writeback_ms"):
        assert parts[name] is None
    assert parts["residual_ms"] is None


async def test_the_evaluation_read_returns_the_parts(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    run_id = new_run(db_url, seed)
    await end_run(
        pool,
        run_id,
        "completed",
        budget_exhausted=False,
        facts=RunFacts(timings={"search_ms": 2.0}),
    )
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        (lat,) = [r for r in await run_latencies(conn, [seed.org_a]) if r.run_id == run_id]
    assert lat.timings == {"search_ms": 2.0} and lat.model_ms == 0
    assert lat.components()["search_ms"] == 2.0
