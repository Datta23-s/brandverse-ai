"""app.match_memory (migration 0022) and the memory trust fixes (0023).

Phases.md Phase 4 exit criteria covered here: "Cross-tenant memory leak test green" and
"Revocation removes an item from retrieval immediately". Each test runs in a rolled-back
transaction with hand-built 768-dimension vectors, so similarities are exact.
"""

from uuid import UUID

import pytest
from psycopg import errors

from tests.db.conftest import Tx

pytestmark = pytest.mark.db

DIM = 768


def vec(*hot: int) -> str:
    """A unit vector with equal weight on the given dimensions."""
    v = [0.0] * DIM
    for i in hot:
        v[i] = 1.0 / len(hot) ** 0.5
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def item(
    tx: Tx,
    content: str,
    embedding: str | None,
    *,
    org: UUID | None = None,
    brand: UUID | None | str = "a",
    approved: bool = True,
    seed: bool = False,
    kind: str = "insight",
) -> UUID:
    org = org or tx.seed.org_a
    brand_id = tx.seed.brand_a if brand == "a" else brand
    rows = tx.execute(
        "insert into memory_items (org_id, brand_id, kind, content, embedding, embedding_model, "
        "embedding_version, provenance, confidence, approved, is_seed) values (%s, %s, %s, %s, "
        "%s::vector, %s, %s, 'test', 0.5, %s, %s) returning id",
        (
            org,
            brand_id,
            kind,
            content,
            embedding,
            "test" if embedding else None,
            "test" if embedding else None,
            approved,
            seed,
        ),
    )
    found: UUID = rows[0][0]
    return found


def match(
    tx: Tx,
    user: str,
    org: UUID,
    query_vec: str,
    text: str,
    *,
    p_brand_id: UUID | None = None,
    p_include_seed: bool = False,
) -> list[tuple[object, ...]]:
    tx.as_user(user)
    rows = tx.execute(
        "select id, vec_rank, txt_rank, rrf, similarity from app.match_memory("
        "%s, %s::vector, %s, p_brand_id => %s, p_include_seed => %s)",
        (org, query_vec, text, p_brand_id, p_include_seed),
    )
    tx.execute("reset role")
    return rows


def test_both_branches_fuse_and_an_item_found_twice_ranks_first(tx: Tx) -> None:
    both = item(tx, "monsoon campaigns lead with the waterproof rating", vec(1))
    vector_only = item(tx, "grip matters on wet rock", vec(1, 2))
    text_only = item(tx, "the waterproof rating sells in the rains", vec(500))
    rows = match(tx, "owner_a", tx.seed.org_a, vec(1), "waterproof rating")
    ids = [r[0] for r in rows]
    assert ids[0] == both
    assert {both, vector_only, text_only} <= set(ids)
    first = rows[0]
    assert first[1] == 1 and first[2] is not None  # found by both branches
    assert float(first[3]) == pytest.approx(1 / 61 + 1 / (60 + first[2]))
    assert first[4] == pytest.approx(1.0, abs=1e-5)  # identical vectors: cosine 1


def test_another_orgs_memory_never_leaks_even_when_identical(tx: Tx) -> None:
    mine = item(tx, "founder story beats spec sheet", vec(7))
    theirs = item(
        tx, "founder story beats spec sheet", vec(7), org=tx.seed.org_b, brand=tx.seed.brand_b
    )
    rows = match(tx, "owner_a", tx.seed.org_a, vec(7), "founder story")
    assert [r[0] for r in rows] == [mine]
    assert theirs not in [r[0] for r in rows]


def test_a_member_of_both_orgs_sees_only_the_org_asked_for(tx: Tx) -> None:
    a = item(tx, "agency insight for brand A", vec(9))
    b = item(tx, "agency insight for brand B", vec(9), org=tx.seed.org_b, brand=tx.seed.brand_b)
    in_a = {r[0] for r in match(tx, "agency", tx.seed.org_a, vec(9), "agency insight")}
    in_b = {r[0] for r in match(tx, "agency", tx.seed.org_b, vec(9), "agency insight")}
    assert in_a == {a} and in_b == {b}


def test_a_non_member_naming_the_org_gets_nothing(tx: Tx) -> None:
    item(tx, "private to org A", vec(11))
    assert match(tx, "outsider", tx.seed.org_a, vec(11), "private") == []


def test_revocation_removes_an_item_from_retrieval_immediately(tx: Tx) -> None:
    gone = item(tx, "competitor x positions on warranty", vec(13))
    assert [r[0] for r in match(tx, "editor_a", tx.seed.org_a, vec(13), "warranty")] == [gone]
    tx.as_user("editor_a")
    tx.execute(
        "update memory_items set revoked = true, revoked_reason = 'wrong' where id = %s", (gone,)
    )
    tx.execute("reset role")
    assert match(tx, "editor_a", tx.seed.org_a, vec(13), "warranty") == []


def test_unapproved_items_are_not_retrieved(tx: Tx) -> None:
    item(tx, "awaiting approval", vec(15), approved=False)
    assert match(tx, "owner_a", tx.seed.org_a, vec(15), "awaiting approval") == []


def test_seed_memory_is_excluded_unless_asked_for(tx: Tx) -> None:
    s = item(tx, "seeded demo insight", vec(17), seed=True)
    assert match(tx, "owner_a", tx.seed.org_a, vec(17), "seeded demo") == []
    rows = match(tx, "owner_a", tx.seed.org_a, vec(17), "seeded demo", p_include_seed=True)
    assert [r[0] for r in rows] == [s]


def test_a_brand_sees_its_own_items_and_org_wide_ones_not_another_brands(tx: Tx) -> None:
    other_brand = tx.execute(
        "insert into brands (org_id, name) values (%s, 'other') returning id", (tx.seed.org_a,)
    )[0][0]
    own = item(tx, "pricing insight", vec(19))
    org_wide = item(tx, "pricing insight for everyone", vec(19), brand=None)
    other = item(tx, "pricing insight elsewhere", vec(19), brand=other_brand)
    rows = match(tx, "owner_a", tx.seed.org_a, vec(19), "pricing", p_brand_id=tx.seed.brand_a)
    assert {r[0] for r in rows} == {own, org_wide}
    assert other not in {r[0] for r in rows}


def test_anon_cannot_execute_match_memory(tx: Tx) -> None:
    tx.execute("set local role anon")
    with pytest.raises(errors.InsufficientPrivilege):
        tx.execute("select * from app.match_memory(%s, %s::vector, 'x')", (tx.seed.org_a, vec(1)))


# ── 0023: revocation recorded by the server; claim-backed memory keeps its claim ─────────────


def test_a_revocation_is_recorded_as_the_caller_now_whatever_was_sent(tx: Tx) -> None:
    m = item(tx, "to revoke", vec(21))
    tx.as_user("editor_a")
    rows = tx.execute(
        "update memory_items set revoked = true, revoked_reason = 'stale', revoked_by = %s, "
        "revoked_at = '2001-01-01' where id = %s returning revoked_by, revoked_at = now()",
        (tx.seed.users["editor_a"], m),
    )
    assert rows == [(tx.seed.users["editor_a"], True)]


def test_revocation_is_permanent_and_recorded_once(tx: Tx) -> None:
    m = item(tx, "revoked for good", vec(23))
    tx.as_user("editor_a")
    tx.execute(
        "update memory_items set revoked = true, revoked_reason = 'wrong' where id = %s", (m,)
    )
    tx.execute("reset role")
    tx.execute("savepoint s")
    with pytest.raises(errors.RaiseException, match="permanent"):
        tx.execute("update memory_items set revoked = false where id = %s", (m,))
    tx.execute("rollback to savepoint s")
    with pytest.raises(errors.RaiseException, match="recorded once"):
        tx.execute("update memory_items set revoked_reason = 'other' where id = %s", (m,))


def test_a_product_whose_claim_backs_memory_cannot_be_deleted(tx: Tx) -> None:
    product = tx.execute(
        "insert into products (org_id, brand_id, name) values (%s, %s, 'backed') returning id",
        (tx.seed.org_a, tx.seed.brand_a),
    )[0][0]
    tx.as_user("owner_a")
    claim = tx.execute(
        "insert into product_claims (org_id, product_id, claim, claim_type, evidence_url, "
        "created_by) values (%s, %s, 'IPX7', 'verified', 'https://example.invalid/s', 'human') "
        "returning id",
        (tx.seed.org_a, product),
    )[0][0]
    tx.execute("reset role")
    tx.execute(
        "insert into memory_items (org_id, brand_id, claim_id, kind, content, provenance, "
        "confidence, approved) values (%s, %s, %s, 'claim', 'IPX7 rated', 'test', 0.5, true)",
        (tx.seed.org_a, tx.seed.brand_a, claim),
    )
    tx.as_user("owner_a")
    with pytest.raises(errors.ForeignKeyViolation):
        tx.execute("delete from products where id = %s", (product,))
