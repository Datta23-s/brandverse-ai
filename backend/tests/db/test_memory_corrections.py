"""Memory.md §6.3 at commit: corrections that reduce trust, reuse bumps that add it, the decay
revocation record (migration 0024), and the count of how memory items died.

Each test works in one transaction that is always rolled back (the `conn` fixture). The attempt
rule — which run endings commit corrections — is tested end to end in test_memory_staging.py.
"""

import json
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import AsyncConnection

from app.evaluation.memory_revocations import memory_revocations
from app.memory.writeback import (
    Correction,
    Reuse,
    StagedMemory,
    WritebackRefused,
    commit_memory,
    decay_reason,
)
from tests.db.conftest import Seed
from tests.db.test_writeback_commit import _existing, _fact, _item, _one, _run

pytestmark = [pytest.mark.anyio, pytest.mark.db]

DECAY = "confidence_decay: confidence 0.40 -> 0.25 below 0.30; cause {cause}; run {run}"


@pytest.fixture
async def conn(db_url: str) -> AsyncIterator[AsyncConnection]:
    """A new connection whose transaction is always rolled back."""
    c = await AsyncConnection.connect(db_url)
    try:
        yield c
    finally:
        await c.rollback()
        await c.close()


async def _state(conn: AsyncConnection, item: UUID) -> tuple[object, ...]:
    return await _one(
        conn,
        "select confidence::text, revoked, revoked_by, revoked_at is not null, revoked_reason "
        "from memory_items where id = %s",
        (item,),
    )


async def _act_as(conn: AsyncConnection, seed: Seed, user: str) -> None:
    claims = {"sub": str(seed.users[user]), "role": "authenticated"}
    await conn.execute("set local role authenticated")
    await conn.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))


# ── Corrections (§6.3): confidence − 0.15; below 0.30, revoked by rule ───────────────────────


async def test_a_flag_lowers_confidence_without_revoking_above_the_threshold(
    conn: AsyncConnection, seed: Seed
) -> None:
    item = await _existing(conn, seed, _fact(), confidence=0.80)
    run_id = await _run(conn, seed)
    done = await commit_memory(
        conn, run_id, StagedMemory(corrections=(Correction.flagged(item, "unrepeated_quantity"),))
    )
    assert await _state(conn, item) == ("0.65", False, None, False, None)
    assert [(c["before"], c["after"], c["revoked"]) for c in done.corrected] == [(0.8, 0.65, False)]


async def test_a_flag_that_crosses_the_threshold_revokes_by_rule_with_an_auditable_record(
    conn: AsyncConnection, seed: Seed
) -> None:
    item = await _existing(conn, seed, _fact(), confidence=0.40)
    run_id = await _run(conn, seed)
    await commit_memory(
        conn, run_id, StagedMemory(corrections=(Correction.flagged(item, "unrepeated_quantity"),))
    )
    reason = DECAY.format(cause="validation_flag:unrepeated_quantity", run=run_id)
    # The confidence at revocation, what caused the crossing, and the run: why this item died.
    assert await _state(conn, item) == ("0.25", True, None, True, reason)


async def test_a_reviewer_edit_that_crosses_the_threshold_names_the_reviewer_in_the_record(
    conn: AsyncConnection, seed: Seed
) -> None:
    item = await _existing(conn, seed, _fact(), confidence=0.40)
    run_id, reviewer = await _run(conn, seed), seed.users["approver_a"]
    await commit_memory(
        conn, run_id, StagedMemory(corrections=(Correction.edited_out(item, reviewer),))
    )
    reason = DECAY.format(cause=f"reviewer_edit:{reviewer}", run=run_id)
    assert await _state(conn, item) == ("0.25", True, None, True, reason)


async def test_corrections_commit_without_an_approved_campaign(
    conn: AsyncConnection, seed: Seed
) -> None:
    # Approval gates what adds trust; a correction reduces it (Memory.md §6, 2026-10-19).
    item = await _existing(conn, seed, _fact(), confidence=0.50)
    run_id = await _run(conn, seed, "rejected")
    staged = StagedMemory(corrections=(Correction.edited_out(item, seed.users["approver_a"]),))
    done = await commit_memory(conn, run_id, staged)
    assert len(done.corrected) == 1
    assert (await _state(conn, item))[0] == "0.35"


async def test_one_run_corrects_an_item_once_and_records_the_rest(
    conn: AsyncConnection, seed: Seed
) -> None:
    item = await _existing(conn, seed, _fact(), confidence=0.80)
    revoked = await _existing(conn, seed, _fact(), revoked=True)
    staged = StagedMemory(
        corrections=(
            Correction.flagged(item, "unrepeated_quantity"),
            Correction.edited_out(item, seed.users["approver_a"]),
            Correction.flagged(revoked, "unsourced_claim"),
        )
    )
    done = await commit_memory(conn, await _run(conn, seed), staged)
    assert (await _state(conn, item))[0] == "0.65"  # lowered once, not twice
    assert [s["reason"] for s in done.skipped] == [
        "already_corrected_in_this_run",
        "already_revoked",
    ]


async def test_no_memory_item_can_be_written_without_a_confidence(
    conn: AsyncConnection, seed: Seed
) -> None:
    # 0025: an item with no confidence could never decay out, so none can exist — whoever writes
    # it, by whatever path, and there is no default to fill the gap.
    for insert in [
        "insert into memory_items (org_id, kind, content, provenance) "
        "values (%s, 'insight', 'no confidence', 'human')",
        "insert into memory_items (org_id, kind, content, provenance, confidence) "
        "values (%s, 'insight', 'null confidence', 'human', null)",
    ]:
        await conn.execute("savepoint s")
        with pytest.raises(psycopg.errors.NotNullViolation):
            await conn.execute(insert, (seed.org_a,))
        await conn.execute("rollback to savepoint s")
    item = await _existing(conn, seed, _fact(), confidence=0.5)
    with pytest.raises(psycopg.errors.NotNullViolation):
        await conn.execute("update memory_items set confidence = null where id = %s", (item,))


async def test_confidence_is_a_probability(conn: AsyncConnection, seed: Seed) -> None:
    # 0026: numeric(3,2) alone stores -9.99 to 9.99.
    for bad in (-0.05, 1.5):
        await conn.execute("savepoint s")
        with pytest.raises(psycopg.errors.CheckViolation, match="confidence_range"):
            await _existing(conn, seed, _fact(), confidence=bad)
        await conn.execute("rollback to savepoint s")
    item = await _existing(conn, seed, _fact(), confidence=1.0)
    with pytest.raises(psycopg.errors.CheckViolation, match="confidence_range"):
        await conn.execute("update memory_items set confidence = -0.01 where id = %s", (item,))


async def test_a_live_item_cannot_sit_below_the_revocation_threshold(
    conn: AsyncConnection, seed: Seed
) -> None:
    # 0026: revocation tests the value, not the crossing. An item written at 0.20 would otherwise
    # be live and retrievable until some correction found it.
    await conn.execute("savepoint s")
    with pytest.raises(psycopg.errors.CheckViolation, match="live_confidence"):
        await _existing(conn, seed, _fact(), confidence=0.20)
    await conn.execute("rollback to savepoint s")
    item = await _existing(conn, seed, _fact(), confidence=0.30)  # the threshold itself is live
    with pytest.raises(psycopg.errors.CheckViolation, match="live_confidence"):
        await conn.execute("update memory_items set confidence = 0.29 where id = %s", (item,))


async def test_the_database_threshold_is_the_configured_one(conn: AsyncConnection) -> None:
    import re
    from decimal import Decimal

    from app.memory.scoring import load_scoring

    (definition,) = await _one(
        conn,
        "select pg_get_constraintdef(oid) from pg_constraint "
        "where conname = 'memory_items_live_confidence'",
        (),
    )
    match = re.search(r"confidence >= ([0-9.]+)", str(definition))
    assert match, definition
    assert Decimal(match[1]) == Decimal(str(load_scoring().writeback.revoke_below))


async def test_a_decrement_cannot_take_confidence_below_zero(
    conn: AsyncConnection, seed: Seed
) -> None:
    from app.memory.scoring import load_scoring

    cfg = load_scoring().writeback.model_copy(update={"correction_step": 0.5})
    item = await _existing(conn, seed, _fact(), confidence=0.40)
    run_id = await _run(conn, seed)
    done = await commit_memory(
        conn,
        run_id,
        StagedMemory(corrections=(Correction.flagged(item, "unrepeated_quantity"),)),
        cfg=cfg,
    )
    reason = "confidence_decay: confidence 0.40 -> 0.00 below 0.30; "
    state = await _state(conn, item)
    assert state[:2] == ("0.00", True)
    assert str(state[4]).startswith(reason)
    assert [(c["after"], c["revoked"]) for c in done.corrected] == [(0.0, True)]


async def test_an_item_corrected_in_a_run_is_not_reinforced_by_it(
    conn: AsyncConnection, seed: Seed
) -> None:
    fact = _fact()
    item = await _existing(conn, seed, fact, confidence=0.80)
    staged = StagedMemory(
        items=(_item(seed, fact),),
        reuses=(Reuse(item, 0.95),),
        corrections=(Correction.flagged(item, "unrepeated_quantity"),),
    )
    done = await commit_memory(conn, await _run(conn, seed), staged)
    assert done.reused == done.reinforced == done.inserted == []
    assert [s["reason"] for s in done.skipped] == [
        "corrected_in_this_run",
        "near_duplicate_corrected_in_this_run",
    ]
    assert (await _state(conn, item))[0] == "0.65"


async def test_a_correction_of_another_organisations_item_refuses_the_commit(
    conn: AsyncConnection, seed: Seed
) -> None:
    (foreign,) = await _one(
        conn,
        "insert into memory_items (org_id, kind, content, provenance, confidence) "
        "values (%s, 'insight', 'org B fact', 'human', 0.5) returning id",
        (seed.org_b,),
    )
    assert isinstance(foreign, UUID)
    staged = StagedMemory(corrections=(Correction.flagged(foreign, "unsourced_claim"),))
    with pytest.raises(WritebackRefused, match="not an item of org"):
        await commit_memory(conn, await _run(conn, seed), staged)


def test_a_correction_is_checked_when_it_is_made() -> None:
    with pytest.raises(ValueError, match="not a finding kind"):
        Correction(uuid4(), "validation_flag", "something; run x")
    with pytest.raises(ValueError):
        Correction(uuid4(), "reviewer_edit", "not-a-user")


# ── Reuse bumps (§6.3): +0.02 when reused at M ≥ 0.90 in an approved campaign ────────────────


async def test_a_reused_item_is_bumped_once_and_only_from_the_reuse_band(
    conn: AsyncConnection, seed: Seed
) -> None:
    item = await _existing(conn, seed, _fact(), confidence=0.50)
    low = await _existing(conn, seed, _fact(), confidence=0.50)
    staged = StagedMemory(reuses=(Reuse(item, 0.93), Reuse(item, 0.91), Reuse(low, 0.89)))
    done = await commit_memory(conn, await _run(conn, seed), staged)
    assert [(r["item"], r["confidence"]) for r in done.reused] == [(str(item), 0.52)]
    assert [s["reason"] for s in done.skipped] == [
        "counted_once_in_this_run",
        "below_the_reuse_band",
    ]
    assert (await _state(conn, low))[0] == "0.50"


async def test_a_reuse_bump_needs_an_approved_campaign(conn: AsyncConnection, seed: Seed) -> None:
    item = await _existing(conn, seed, _fact(), confidence=0.50)
    with pytest.raises(WritebackRefused, match="adds trust"):
        await commit_memory(
            conn, await _run(conn, seed, "rejected"), StagedMemory(reuses=(Reuse(item, 0.95),))
        )


# ── The decay revocation record (0024) ──────────────────────────────────────────────────────


async def _revoke(
    conn: AsyncConnection,
    item: UUID,
    reason: str | None,
    by: UUID | None = None,
    *,
    confidence: float | None = None,
) -> None:
    """Revoke; `confidence` is lowered in the same statement, as a decay does (0026 allows a
    confidence below the threshold only on a revoked item)."""
    # Named only when given: a signed-in user holds no UPDATE grant on the column.
    if confidence is None:
        await conn.execute(
            "update memory_items set revoked = true, revoked_by = %s, revoked_reason = %s "
            "where id = %s",
            (by, reason, item),
        )
    else:
        await conn.execute(
            "update memory_items set revoked = true, revoked_by = %s, revoked_reason = %s, "
            "confidence = %s where id = %s",
            (by, reason, confidence, item),
        )


@pytest.mark.parametrize(
    "reason",
    [
        None,
        "decayed",  # a category, not a record
        "confidence_decay: confidence 0.40 -> 0.25 below 0.30; cause validation_flag:x",  # no run
        "confidence_decay: confidence 0.40 -> 0.20 below 0.30; cause validation_flag:x; run {run}",
        "confidence_decay: confidence 0.40 -> 0.25 below 0.20; cause validation_flag:x; run {run}",
        "confidence_decay: confidence 0.40 -> 0.25 below 0.30; cause because:x; run {run}",
        "confidence_decay: confidence 0.40 -> 0.25 below 0.30; cause reviewer_edit:bob; run {run}",
    ],
    ids=[
        "no_reason",
        "category_only",
        "no_run",
        "after_not_the_confidence",
        "after_not_below_threshold",
        "unknown_cause",
        "reviewer_not_a_user_id",
    ],
)
async def test_a_revocation_with_no_revoker_must_carry_a_well_formed_decay_record(
    conn: AsyncConnection, seed: Seed, reason: str | None
) -> None:
    item = await _existing(conn, seed, _fact(), confidence=0.40)
    with pytest.raises(psycopg.errors.CheckViolation):
        await _revoke(conn, item, reason and reason.format(run=uuid4()), confidence=0.25)


async def test_the_well_formed_record_is_accepted(conn: AsyncConnection, seed: Seed) -> None:
    item = await _existing(conn, seed, _fact(), confidence=0.40)
    reason = DECAY.format(cause="validation_flag:unsourced_claim", run=uuid4())
    await _revoke(conn, item, reason, confidence=0.25)
    assert await _state(conn, item) == ("0.25", True, None, True, reason)


async def test_a_decay_record_cannot_carry_a_revoker(conn: AsyncConnection, seed: Seed) -> None:
    # By rule or by a person, never both: the split the evaluation reads depends on it.
    item = await _existing(conn, seed, _fact(), confidence=0.40)
    reason = DECAY.format(cause="validation_flag:unsourced_claim", run=uuid4())
    with pytest.raises(psycopg.errors.CheckViolation):
        await _revoke(conn, item, reason, by=seed.users["owner_a"], confidence=0.25)


async def test_a_decay_revocations_confidence_is_frozen(conn: AsyncConnection, seed: Seed) -> None:
    item = await _existing(conn, seed, _fact(), confidence=0.40)
    await _revoke(
        conn,
        item,
        DECAY.format(cause="validation_flag:unsourced_claim", run=uuid4()),
        confidence=0.25,
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        await conn.execute("update memory_items set confidence = 0.90 where id = %s", (item,))


async def test_a_person_cannot_write_a_decay_record(conn: AsyncConnection, seed: Seed) -> None:
    item = await _existing(conn, seed, _fact(), confidence=0.40)
    await _act_as(conn, seed, "owner_a")
    reason = DECAY.format(cause="validation_flag:unsourced_claim", run=uuid4())
    with pytest.raises(psycopg.errors.RaiseException, match="recorded by the system"):
        await _revoke(conn, item, reason, by=seed.users["owner_a"])


async def test_a_person_still_revokes_in_their_own_name(conn: AsyncConnection, seed: Seed) -> None:
    item = await _existing(conn, seed, _fact(), confidence=0.50)
    await _act_as(conn, seed, "owner_a")
    await _revoke(conn, item, "wrong supplier", by=seed.users["owner_a"])
    await conn.execute("reset role")
    state = await _state(conn, item)
    assert state[1:3] == (True, seed.users["owner_a"])


def test_the_record_written_matches_the_format_the_database_enforces() -> None:
    from decimal import Decimal

    run, reviewer = uuid4(), uuid4()
    c = Correction.edited_out(uuid4(), reviewer)
    assert decay_reason(Decimal("0.40"), Decimal("0.25"), Decimal("0.30"), c, run) == (
        DECAY.format(cause=f"reviewer_edit:{reviewer}", run=run)
    )


# ── How memory items died: by decay, by review (evaluation-plan.md) ────────────────────────


async def test_revocations_are_counted_by_decay_cause_and_by_review(
    conn: AsyncConnection, seed: Seed
) -> None:
    (org,) = await _one(conn, "insert into organisations (name) values ('R') returning id", ())
    assert isinstance(org, UUID)

    async def item(confidence: float, *, seed_item: bool = False) -> UUID:
        (i,) = await _one(
            conn,
            "insert into memory_items (org_id, kind, content, provenance, confidence, is_seed) "
            "values (%s, 'insight', %s, 'human', %s, %s) returning id",
            (org, _fact(), confidence, seed_item),
        )
        assert isinstance(i, UUID)
        return i

    for cause in ("validation_flag:unsourced_claim", "validation_flag:unsourced_claim",
                  f"reviewer_edit:{seed.users['approver_a']}"):  # fmt: skip
        await _revoke(
            conn, await item(0.40), DECAY.format(cause=cause, run=uuid4()), confidence=0.25
        )
    await _revoke(conn, await item(0.70), "wrong supplier", by=seed.users["owner_a"])
    await _revoke(conn, await item(0.70, seed_item=True), "seed", by=seed.users["owner_a"])
    await item(0.70)  # live: not counted

    counts = await memory_revocations(conn, [org])
    assert counts.by_kind == {
        "decay_validation_flag": 2,
        "decay_reviewer_edit": 1,
        "review": 1,  # the seed item's revocation is excluded (Memory.md §7.1)
    }
    assert (counts.by_decay, counts.by_review) == (3, 1)
    assert (await memory_revocations(conn, [seed.org_b])).by_kind == dict.fromkeys(
        counts.by_kind, 0
    )
