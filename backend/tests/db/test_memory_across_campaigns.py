"""Memory across campaigns, through the real path (threat V13; Phase 6 chunk D).

The test that was missing: campaign 1's write-back (`writeback_step` with the deterministic
proposer, then `commit_memory`, the path a run's end takes) and then campaign 2 recalling what
campaign 1 left. Before the proposer, campaign 1 wrote nothing, so campaign 2 saw nothing; before
evidence sources, what was written had no `E`. Nothing here is inserted by hand.

What this does NOT show: that A2's output in a real run has these facts (those are tested with
A2), that the hashing encoder's similarity means anything, or that the M means anything for reuse
(chunk G). It shows the mechanism end to end."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.gates import GateReport
from app.core.truth import ProductTruth
from app.memory.embeddings import HashingEncoder
from app.memory.recall import recall
from app.memory.scoring import load_scoring
from app.memory.writeback import MemoryStage, commit_memory
from app.orchestrator.adapters import writeback_step
from tests.db.conftest import Seed
from tests.db.test_writeback_commit import _run

pytestmark = [pytest.mark.anyio, pytest.mark.db]

ENC = HashingEncoder()
CFG = load_scoring()
PERSONA = "Commuters, 22-30, find gear on video, objection: durability"
PAGE_A = "https://reviews.example/best-walking-shoes"
PAGE_B = "https://rival.example/monsoon"
INSIGHT = "Sellers in this category make returns easy and say so"
COMPETITOR = "rival.example: invites buyers to compare it with anyone"


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


def finding(url: str, score: float) -> dict[str, object]:
    return {
        "url": url,
        "title": "t",
        "score": score,
        "components": {"authority": 0.5, "recency": 0.9, "specificity": 0.75},
        "published": "2026-03-14",
        "first_party": False,
    }


def campaign_one(seed: Seed, run_id: object, *, approved: bool = True) -> dict[str, object]:
    return {
        "run_id": str(run_id),
        "org_id": str(seed.org_a),
        "product_id": str(seed.product_a),
        "product": {"brand_id": str(seed.brand_a)},
        "approval": {"decision": "approve" if approved else "reject"},
        "personas": [{"summary": PERSONA}],
        "findings": [finding(PAGE_A, 0.8), finding(PAGE_B, 0.7)],
        "agent_outputs": {
            "A2": {
                "memory_facts": [
                    {"kind": "insight", "text": INSIGHT, "source_url": PAGE_A},
                    {"kind": "competitor", "text": COMPETITOR, "source_url": PAGE_B},
                ]
            }
        },
    }


async def write_back(
    pool: AsyncConnectionPool[AsyncConnection],
    conn: AsyncConnection,
    seed: Seed,
    run_id: object,
    state: dict[str, object],
):  # type: ignore[no-untyped-def]
    stage = MemoryStage()
    report = GateReport((), (), "footwear", "v1", date(2026, 10, 31))
    out = await writeback_step(pool, ENC)(state, stage, report, ProductTruth(None, (), ()))
    done = await commit_memory(conn, run_id, stage.all())  # type: ignore[arg-type]
    return out, done


async def campaign_two(conn: AsyncConnection, seed: Seed, personas: list[str] | None):  # type: ignore[no-untyped-def]
    return await recall(
        conn,
        ENC,
        CFG,
        org_id=seed.org_a,
        query_text=INSIGHT,
        product_id=str(seed.product_a),
        brand_id=str(seed.brand_a),
        product_category=None,
        sector=None,
        now=datetime.now(UTC),
        personas=personas,
    )


async def test_campaign_two_recalls_what_campaign_one_left_with_all_six_components(
    pool: AsyncConnectionPool[AsyncConnection], conn: AsyncConnection, seed: Seed
) -> None:
    run = await _run(conn, seed)
    out, done = await write_back(pool, conn, seed, run, campaign_one(seed, run))
    assert out["proposed"] == 2 and out["staged"] == 2 and len(done.inserted) == 2

    found = await campaign_two(conn, seed, [PERSONA])
    mine = [i for i in found.items if i.content in (INSIGHT, COMPETITOR)]
    assert {i.content for i in mine} == {INSIGHT, COMPETITOR}
    for item in mine:
        assert set(item.components) == {"S", "P", "C", "A", "F", "E"}, item.left_out
        assert item.left_out == () and item.first_party_only is False
    insight = next(i for i in mine if i.kind == "insight")
    assert insight.components["E"] == pytest.approx(0.8 * CFG.writeback.evidence_item_confidence)
    assert insight.components["C"] == pytest.approx(1.0)  # campaign 1's persona, same encoder


async def test_without_the_proposer_there_would_have_been_nothing_to_recall(
    conn: AsyncConnection, seed: Seed
) -> None:
    """The V13 consequence: a run that wrote nothing leaves campaign 2 an empty store."""
    found = await campaign_two(conn, seed, [PERSONA])
    assert [i for i in found.items if i.content in (INSIGHT, COMPETITOR)] == []


async def test_a_campaign_that_was_not_approved_leaves_nothing(
    pool: AsyncConnectionPool[AsyncConnection], conn: AsyncConnection, seed: Seed
) -> None:
    run = await _run(conn, seed, status="rejected")
    out, done = await write_back(pool, conn, seed, run, campaign_one(seed, run, approved=False))
    assert out["proposed"] == 0 and out["staged"] == 0 and done.inserted == []
    found = await campaign_two(conn, seed, [PERSONA])
    assert [i for i in found.items if i.content in (INSIGHT, COMPETITOR)] == []


async def test_campaign_twos_own_proposal_reinforces_rather_than_duplicates(
    pool: AsyncConnectionPool[AsyncConnection], conn: AsyncConnection, seed: Seed
) -> None:
    first = await _run(conn, seed)
    await write_back(pool, conn, seed, first, campaign_one(seed, first))
    second = await _run(conn, seed)
    _, done = await write_back(pool, conn, seed, second, campaign_one(seed, second))
    assert done.inserted == [] and len(done.reinforced) == 2  # the same facts, counted once
