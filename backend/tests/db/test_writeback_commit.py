"""Write-back's commit half, inside the run's terminal transaction (app/memory/writeback.py;
Memory.md §6.1 rules 1 and 4, §6.4).

Each test works in one transaction that is always rolled back, except the concurrency test, which
must commit one side. Contents carry a per-test marker, so a committed row is never a
near-duplicate of another test's. The encoder is the hashing stand-in: case and spacing do not
change its vector, so "the same fact, written differently" has cosine 1.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection

from app.memory.embeddings import HashingEncoder
from app.memory.writeback import MemoryWrite, WritebackRefused, commit_memory, vector_literal
from tests.db.conftest import Seed

pytestmark = [pytest.mark.anyio, pytest.mark.db]

ENCODER = HashingEncoder()


@pytest.fixture
async def conn(db_url: str) -> AsyncIterator[AsyncConnection]:
    """A new connection whose transaction is always rolled back."""
    c = await AsyncConnection.connect(db_url)
    try:
        yield c
    finally:
        await c.rollback()
        await c.close()


async def _one(conn: AsyncConnection, query: str, params: tuple[object, ...]) -> tuple[object, ...]:
    row = await (await conn.execute(query, params)).fetchone()
    assert row is not None
    return tuple(row)


async def _run(conn: AsyncConnection, seed: Seed, status: str = "approved") -> UUID:
    (run_id,) = await _one(
        conn,
        "insert into runs (org_id, product_id, request) values (%s, %s, '{}') returning id",
        (seed.org_a, seed.product_a),
    )
    approved = status == "approved"
    await conn.execute(
        "insert into campaigns (org_id, run_id, status, approved_by, approved_at) "
        "values (%s, %s, %s, %s, case when %s then now() end)",
        (seed.org_a, run_id, status, seed.users["approver_a"] if approved else None, approved),
    )
    assert isinstance(run_id, UUID)
    return run_id


def _item(
    seed: Seed,
    content: str,
    *,
    kind: str = "insight",
    brand: UUID | None = None,
    claim: UUID | None = None,
    confidence: float | None = None,
) -> MemoryWrite:
    return MemoryWrite(
        org_id=seed.org_a,
        kind=kind,
        content=content,
        provenance="test",
        embedding=tuple(ENCODER.embed_documents([content])[0]),
        embedding_model=ENCODER.model,
        embedding_version=ENCODER.version,
        brand_id=brand or seed.brand_a,
        product_id=seed.product_a,
        claim_id=claim,
        confidence=confidence,
    )


async def _existing(
    conn: AsyncConnection,
    seed: Seed,
    content: str,
    *,
    brand: UUID | None = None,
    kind: str = "insight",
    version: str = ENCODER.version,
    confidence: float | None = 0.5,
    revoked: bool = False,
    is_seed: bool = False,
) -> UUID:
    (item_id,) = await _one(
        conn,
        "insert into memory_items (org_id, brand_id, kind, content, embedding, embedding_model, "
        "embedding_version, provenance, confidence, approved, revoked, revoked_by, revoked_at, "
        "revoked_reason, is_seed) values (%s, %s, %s, %s, %s::vector, %s, %s, 'human', %s, true, "
        "%s, %s, case when %s then now() end, %s, %s) returning id",
        (
            seed.org_a,
            brand or seed.brand_a,
            kind,
            content,
            vector_literal(ENCODER.embed_documents([content])[0]),
            ENCODER.model,
            version,
            confidence,
            revoked,
            seed.users["owner_a"] if revoked else None,
            revoked,
            "wrong" if revoked else None,
            is_seed,
        ),
    )
    assert isinstance(item_id, UUID)
    return item_id


async def _count(conn: AsyncConnection, content: str) -> int:
    (n,) = await _one(
        conn, "select count(*) from memory_items where lower(content) = lower(%s)", (content,)
    )
    assert isinstance(n, int)
    return n


def _fact() -> str:
    return f"Founder-story copy outperforms spec-sheet copy {uuid4().hex}"


# ── Insert, and the audit row ────────────────────────────────────────────────────────────────


async def test_a_new_fact_is_inserted_with_its_encoder_and_audited(
    conn: AsyncConnection, seed: Seed
) -> None:
    run_id, fact = await _run(conn, seed), _fact()
    done = await commit_memory(conn, run_id, [_item(seed, fact, claim=seed.claim_a)])
    [inserted] = done.inserted
    assert done.reinforced == done.skipped == []
    row = await _one(
        conn,
        "select content, embedding_model, embedding_version, approved, claim_id, revoked, "
        "is_seed, embedding = %s::vector from memory_items where id = %s",
        (vector_literal(ENCODER.embed_documents([fact])[0]), inserted),
    )
    assert row == (fact, ENCODER.model, ENCODER.version, True, seed.claim_a, False, False, True)
    actor_type, actor_id, action, details = await _one(
        conn,
        "select actor_type, actor_id, action, details from audit_log "
        "where entity_type = 'run' and entity_id = %s",
        (str(run_id),),
    )
    assert (actor_type, actor_id, action) == ("service", "write_memory", "memory.writeback")
    assert isinstance(details, dict)
    assert details["inserted"] == [inserted]
    assert details["near_duplicate_cosine"] == 0.95


# ── Near-duplicates (§6.1 rule 4) ─────────────────────────────────────────────────────────────


async def test_a_near_duplicate_of_a_live_item_reinforces_it_instead_of_inserting(
    conn: AsyncConnection, seed: Seed
) -> None:
    fact = _fact()
    existing = await _existing(conn, seed, fact, confidence=0.5)
    run_id = await _run(conn, seed)
    done = await commit_memory(conn, run_id, [_item(seed, fact.upper())])
    assert done.inserted == []
    assert done.reinforced == [
        {"content": fact.upper(), "near": str(existing), "cosine": 1.0, "confidence": 0.52}
    ]
    assert await _count(conn, fact) == 1
    confidence, used = await _one(
        conn,
        "select confidence, last_used_at is not null from memory_items where id = %s",
        (existing,),
    )
    assert (float(str(confidence)), used) == (0.52, True)


@pytest.mark.parametrize(("before", "after"), [(0.98, 0.99), (0.99, 0.99), (None, None)])
async def test_reinforcement_stops_at_the_cap_and_a_null_confidence_stays_null(
    conn: AsyncConnection, seed: Seed, before: float | None, after: float | None
) -> None:
    fact = _fact()
    await _existing(conn, seed, fact, confidence=before)
    done = await commit_memory(conn, await _run(conn, seed), [_item(seed, fact)])
    assert [r["confidence"] for r in done.reinforced] == [after]


async def test_a_near_duplicate_of_a_revoked_item_is_never_written_again(
    conn: AsyncConnection, seed: Seed
) -> None:
    # Revocation is permanent (§6.4): the same fact under a new id would undo it. A revoked
    # near-duplicate decides even when a live one is also there.
    fact = _fact()
    await _existing(conn, seed, fact)
    revoked = await _existing(conn, seed, fact, revoked=True)
    done = await commit_memory(conn, await _run(conn, seed), [_item(seed, fact)])
    assert done.inserted == done.reinforced == []
    assert [(s["near"], s["reason"]) for s in done.skipped] == [
        (str(revoked), "near_duplicate_of_a_revoked_item")
    ]
    assert await _count(conn, fact) == 2


async def test_one_write_back_counts_a_fact_once(conn: AsyncConnection, seed: Seed) -> None:
    new, old = _fact(), _fact()
    await _existing(conn, seed, old, confidence=0.5)
    done = await commit_memory(
        conn,
        await _run(conn, seed),
        [_item(seed, new), _item(seed, new.lower()), _item(seed, old), _item(seed, old.lower())],
    )
    assert len(done.inserted) == 1
    assert [r["confidence"] for r in done.reinforced] == [0.52]  # +0.02 once, not twice
    assert [s["reason"] for s in done.skipped] == ["duplicate_within_this_writeback"] * 2
    assert await _count(conn, new) == 1


@pytest.mark.parametrize(
    "difference",
    ["other_brand", "other_kind", "other_encoder_revision", "seed_item"],
)
async def test_only_items_of_the_same_brand_kind_and_encoder_revision_are_compared(
    conn: AsyncConnection, seed: Seed, difference: str
) -> None:
    # Seed memory stays apart from what runs write (§7.1); vectors from another encoder revision
    # are not comparable (§5).
    fact = _fact()
    (other_brand,) = await _one(
        conn,
        "insert into brands (org_id, name) values (%s, 'BA2') returning id",
        (seed.org_a,),
    )
    assert isinstance(other_brand, UUID)
    await _existing(
        conn,
        seed,
        fact,
        brand=other_brand if difference == "other_brand" else None,
        kind="persona" if difference == "other_kind" else "insight",
        version="0" if difference == "other_encoder_revision" else ENCODER.version,
        is_seed=difference == "seed_item",
    )
    done = await commit_memory(conn, await _run(conn, seed), [_item(seed, fact)])
    assert len(done.inserted) == 1
    assert done.reinforced == done.skipped == []


# ── What can change between staging and the end ──────────────────────────────────────────────


async def test_a_claim_retired_since_staging_is_skipped(conn: AsyncConnection, seed: Seed) -> None:
    # A claim is entered and retired by a signed-in user (0017).
    await conn.execute("set local role authenticated")
    await conn.execute(
        "select set_config('request.jwt.claims', %s, true)",
        (json.dumps({"sub": str(seed.users["owner_a"]), "role": "authenticated"}),),
    )
    (claim,) = await _one(
        conn,
        "insert into product_claims (org_id, product_id, claim, claim_type, evidence_url, "
        "created_by) values (%s, %s, 'Sole is stitched', 'verified', 'https://e.invalid/s', "
        "'human') returning id",
        (seed.org_a, seed.product_a),
    )
    await conn.execute(
        "update product_claims set retired_reason = 'supplier changed' where id = %s", (claim,)
    )
    await conn.execute("reset role")
    assert isinstance(claim, UUID)
    fact = _fact()
    done = await commit_memory(conn, await _run(conn, seed), [_item(seed, fact, claim=claim)])
    assert done.inserted == done.reinforced == []
    assert [s["reason"] for s in done.skipped] == ["claim_no_longer_verified"]
    assert await _count(conn, fact) == 0


@pytest.mark.parametrize("status", ["draft", "awaiting_approval", "rejected"])
async def test_a_campaign_not_approved_by_a_human_refuses_the_whole_commit(
    conn: AsyncConnection, seed: Seed, status: str
) -> None:
    fact = _fact()
    with pytest.raises(WritebackRefused, match="no approved campaign"):
        await commit_memory(conn, await _run(conn, seed, status), [_item(seed, fact)])


async def test_an_item_naming_another_organisation_refuses_the_whole_commit(
    conn: AsyncConnection, seed: Seed
) -> None:
    foreign = replace(_item(seed, _fact()), org_id=seed.org_b)
    with pytest.raises(WritebackRefused, match="names org"):
        await commit_memory(conn, await _run(conn, seed), [_item(seed, _fact()), foreign])


async def test_concurrent_write_backs_of_one_organisation_cannot_both_insert_a_fact(
    db_url: str, seed: Seed
) -> None:
    # Without the advisory lock both would find no near-duplicate and both insert. With it the
    # second waits for the first to commit, then finds its row and reinforces it.
    fact = _fact()
    first = await AsyncConnection.connect(db_url)
    second = await AsyncConnection.connect(db_url)
    try:
        run_1, run_2 = await _run(first, seed), await _run(second, seed)
        done_1 = await commit_memory(first, run_1, [_item(seed, fact, confidence=0.5)])
        waiting = asyncio.create_task(commit_memory(second, run_2, [_item(seed, fact)]))
        await asyncio.sleep(0.5)
        assert not waiting.done()  # blocked on the lock, not racing
        await first.commit()
        done_2 = await asyncio.wait_for(waiting, timeout=10)
        assert done_2.inserted == []
        assert [r["near"] for r in done_2.reinforced] == done_1.inserted
    finally:
        await second.rollback()
        await first.rollback()
        await second.close()
        await first.close()
