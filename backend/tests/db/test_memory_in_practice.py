"""What memory was, in practice, before candidate extraction: the characterization that decided V13.

Chunk C wired `C` and `E` into the M-score and proved it with rows inserted by hand. The owner's
check of 2026-10-31 asked whether any real run produces a six-component M. These tests pinned that
it did not, for two reasons:

1. `writeback_step` passed `candidates=[]`: nothing extracted facts from an approved campaign, so
   a real run wrote no memory item and campaigns 2 and 3 of a brand saw nothing from campaign 1.
2. Even an item written by `commit_memory` got only a `source_url` string, no `sources` or
   `memory_item_sources` row, so its `source_score` was NULL and `E` was left out of its M.

Both are now addressed (the deterministic proposer, app/memory/proposer.py; evidence recorded by
`commit_memory`), and tests/db/test_memory_across_campaigns.py shows campaign 2 recalling campaign 1
with all six components. What remains true, and is pinned here, is the NEGATIVE space: a run whose
A2 found no memory fact proposes and stages nothing, and an item written WITHOUT evidence still has
no `E`. Both would be wrong to "fix" by guessing.
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


async def test_an_approved_run_whose_a2_found_nothing_stages_no_memory_item(
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
    assert out["approved"] is True and out["proposed"] == 0 and out["staged"] == 0


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
