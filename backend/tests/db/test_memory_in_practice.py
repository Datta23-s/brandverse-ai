"""What memory is, IN PRACTICE, today: a characterization of the gap that decides whether the
six-component M exists (Phase 6 chunk D, owner's check 2026-10-31).

Chunk C wired `C` and `E` into the M-score and proved it with rows inserted by hand. These tests
pin what a real run actually does, so the claim "the six-component M does not exist in practice" is
checkable, and breaks loudly, to be updated on purpose, the day write-back is built:

1. A real run writes NO memory item. `writeback_step` passes `candidates=[]`: reading the facts out
   of an approved campaign is not built (its own docstring says so). With nothing written, the
   Proposed arm's memory stays empty and there is nothing to recall.
2. Even an item that IS written (by `commit_memory`, as the tests of the commit half do) gets only a
   `source_url` string. No `sources` row and no `memory_item_sources` row is written for it, so its
   `source_score` is NULL, and `E` (source score x confidence) is LEFT OUT of its M.

So no M in a real run has six components: today none exists to score, and a written item would have
five at most (C needs persona rows, which write-back records; E needs sources, which nothing does).
"""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.gates import GateReport
from app.core.truth import ProductTruth
from app.memory.embeddings import HashingEncoder
from app.memory.recall import recall
from app.memory.scoring import load_scoring
from app.memory.writeback import MemoryStage, MemoryWrite
from app.orchestrator.adapters import writeback_step
from tests.db.conftest import Seed
from tests.db.test_writeback_commit import _run

pytestmark = [pytest.mark.anyio, pytest.mark.db]

ENC = HashingEncoder()
CFG = load_scoring()


@pytest.fixture
async def conn(db_url: str) -> AsyncIterator[AsyncConnection]:
    c = await AsyncConnection.connect(db_url)
    try:
        yield c
    finally:
        await c.rollback()
        await c.close()


@pytest.fixture
async def pool(db_url: str) -> AsyncIterator[AsyncConnectionPool[AsyncConnection]]:
    p: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        db_url, min_size=0, max_size=2, open=False
    )
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def test_an_approved_run_stages_no_new_memory_item(
    pool: AsyncConnectionPool[AsyncConnection], seed: Seed
) -> None:
    from uuid import uuid4

    state = {
        "run_id": str(uuid4()),
        "org_id": str(seed.org_a),
        "product_id": str(seed.product_a),
        "product": {"brand_id": str(seed.brand_a)},
        "approval": {"decision": "approve"},
        "personas": [],
    }
    report = GateReport((), (), "footwear", "v1", date(2026, 10, 31))
    step = writeback_step(pool, ENC)
    out = await step(state, MemoryStage(), report, ProductTruth(None, (), ()))  # type: ignore[arg-type]
    assert out["approved"] is True and out["staged"] == 0, "write-back stages nothing today"


async def test_an_item_that_is_written_carries_no_evidence_sources_so_e_is_left_out(
    conn: AsyncConnection, seed: Seed
) -> None:
    from app.memory.writeback import commit_memory

    run = await _run(conn, seed)
    content = "Waterproof knit uppers sell in the monsoon"
    stage = MemoryStage()
    stage.stage(
        MemoryWrite(
            org_id=seed.org_a,
            kind="insight",
            content=content,
            provenance=f"run:{run}",
            embedding=tuple(ENC.embed_documents([content])[0]),
            embedding_model=ENC.model,
            embedding_version=ENC.version,
            brand_id=seed.brand_a,
            product_id=seed.product_a,
            source_url="https://example.com/the-page-it-came-from",
            confidence=0.8,
        )
    )
    await commit_memory(conn, run, stage.all())

    row = await (
        await conn.execute(
            "select id, source_score, source_url from memory_items where provenance = %s",
            (f"run:{run}",),
        )
    ).fetchone()
    assert row is not None
    item, source_score, source_url = row
    assert source_url is not None, "a URL string is kept"
    assert source_score is None, "but no source row was recorded, so there is no source score"
    linked = await (
        await conn.execute(
            "select count(*) from memory_item_sources where memory_item_id = %s", (item,)
        )
    ).fetchone()
    assert linked is not None and linked[0] == 0

    found = await recall(
        conn,
        ENC,
        CFG,
        org_id=seed.org_a,
        query_text=content,
        product_id=str(seed.product_a),
        brand_id=str(seed.brand_a),
        product_category=None,
        sector=None,
        now=datetime.now(UTC),
        personas=None,
    )
    got = next(i for i in found.items if str(i.id) == str(item))
    assert "E" in got.left_out and "E" not in got.components, "E never arrives for a written item"
    assert isinstance(item, UUID)
