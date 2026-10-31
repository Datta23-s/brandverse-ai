"""A blocked gate report in audit_log keeps what the false-block review needs (pre-registered).

`citation_lacks_term` findings carry the statement and the text of the claim it cited, so after the
evaluation a sample can be judged by hand: does the claim in fact support the statement? The audit
log is append-only, so these rows are the record the review reads.
"""

import json
from collections.abc import AsyncIterator
from datetime import date

import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.gates import Finding, GateReport, record_gate_block
from tests.db.conftest import Seed
from tests.db.test_graph_checkpoints import new_run

pytestmark = pytest.mark.db


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


@pytest.mark.anyio
async def test_the_block_is_logged_with_the_statement_the_claim_and_the_round(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    run = new_run(db_url, seed)
    finding = Finding(
        "citation_lacks_term",
        "the cited claim does not mention 'lightweight'",
        ("c3",),
        span="lightweight walking shoe",
        statement="lightweight walking shoe",
        claim_text="Lace-up, low top, medium width, almond toe, low arch",
    )
    report = GateReport((finding,), (), "footwear", "v1", date(2026, 10, 31))
    await record_gate_block(
        pool,
        org_id=seed.org_a,
        product_id=seed.product_a,
        run_id=run,
        report=report,
        extra={"revision_round": 1},
    )
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "select details from audit_log where entity_id = %s and action = 'output.blocked'",
            (str(run),),
        ).fetchone()
    assert row is not None
    details = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    assert details["revision_round"] == 1
    (stored,) = details["findings"]
    assert stored["kind"] == "citation_lacks_term"
    assert stored["statement"] == "lightweight walking shoe"
    assert stored["claim_text"] == "Lace-up, low top, medium width, almond toe, low arch"
