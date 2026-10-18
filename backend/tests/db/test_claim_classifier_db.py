"""Phases.md Phase 3 exit criterion 2 — "An attempt to generate a restricted claim is blocked and
logged" — the logging half: validate_output appends the block to audit_log. Mocked model, real
database; the gates and the classifier are tested in tests/test_claim_classifier.py.
"""

import json
from collections.abc import AsyncIterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.claim_classifier import validate_output
from app.llm.ledger import CallContext
from tests.db.conftest import Seed
from tests.llm_fakes import FakeCompletion, prompt_library
from tests.test_claim_classifier import ALWAYS, FIRST, LEXICON, TODAY, WHEY, gateway, model_says

pytestmark = [pytest.mark.anyio, pytest.mark.db]


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


async def test_a_restricted_claim_in_output_is_blocked_and_logged(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    run_id, product_id = uuid4(), uuid4()
    gw, _ = gateway(FakeCompletion(model_says((FIRST, "velositol"))))
    report = await validate_output(
        gw,
        prompt_library(),
        pool,
        CallContext(org_id=seed.org_a, run_id=run_id, agent_id="test"),
        product_id=product_id,
        copy={"body": f"{FIRST}.", "footer": ALWAYS},
        truth=WHEY,
        lexicon=LEXICON,
        on=TODAY,
    )
    assert report.outcome == "block"
    with psycopg.connect(db_url) as c:
        rows = c.execute(
            "select actor_id, entity_type, details from audit_log "
            "where action = 'output.blocked' and entity_id = %s",
            (str(run_id),),
        ).fetchall()
    assert len(rows) == 1
    actor, entity_type, details = rows[0]
    assert (actor, entity_type) == ("validation_engine", "run")
    kinds = {f["kind"] for f in details["findings"]}
    assert "restricted_claim_in_copy" in kinds
    assert details["product_id"] == str(product_id)
    assert json.dumps(details).count("illustrative rules") >= 1  # the sector, with provenance


async def test_passing_copy_writes_no_audit_row(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str, seed: Seed
) -> None:
    from tests.test_claim_classifier import PROTEIN_FACTS, WHEY_COPY

    run_id = uuid4()
    gw, _ = gateway(FakeCompletion(model_says((PROTEIN_FACTS, "protein"))))
    report = await validate_output(
        gw,
        prompt_library(),
        pool,
        CallContext(org_id=seed.org_a, run_id=run_id),
        product_id=uuid4(),
        copy=WHEY_COPY,
        truth=WHEY,
        lexicon=LEXICON,
        on=TODAY,
    )
    assert report.outcome == "pass", report.findings
    with psycopg.connect(db_url) as c:
        row = c.execute(
            "select count(*) from audit_log where entity_id = %s", (str(run_id),)
        ).fetchone()
    assert row == (0,)
