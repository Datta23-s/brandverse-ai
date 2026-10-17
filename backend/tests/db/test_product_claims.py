"""The product truth store as constraints (migration 0017; Phases.md Phase 3).

What verified, deduced and restricted mean is asserted here at the database, because members can
write product_claims through the REST API directly: every statement below runs as the API role
would run it, not through our API. Merge-blocking with the rest of the database suite.

- verified needs a human entry (derived from the JWT, never from the caller) and an http(s) URL;
- deduced needs a confidence and a basis; restricted needs its reason;
- who entered or retired a claim, and when, are written by the trigger, whatever the caller sent;
- claims are never edited or deleted, only retired, once.
"""

from uuid import UUID, uuid4

import pytest
from psycopg import errors

from tests.db.conftest import Tx

pytestmark = pytest.mark.db

INSERT = (
    "insert into product_claims (org_id, product_id, claim, claim_type, evidence_url, "
    "evidence_note, confidence, created_by) values (%s, %s, %s, %s, %s, %s, %s, %s) "
    "returning id, created_by, entered_by, created_at, retired_at"
)


def _insert(
    tx: Tx,
    claim_type: str,
    *,
    url: str | None = None,
    note: str | None = None,
    confidence: float | None = None,
    created_by: str = "human",
    claim: str = "Weighs 240 g",
) -> tuple[object, ...]:
    rows = tx.execute(
        INSERT,
        (
            tx.seed.org_a,
            tx.seed.product_a,
            claim,
            claim_type,
            url,
            note,
            confidence,
            created_by,
        ),
    )
    return rows[0]


def _verified(tx: Tx, **kw: object) -> UUID:
    row = _insert(tx, "verified", url="https://example.invalid/spec", **kw)  # type: ignore[arg-type]
    claim_id = row[0]
    assert isinstance(claim_id, UUID)
    return claim_id


# ── Provenance comes from the JWT, never from the caller (Architecture.md §9) ─────────────────


def test_a_rest_insert_naming_someone_else_is_recorded_as_the_caller(tx: Tx) -> None:
    tx.as_user("editor_a")
    rows = tx.execute(
        "insert into product_claims (org_id, product_id, claim, claim_type, evidence_url, "
        "created_by, entered_by, created_at) values (%s, %s, 'Weighs 240 g', 'verified', "
        "'https://example.invalid/spec', 'A1', %s, '2001-01-01') "
        "returning created_by, entered_by, created_at = now()",
        (tx.seed.org_a, tx.seed.product_a, tx.seed.users["owner_a"]),
    )
    assert rows == [("human", tx.seed.users["editor_a"], True)]


def test_a_signed_in_user_cannot_insert_as_an_agent(tx: Tx) -> None:
    tx.as_user("editor_a")
    _, created_by, entered_by, _, _ = _insert(
        tx, "deduced", note="from the spec sheet", confidence=0.7, created_by="A7"
    )
    assert (created_by, entered_by) == ("human", tx.seed.users["editor_a"])


def test_the_backend_cannot_claim_to_be_a_human(tx: Tx) -> None:
    with pytest.raises(errors.RaiseException, match="only a signed-in user enters a human claim"):
        _verified(tx)


def test_the_backend_cannot_supply_a_human_entrant(tx: Tx) -> None:
    with pytest.raises(errors.RaiseException, match="only a signed-in user"):
        tx.execute(
            "insert into product_claims (org_id, product_id, claim, claim_type, evidence_note, "
            "confidence, created_by, entered_by) values (%s, %s, 'x', 'deduced', 'n', 0.5, 'A1', "
            "%s)",
            (tx.seed.org_a, tx.seed.product_a, tx.seed.users["owner_a"]),
        )


def test_authenticated_role_with_no_identity_cannot_enter_a_claim(tx: Tx) -> None:
    tx.set_claims({"role": "authenticated"})
    with pytest.raises(errors.RaiseException, match="signed-in user"):
        _verified(tx)


# ── What each claim_type requires ────────────────────────────────────────────────────────────


def test_verified_needs_an_evidence_url(tx: Tx) -> None:
    tx.as_user("editor_a")
    with pytest.raises(errors.CheckViolation, match="product_claims_verified"):
        _insert(tx, "verified", note="the manufacturer said so")


@pytest.mark.parametrize("url", ["example.com/spec", "ftp://example.com/spec", "https://"])
def test_evidence_must_be_an_http_url(tx: Tx, url: str) -> None:
    tx.as_user("editor_a")
    with pytest.raises(errors.CheckViolation, match="product_claims_evidence_url"):
        _insert(tx, "verified", url=url)


def test_an_agent_claim_is_never_verified(tx: Tx) -> None:
    with pytest.raises(errors.CheckViolation, match="product_claims_verified"):
        _insert(tx, "verified", url="https://example.invalid/spec", created_by="A1")


def test_an_agent_may_record_a_deduced_claim_with_its_basis(tx: Tx) -> None:
    _, created_by, entered_by, _, _ = _insert(
        tx, "deduced", url="https://example.invalid/review", confidence=0.6, created_by="A1"
    )
    assert (created_by, entered_by) == ("A1", None)


@pytest.mark.parametrize(
    ("note", "url", "confidence"),
    [("basis", None, None), (None, None, 0.6), ("   ", None, 0.6)],
    ids=["no confidence", "no basis", "blank note"],
)
def test_deduced_needs_confidence_and_a_basis(
    tx: Tx, note: str | None, url: str | None, confidence: float | None
) -> None:
    tx.as_user("editor_a")
    with pytest.raises(errors.CheckViolation, match="product_claims_deduced"):
        _insert(tx, "deduced", note=note, url=url, confidence=confidence)


def test_restricted_needs_its_reason(tx: Tx) -> None:
    tx.as_user("editor_a")
    tx.execute("savepoint s")
    with pytest.raises(errors.CheckViolation, match="product_claims_restricted"):
        _insert(tx, "restricted", claim="Clinically proven")
    tx.execute("rollback to savepoint s")
    _insert(tx, "restricted", claim="Clinically proven", note="medical claim, no trial cited")


def test_restricted_may_carry_the_source_that_states_it(tx: Tx) -> None:
    # A manufacturer stating it does not make it sayable (Architecture.md §8).
    tx.as_user("editor_a")
    _insert(
        tx,
        "restricted",
        claim="Unbeatable sound",
        url="https://example.invalid/product",
        note="superlative, no independent source",
    )


@pytest.mark.parametrize("text", ["", "   ", "x" * 501])
def test_claim_text_is_1_to_500_characters(tx: Tx, text: str) -> None:
    tx.as_user("editor_a")
    with pytest.raises(errors.CheckViolation, match="product_claims_claim_text"):
        _insert(tx, "verified", url="https://example.invalid/spec", claim=text)


def test_viewer_cannot_enter_a_claim(tx: Tx) -> None:
    tx.as_user("viewer_a")
    with pytest.raises(errors.InsufficientPrivilege):
        _verified(tx)


def test_an_editor_cannot_enter_a_claim_in_another_org(tx: Tx) -> None:
    tx.as_user("approver_b")
    with pytest.raises(errors.InsufficientPrivilege):
        _verified(tx)


# ── Never edited, never deleted; retired once ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "assignment",
    [
        "claim = 'Weighs 200 g'",
        "claim_type = 'restricted', evidence_note = 'n'",
        "evidence_url = 'https://example.invalid/other'",
        "created_by = 'A1', entered_by = null",
    ],
)
def test_a_claim_is_never_edited(tx: Tx, assignment: str) -> None:
    tx.as_user("editor_a")
    claim = _verified(tx)
    with pytest.raises(errors.RaiseException, match="never edited"):
        tx.execute(f"update product_claims set {assignment} where id = %s", (claim,))  # noqa: S608 - fixed list


def test_retiring_records_the_caller_and_now_whatever_was_sent(tx: Tx) -> None:
    tx.as_user("editor_a")
    claim = _verified(tx)
    rows = tx.execute(
        "update product_claims set retired_reason = 'spec sheet withdrawn', "
        "retired_at = '2001-01-01', retired_by = %s where id = %s "
        "returning retired_by, retired_at = now(), retired_reason",
        (tx.seed.users["owner_a"], claim),
    )
    assert rows == [(tx.seed.users["editor_a"], True, "spec sheet withdrawn")]


def test_retiring_needs_a_reason(tx: Tx) -> None:
    tx.as_user("editor_a")
    claim = _verified(tx)
    with pytest.raises(errors.CheckViolation, match="product_claims_retirement"):
        tx.execute("update product_claims set retired_at = now() where id = %s", (claim,))


def test_a_retired_claim_cannot_change_again(tx: Tx) -> None:
    tx.as_user("editor_a")
    claim = _verified(tx)
    tx.execute("update product_claims set retired_reason = 'wrong' where id = %s", (claim,))
    with pytest.raises(errors.RaiseException, match="retired claim cannot change"):
        tx.execute("update product_claims set retired_reason = 'other' where id = %s", (claim,))


def test_the_backend_cannot_retire_a_claim(tx: Tx) -> None:
    claim = tx.seed.claim_a
    with pytest.raises(errors.RaiseException, match="retired by a signed-in user"):
        tx.execute("update product_claims set retired_reason = 'x' where id = %s", (claim,))


def test_a_claim_is_entered_active(tx: Tx) -> None:
    tx.as_user("editor_a")
    with pytest.raises(errors.RaiseException, match="entered active"):
        tx.execute(
            "insert into product_claims (org_id, product_id, claim, claim_type, evidence_url, "
            "created_by, retired_reason) values (%s, %s, 'x', 'verified', "
            "'https://example.invalid/spec', 'human', 'pre-retired')",
            (tx.seed.org_a, tx.seed.product_a),
        )


def test_viewer_cannot_retire(tx: Tx) -> None:
    tx.as_user("viewer_a")
    rows = tx.execute(
        "update product_claims set retired_reason = 'x' where id = %s returning id",
        (tx.seed.claim_a,),
    )
    assert rows == []  # no update policy admits a viewer


@pytest.mark.parametrize("user", ["owner_a", "editor_a"])
def test_no_member_can_delete_a_claim(tx: Tx, user: str) -> None:
    tx.as_user(user)
    with pytest.raises(errors.InsufficientPrivilege):
        tx.execute("delete from product_claims where id = %s", (tx.seed.claim_a,))


def test_deleting_a_product_without_runs_still_removes_its_claims(tx: Tx) -> None:
    product = tx.execute(
        "insert into products (org_id, brand_id, name) values (%s, %s, 'no runs') returning id",
        (tx.seed.org_a, tx.seed.brand_a),
    )[0][0]
    tx.as_user("owner_a")
    tx.execute(
        "insert into product_claims (org_id, product_id, claim, claim_type, evidence_url, "
        "created_by) values (%s, %s, 'x', 'verified', 'https://example.invalid/spec', 'human')",
        (tx.seed.org_a, product),
    )
    tx.execute("delete from products where id = %s", (product,))
    assert tx.execute("select 1 from product_claims where product_id = %s", (product,)) == []


def test_a_product_whose_claims_a_run_could_cite_cannot_be_deleted(tx: Tx) -> None:
    # product_a has a run (seed), so its claims stay: the run's source trail cannot vanish.
    tx.as_user("owner_a")
    with pytest.raises(errors.ForeignKeyViolation):
        tx.execute("delete from products where id = %s", (tx.seed.product_a,))


def test_a_forged_identity_that_is_no_member_cannot_enter_a_claim(tx: Tx) -> None:
    tx.set_claims({"sub": str(uuid4()), "role": "authenticated"})
    with pytest.raises(errors.InsufficientPrivilege):
        _verified(tx)
