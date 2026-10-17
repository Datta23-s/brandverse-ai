"""The gates against the real registry rows (migration 0021's synthetic health-supplements rules),
loaded through app/core/truth.py, and a block written to audit_log.

Phases.md Phase 3 exit criteria 4 (regulated output missing a required disclaimer is blocked) and 5
(output containing a registry-prohibited phrasing is blocked), on the rules as stored.
"""

import json
from collections.abc import AsyncIterator
from datetime import date
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.gates import GateReport, Statement, evaluate, load_superlatives, record_gate_block
from app.core.truth import ProductTruth, load_product_truth

pytestmark = [pytest.mark.anyio, pytest.mark.db]

TODAY = date(2026, 10, 17)
ALWAYS = "Illustrative disclaimer (synthetic rule): consult a qualified professional before use."
PROTEIN = "Illustrative disclaimer (synthetic rule): protein content varies by serving size."


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


def _supplement_product(db_url: str) -> tuple[UUID, UUID, str]:
    """A fresh organisation (so the seed orgs other suites count stay as seeded) with a
    health-supplements brand and product, and one verified claim entered by its owner."""
    owner = uuid4()
    with psycopg.connect(db_url, autocommit=True) as c:
        c.execute("insert into auth.users (id) values (%s)", (owner,))
        org = c.execute("insert into organisations (name) values ('Gates') returning id").fetchone()
        assert org is not None
        c.execute(
            "insert into org_members (org_id, user_id, role) values (%s, %s, 'owner')",
            (org[0], owner),
        )
        brand = c.execute(
            "insert into brands (org_id, name, sector) values (%s, 'Greens', 'health-supplements') "
            "returning id",
            (org[0],),
        ).fetchone()
        assert brand is not None
        product = c.execute(
            "insert into products (org_id, brand_id, name) values (%s, %s, 'Greens Daily') "
            "returning id",
            (org[0], brand[0]),
        ).fetchone()
        assert product is not None
        with c.transaction():
            c.execute(
                "select set_config('role', 'authenticated', true), "
                "set_config('request.jwt.claims', %s, true)",
                (json.dumps({"sub": str(owner), "role": "authenticated"}),),
            )
            claim = c.execute(
                "insert into product_claims (org_id, product_id, claim, claim_type, evidence_url, "
                "created_by) values (%s, %s, '24 g of protein per scoop', 'verified', "
                "'https://example.invalid/label', 'human') returning id",
                (org[0], product[0]),
            ).fetchone()
            assert claim is not None
    return org[0], product[0], str(claim[0])


async def _truth(db_url: str, org: UUID, product: UUID) -> ProductTruth:
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        truth = await load_product_truth(conn, org, product)
    assert truth is not None
    return truth


async def _check(db_url: str, copy: dict[str, str]) -> GateReport:
    org, product, claim = _supplement_product(db_url)
    truth = await _truth(db_url, org, product)
    statements = (Statement("24 g of protein per scoop", claim),) if "24 g" in copy["body"] else ()
    return evaluate(copy, statements, truth, load_superlatives(), TODAY)


async def test_the_stored_rules_are_loaded_with_their_provenance(db_url: str) -> None:
    org, product, _ = _supplement_product(db_url)
    truth = await _truth(db_url, org, product)
    assert truth.sector is not None and truth.sector.rules_provenance == "illustrative"
    assert (
        sorted(r.kind for r in truth.rules)
        == ["prohibited_phrase"] * 2 + ["required_disclaimer"] * 2
    )


async def test_criterion_4_regulated_output_missing_a_required_disclaimer_is_blocked(
    db_url: str,
) -> None:
    report = await _check(db_url, {"body": "24 g of protein per scoop.", "footer": ALWAYS})
    assert [f.kind for f in report.findings] == ["missing_disclaimer"]  # the protein one
    assert report.outcome == "block"
    assert "illustrative rules" in report.sector


async def test_criterion_5_output_with_a_registry_prohibited_phrasing_is_blocked(
    db_url: str,
) -> None:
    copy = {"body": "Not a miracle cure, just greens. Guaranteed results.", "footer": ALWAYS}
    report = await _check(db_url, copy)
    assert [f.kind for f in report.findings] == ["prohibited_phrase", "prohibited_phrase"]
    assert report.outcome == "block"


async def test_regulated_output_meeting_every_rule_passes(db_url: str) -> None:
    copy = {"body": "24 g of protein per scoop.", "footer": f"{ALWAYS}\n{PROTEIN} "}
    report = await _check(db_url, copy)
    assert report.findings == ()
    assert report.outcome == "pass"
    assert [r.label for r in report.resolutions] == ["verified"]


async def test_a_block_is_written_to_audit_log(
    pool: AsyncConnectionPool[AsyncConnection], db_url: str
) -> None:
    org, product, _ = _supplement_product(db_url)
    truth = await _truth(db_url, org, product)
    report = evaluate({"body": "A miracle cure."}, (), truth, load_superlatives(), TODAY)
    await record_gate_block(pool, org_id=org, product_id=product, run_id=None, report=report)
    with psycopg.connect(db_url) as c:
        rows = c.execute(
            "select actor_type, actor_id, entity_type, details from audit_log "
            "where action = 'output.blocked' and entity_id = %s",
            (str(product),),
        ).fetchall()
    assert len(rows) == 1
    actor_type, actor_id, entity_type, details = rows[0]
    assert (actor_type, actor_id, entity_type) == ("service", "validation_engine", "product")
    assert {f["kind"] for f in details["findings"]} == {"prohibited_phrase", "missing_disclaimer"}
    assert "illustrative rules" in details["sector"]
    assert details["superlatives_version"] == "v1"
