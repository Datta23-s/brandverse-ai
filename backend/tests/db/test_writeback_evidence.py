"""Write-back attaches evidence sources (Phase 6 chunk D, threat V6; owner 2026-10-31).

Before this, nothing wrote `sources` or `memory_item_sources`: an item got a `source_url` string and
a NULL `source_score`, so `E` was left out of every M and the six-component M existed only in a
test that inserted its rows by hand (tests/db/test_memory_in_practice.py pins that). These tests go
through `commit_memory`, the path a run's end takes, and check that the evidence is recorded with
its score and `first_party`, that it lifts the item's `source_score`, and that `E` then ARRIVES in
recall. What decides WHICH facts a run proposes, and which page backs each, is still not built.
"""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from psycopg import AsyncConnection

from app.memory.embeddings import HashingEncoder
from app.memory.recall import recall
from app.memory.scoring import load_scoring
from app.memory.writeback import (
    EvidenceSource,
    MemoryStage,
    commit_memory,
    evidence_from_finding,
)
from tests.db.conftest import Seed
from tests.db.test_writeback_commit import _existing, _item, _run

pytestmark = [pytest.mark.anyio, pytest.mark.db]

ENC = HashingEncoder()
CFG = load_scoring()
PAGE = "https://reviews.example/best-walking-shoes"
OWN = "https://neemans.com/products/begin-walk-lite"


def evidence(
    url: str = PAGE, score: float = 0.8, first_party: bool | None = False
) -> EvidenceSource:
    return EvidenceSource(
        url=url,
        score=score,
        components={"authority": 0.5, "recency": 0.9, "specificity": 0.75},
        scorer="source_scoring/1",
        config_version="defaults-2026-10-18",
        published_on=date(2026, 3, 14),
        first_party=first_party,
    )


@pytest.fixture
async def conn(db_url: str) -> AsyncIterator[AsyncConnection]:
    c = await AsyncConnection.connect(db_url)
    try:
        yield c
    finally:
        await c.rollback()
        await c.close()


async def row(conn: AsyncConnection, query: str, *params: object) -> tuple[object, ...]:
    found = await (await conn.execute(query, params)).fetchone()
    assert found is not None
    return tuple(found)


async def recalled(conn: AsyncConnection, seed: Seed, content: str, item: UUID):  # type: ignore[no-untyped-def]
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
    return next(i for i in found.items if str(i.id) == str(item))


# ── A written item keeps its evidence, and E arrives ─────────────────────────────────────────


async def test_a_new_item_is_written_with_its_sources_and_e_arrives_in_recall(
    conn: AsyncConnection, seed: Seed
) -> None:
    run = await _run(conn, seed)
    content = "Reviewers rank knit uppers highest for hot weather commuting"
    stage = MemoryStage()
    from dataclasses import replace

    stage.stage(replace(_item(seed, content, confidence=0.8), evidence=(evidence(score=0.9),)))
    done = await commit_memory(conn, run, stage.all())
    (item,) = (UUID(i) for i in done.inserted)

    score, first_party_only = await row(
        conn,
        "select source_score, evidence_first_party_only from memory_items where id = %s",
        item,
    )
    assert float(str(score)) == pytest.approx(0.9)
    assert first_party_only is False  # the page is not the brand's own
    src = await row(
        conn,
        "select s.url, s.host, s.published_on, l.score, l.first_party, l.scorer "
        "from memory_item_sources l join sources s on s.id = l.source_id "
        "where l.memory_item_id = %s",
        item,
    )
    assert src[:3] == (PAGE, "reviews.example", date(2026, 3, 14))
    assert (float(str(src[3])), src[4], src[5]) == (pytest.approx(0.9), False, "source_scoring/1")

    got = await recalled(conn, seed, content, item)
    assert "E" in got.components and "E" not in got.left_out, "E now ARRIVES for a written item"
    assert got.components["E"] == pytest.approx(0.9 * 0.8)  # source score x confidence
    assert got.first_party_only is False


async def test_an_item_whose_only_source_is_the_brands_own_page_is_marked_so(
    conn: AsyncConnection, seed: Seed
) -> None:
    run = await _run(conn, seed)
    stage = MemoryStage()
    from dataclasses import replace

    stage.stage(
        replace(_item(seed, "Knit upper, medium width"), evidence=(evidence(OWN, 0.7, True),))
    )
    done = await commit_memory(conn, run, stage.all())
    (flag,) = await row(
        conn,
        "select evidence_first_party_only from memory_items where id = %s",
        UUID(done.inserted[0]),
    )
    assert flag is True


async def test_an_item_with_no_evidence_still_has_no_e(conn: AsyncConnection, seed: Seed) -> None:
    run = await _run(conn, seed)
    stage = MemoryStage()
    stage.stage(_item(seed, "A fact written without any source page"))
    done = await commit_memory(conn, run, stage.all())
    item = UUID(done.inserted[0])
    got = await recalled(conn, seed, "A fact written without any source page", item)
    assert "E" in got.left_out and got.first_party_only is None  # unknown, never independent


# ── Reinforcement adds evidence; sources are shared; the same link is made once ──────────────


async def test_reinforcing_a_near_duplicate_adds_the_new_evidence_to_the_existing_item(
    conn: AsyncConnection, seed: Seed
) -> None:
    from dataclasses import replace

    existing = await _existing(conn, seed, "Rubber and EVA soles last longer on pavement")
    run = await _run(conn, seed)
    stage = MemoryStage()
    stage.stage(
        replace(
            _item(seed, "rubber and eva soles last longer on pavement"),
            evidence=(evidence(score=0.6),),
        )
    )
    done = await commit_memory(conn, run, stage.all())
    assert done.inserted == [] and len(done.reinforced) == 1  # a near-duplicate, not a new row
    (score,) = await row(conn, "select source_score from memory_items where id = %s", existing)
    assert float(str(score)) == pytest.approx(0.6)


async def test_the_same_page_is_one_source_row_for_the_organisation(
    conn: AsyncConnection, seed: Seed
) -> None:
    from dataclasses import replace

    run = await _run(conn, seed)
    stage = MemoryStage()
    stage.stage(
        replace(_item(seed, "First distinct fact about hot weather"), evidence=(evidence(),))
    )
    stage.stage(replace(_item(seed, "Second fact about monsoon grip"), evidence=(evidence(),)))
    done = await commit_memory(conn, run, stage.all())
    assert len(done.inserted) == 2
    (sources,) = await row(conn, "select count(*) from sources where url = %s", PAGE)
    (links,) = await row(
        conn,
        "select count(*) from memory_item_sources l join sources s on s.id = l.source_id "
        "where s.url = %s",
        PAGE,
    )
    assert (sources, links) == (1, 2)


async def test_the_same_page_twice_for_one_item_is_recorded_once(
    conn: AsyncConnection, seed: Seed
) -> None:
    from dataclasses import replace

    run = await _run(conn, seed)
    stage = MemoryStage()
    stage.stage(
        replace(_item(seed, "Fact backed twice by one page"), evidence=(evidence(), evidence()))
    )
    done = await commit_memory(conn, run, stage.all())
    (links,) = await row(
        conn,
        "select count(*) from memory_item_sources where memory_item_id = %s",
        UUID(done.inserted[0]),
    )
    assert links == 1


async def test_the_audit_row_counts_the_evidence_it_recorded(
    conn: AsyncConnection, seed: Seed
) -> None:
    from dataclasses import replace

    run = await _run(conn, seed)
    stage = MemoryStage()
    stage.stage(replace(_item(seed, "A fact with one source"), evidence=(evidence(),)))
    await commit_memory(conn, run, stage.all())
    (details,) = await row(
        conn,
        "select details from audit_log where action = 'memory.writeback' and entity_id = %s",
        str(run),
    )
    assert isinstance(details, dict) and details["evidence_recorded"] == 1


# ── From a run's finding ─────────────────────────────────────────────────────────────────────


def test_a_finding_becomes_evidence_with_its_components_and_first_party() -> None:
    finding = {
        "url": OWN,
        "score": 0.72,
        "components": {"authority": 0.5, "specificity": 0.9},
        "published": "2026-05-01",
        "first_party": True,
    }
    e = evidence_from_finding(finding, "defaults-2026-10-18")
    assert (e.url, e.score, e.first_party, e.published_on) == (OWN, 0.72, True, date(2026, 5, 1))
    assert e.components == {"authority": 0.5, "specificity": 0.9} and e.host == "neemans.com"


def test_a_finding_without_components_cannot_be_recorded() -> None:
    with pytest.raises(KeyError):
        evidence_from_finding({"url": PAGE, "score": 0.5}, "v")


def test_evidence_is_checked_when_it_is_made() -> None:
    with pytest.raises(ValueError, match="http"):
        EvidenceSource("ftp://x.example/a", 0.5, {}, "s", "v")
    with pytest.raises(ValueError, match="score"):
        EvidenceSource("https://x.example/a", 1.5, {}, "s", "v")
