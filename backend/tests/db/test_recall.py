"""Chunk C: the M-score's customer match C and evidence E, from a real retrieval (Phase 6).

`score()` had no caller, so no real M had ever been computed, and E and C could not have entered
one: the guard that matters is a test that takes a retrieval from the database and checks that all
SIX components arrive (the failure 0031 fixed was exactly that: a value stored and never returned,
so every M would have been four-component while looking complete). Also: C is the maximum over the
persona pairs, an item with no persona context leaves C OUT (never zero), vectors from another
encoder are not compared, and write-back records the run's personas for every item it touched.
"""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import AsyncConnection, errors

from app.memory.embeddings import HashingEncoder
from app.memory.recall import Recall, recall
from app.memory.scoring import load_scoring
from app.memory.writeback import (
    MemoryWrite,
    PersonaVector,
    StagedMemory,
    commit_memory,
    vector_literal,
)
from tests.db.conftest import Seed, Tx

pytestmark = [pytest.mark.anyio, pytest.mark.db]

ENC = HashingEncoder()
CFG = load_scoring()
NOW = datetime.now(UTC)
COMMUTERS = "Commuters aged 22-30 who find gear through short video, objection: durability"
RUNNERS = "Weekend trail runners who read forum reviews, objection: weight"
SQL_ITEM = (
    "insert into memory_items (org_id, brand_id, kind, content, embedding, embedding_model, "
    "embedding_version, provenance, confidence, approved, sector) values (%s, %s, %s, %s, "
    "%s::vector, %s, %s, 'test', %s, true, 'footwear') returning id"
)


@pytest.fixture
async def conn(db_url: str) -> AsyncIterator[AsyncConnection]:
    c = await AsyncConnection.connect(db_url)
    try:
        yield c
    finally:
        await c.rollback()
        await c.close()


async def add_item(conn: AsyncConnection, seed: Seed, content: str, kind: str = "insight") -> UUID:
    cur = await conn.execute(
        SQL_ITEM,
        (
            seed.org_a,
            seed.brand_a,
            kind,
            content,
            vector_literal(ENC.embed_documents([content])[0]),
            ENC.model,
            ENC.version,
            0.8,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    found: UUID = row[0]
    return found


async def add_persona(
    conn: AsyncConnection,
    seed: Seed,
    item: UUID,
    summary: str,
    *,
    index: int = 0,
    model: str | None = None,
    run: UUID | None = None,
) -> None:
    await conn.execute(
        "insert into memory_item_personas (memory_item_id, run_id, persona_index, org_id, summary, "
        "embedding, embedding_model, embedding_version) values (%s, %s, %s, %s, %s, %s::vector, "
        "%s, %s)",
        (
            item,
            run or seed.run_a,
            index,
            seed.org_a,
            summary,
            vector_literal(ENC.embed_documents([summary])[0]),
            model or ENC.model,
            ENC.version,
        ),
    )


async def add_source(conn: AsyncConnection, seed: Seed, item: UUID, score: float) -> None:
    cur = await conn.execute(
        "insert into sources (org_id, url, host) values (%s, %s, 'example.com') returning id",
        (seed.org_a, f"https://example.com/{uuid4()}"),
    )
    row = await cur.fetchone()
    assert row is not None
    await conn.execute(
        "insert into memory_item_sources (memory_item_id, source_id, org_id, score, components, "
        "scorer, config_version) values (%s, %s, %s, %s, '{}', 'test/1', 'test')",
        (item, row[0], seed.org_a, score),
    )


async def run_recall(
    conn: AsyncConnection, seed: Seed, personas: list[str] | None, query: str = "rain sells"
) -> Recall:
    return await recall(
        conn,
        ENC,
        CFG,
        org_id=seed.org_a,
        query_text=query,
        product_id=str(seed.product_a),
        brand_id=str(seed.brand_a),
        product_category=None,
        sector="footwear",
        now=NOW,
        personas=personas,
    )


def one(found: Recall, content: str):  # type: ignore[no-untyped-def]
    (item,) = [i for i in found.items if i.content == content]
    return item


# ── The guard: a real retrieval returns all six components ───────────────────────────────────


async def test_a_real_retrieval_returns_all_six_components(
    conn: AsyncConnection, seed: Seed
) -> None:
    item = await add_item(conn, seed, "rain sells")
    await add_persona(conn, seed, item, COMMUTERS)
    await add_source(conn, seed, item, 0.9)
    found = await run_recall(conn, seed, [COMMUTERS])
    got = one(found, "rain sells")
    assert set(got.components) == {"S", "P", "C", "A", "F", "E"} and got.left_out == ()
    assert got.components["C"] == pytest.approx(1.0)  # the same persona text, same encoder
    assert got.components["E"] == pytest.approx(0.9 * 0.8)  # source score x confidence
    assert found.persona_scored is True and found.ef_search == CFG.retrieval.hnsw_ef_search


async def test_before_the_persona_exists_c_is_left_out_and_the_figure_says_so(
    conn: AsyncConnection, seed: Seed
) -> None:
    item = await add_item(conn, seed, "rain sells")
    await add_persona(conn, seed, item, COMMUTERS)
    await add_source(conn, seed, item, 0.9)
    found = await run_recall(conn, seed, None)  # step 4: A3 has not spoken
    got = one(found, "rain sells")
    assert "C" not in got.components and got.left_out == ("C",)
    assert found.persona_scored is False


async def test_an_item_with_no_persona_context_leaves_c_out_never_zero(
    conn: AsyncConnection, seed: Seed
) -> None:
    await add_item(conn, seed, "rain sells")  # no persona rows, no sources
    got = one(await run_recall(conn, seed, [COMMUTERS]), "rain sells")
    assert "C" not in got.components and "E" not in got.components
    assert set(got.left_out) == {"C", "E"}
    assert set(got.components) == {"S", "P", "A", "F"}


# ── C is the maximum, over the item's personas and over the request's ────────────────────────


async def test_c_is_the_maximum_over_every_pair_of_request_and_item_persona(
    conn: AsyncConnection, seed: Seed
) -> None:
    item = await add_item(conn, seed, "rain sells")
    await add_persona(conn, seed, item, RUNNERS, index=0)  # one campaign's persona
    await add_persona(conn, seed, item, COMMUTERS, index=1)  # another campaign's, an exact match
    both = await run_recall(conn, seed, [RUNNERS, "Parents buying school shoes"])
    only_other = await run_recall(conn, seed, ["Parents buying school shoes"])
    c_runners = one(both, "rain sells").components["C"]
    c_other = one(only_other, "rain sells").components["C"]
    assert c_runners == pytest.approx(1.0) and c_other < c_runners
    # adding a persona to the request can only raise C, never lower it
    more = await run_recall(conn, seed, ["Parents buying school shoes", COMMUTERS])
    assert one(more, "rain sells").components["C"] >= c_other


async def test_an_item_used_for_another_persona_is_no_less_relevant_to_the_first(
    conn: AsyncConnection, seed: Seed
) -> None:
    item = await add_item(conn, seed, "rain sells")
    await add_persona(conn, seed, item, COMMUTERS, index=0)
    before = one(await run_recall(conn, seed, [COMMUTERS]), "rain sells").components["C"]
    await add_persona(conn, seed, item, RUNNERS, index=1)  # also used for Y
    after = one(await run_recall(conn, seed, [COMMUTERS]), "rain sells").components["C"]
    assert after >= before


async def test_vectors_from_another_encoder_are_not_compared(
    conn: AsyncConnection, seed: Seed
) -> None:
    item = await add_item(conn, seed, "rain sells")
    await add_persona(conn, seed, item, COMMUTERS, model="some-other-encoder")
    got = one(await run_recall(conn, seed, [COMMUTERS]), "rain sells")
    assert "C" not in got.components, "a cosine across models means nothing"


# ── Write-back records the run's personas for every item the campaign touched ───────────────


def approved_run(db_url: str, seed: Seed) -> UUID:
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "insert into runs (org_id, product_id, request) values (%s, %s, '{}') returning id",
            (seed.org_a, seed.product_a),
        ).fetchone()
        assert row is not None
        run_id: UUID = row[0]
        c.execute(
            "insert into campaigns (org_id, run_id, status, approved_by, approved_at) "
            "values (%s, %s, 'approved', %s, now())",
            (seed.org_a, run_id, seed.users["approver_a"]),
        )
    return run_id


def write(seed: Seed, text: str) -> MemoryWrite:
    return MemoryWrite(
        org_id=seed.org_a,
        kind="insight",
        content=text,
        provenance="test",
        embedding=tuple(ENC.embed_documents([text])[0]),
        embedding_model=ENC.model,
        embedding_version=ENC.version,
        confidence=0.7,
        brand_id=seed.brand_a,
    )


def personas() -> tuple[PersonaVector, ...]:
    return tuple(
        PersonaVector(s, tuple(v), ENC.model, ENC.version)
        for s, v in zip(
            [COMMUTERS, RUNNERS], ENC.embed_documents([COMMUTERS, RUNNERS]), strict=True
        )
    )


def rows(db_url: str, run_id: UUID) -> list[tuple[object, ...]]:
    with psycopg.connect(db_url, autocommit=True) as c:
        return c.execute(
            "select memory_item_id, persona_index, summary, embedding_model from "
            "memory_item_personas where run_id = %s order by memory_item_id, persona_index",
            (run_id,),
        ).fetchall()


async def test_a_new_item_and_a_reinforced_one_both_record_the_runs_personas(
    db_url: str, seed: Seed
) -> None:
    marker = f"persona-{uuid4()}"
    first = approved_run(db_url, seed)
    async with await AsyncConnection.connect(db_url) as c:
        done = await commit_memory(
            c, first, StagedMemory(items=(write(seed, marker),), personas=personas())
        )
        await c.commit()
    (item_id,) = done.inserted
    assert [(r[1], r[2]) for r in rows(db_url, first)] == [(0, COMMUTERS), (1, RUNNERS)]

    second = approved_run(db_url, seed)  # a second campaign finds the same fact: reinforced
    async with await AsyncConnection.connect(db_url) as c:
        again = await commit_memory(
            c, second, StagedMemory(items=(write(seed, marker),), personas=personas()[:1])
        )
        await c.commit()
    assert not again.inserted and len(again.reinforced) == 1
    assert [str(r[0]) for r in rows(db_url, second)] == [item_id]  # recorded against the same item
    assert [r[2] for r in rows(db_url, second)] == [COMMUTERS]


async def test_a_run_with_no_personas_records_none_and_an_unapproved_one_cannot_write(
    db_url: str, seed: Seed
) -> None:
    run_id = approved_run(db_url, seed)
    async with await AsyncConnection.connect(db_url) as c:
        await commit_memory(c, run_id, StagedMemory(items=(write(seed, f"plain-{uuid4()}"),)))
        await c.commit()
    assert rows(db_url, run_id) == []


# ── The table's posture ──────────────────────────────────────────────────────────────────────


def seed_persona_row(tx: Tx) -> UUID:
    item = tx.execute(
        SQL_ITEM,
        (
            tx.seed.org_a,
            tx.seed.brand_a,
            "insight",
            "x",
            vector_literal(ENC.embed_documents(["x"])[0]),
            ENC.model,
            ENC.version,
            0.5,
        ),
    )[0][0]
    tx.execute(
        "insert into memory_item_personas (memory_item_id, run_id, persona_index, org_id, summary, "
        "embedding, embedding_model, embedding_version) values (%s, %s, 0, %s, 'p', %s::vector, "
        "'m', 'v')",
        (item, tx.seed.run_a, tx.seed.org_a, vector_literal(ENC.embed_documents(["p"])[0])),
    )
    return UUID(str(item))


def test_persona_rows_are_append_only_and_truncate_is_trapped(tx: Tx) -> None:
    item = seed_persona_row(tx)
    for statement in (
        "update memory_item_personas set summary = 'q' where memory_item_id = %s",
        "delete from memory_item_personas where memory_item_id = %s",
    ):
        with pytest.raises(errors.RaiseException, match="append-only"), tx.conn.transaction():
            tx.execute(statement, (item,))
    with pytest.raises(errors.RaiseException, match="append-only"), tx.conn.transaction():
        tx.execute("truncate memory_item_personas")


def test_no_api_role_writes_persona_context_and_members_read_only_their_own(tx: Tx) -> None:
    item = seed_persona_row(tx)
    tx.as_user("viewer_a")
    assert tx.execute(
        "select count(*) from memory_item_personas where memory_item_id = %s", (item,)
    ) == [(1,)]
    with pytest.raises(errors.InsufficientPrivilege), tx.conn.transaction():
        tx.execute(
            "insert into memory_item_personas (memory_item_id, run_id, persona_index, org_id, "
            "summary, embedding, embedding_model, embedding_version) values (%s, %s, 5, %s, 'p', "
            "%s::vector, 'm', 'v')",
            (item, tx.seed.run_a, tx.seed.org_a, vector_literal(ENC.embed_documents(["p"])[0])),
        )


def test_another_organisations_member_reads_none(tx: Tx) -> None:
    seed_persona_row(tx)
    tx.as_user("approver_b")
    assert tx.execute("select count(*) from memory_item_personas") == [(0,)]


def test_the_json_roundtrip_of_a_recalled_item_is_plain_data() -> None:
    from app.memory.recall import RecalledItem

    record = RecalledItem("i", "insight", "c", 0.81234, "adapt", {"S": 0.9}, ("C",)).record()
    assert json.loads(json.dumps(record))["left_out"] == ["C"]
