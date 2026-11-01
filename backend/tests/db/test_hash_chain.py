"""The hash chain on `approvals` and `audit_log` (migration 0042; Architecture.md section 9).

Detection, not prevention: these tests show the chain DETECTS an edited row, a deleted row and a
reordered one, stays valid under concurrent inserts and across session time zones, and is not
disturbed by an insert the database refuses. What it cannot detect (the table owner rewriting the
whole chain; truncating the tail) is stated in the migration and not tested for.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from psycopg import errors

from tests.db.conftest import Seed, Tx

pytestmark = pytest.mark.db

AUDIT = (
    "insert into audit_log (actor_type, actor_id, action, entity_type, entity_id, details) "
    "values ('service', 'test', 'chain.test', 'test', %s, %s::jsonb) returning chain_seq"
)
UNDECIDED = (
    "insert into approvals (campaign_id, org_id, approver_id, outcome, reason, seconds_spent, "
    "kind) "
    "values (%s, %s, %s, 'undecided', 'ran out of time', 1, 'evaluation') returning chain_seq"
)


def verify(tx: Tx, table: str) -> tuple[int, int, int | None, str | None, int | None, bool]:
    rows = tx.execute("select * from app.chain_verify(%s)", (table,))
    return rows[0]  # type: ignore[return-value]


def add_audit(tx: Tx, n: int) -> list[int]:
    return [tx.execute(AUDIT, (f"e{i}", f'{{"i": {i}}}'))[0][0] for i in range(n)]  # type: ignore[misc]


def test_rows_are_chained_in_order_and_verify(tx: Tx) -> None:
    seqs = add_audit(tx, 3)
    assert seqs == [seqs[0], seqs[0] + 1, seqs[0] + 2]
    chained, _, head_seq, head_hash, bad, ok = verify(tx, "audit_log")
    assert ok and bad is None and head_seq == seqs[-1] and len(head_hash or "") == 64
    assert chained >= 3


def test_an_edited_row_is_detected_at_that_row(tx: Tx) -> None:
    seqs = add_audit(tx, 3)
    tx.execute("alter table audit_log disable trigger audit_log_no_update_delete")
    tx.execute("update audit_log set action = 'changed' where chain_seq = %s", (seqs[1],))
    _, _, _, _, bad, ok = verify(tx, "audit_log")
    assert not ok and bad == seqs[1]


def test_a_deleted_row_is_detected_at_its_successor(tx: Tx) -> None:
    seqs = add_audit(tx, 3)
    tx.execute("alter table audit_log disable trigger audit_log_no_update_delete")
    tx.execute("delete from audit_log where chain_seq = %s", (seqs[1],))
    _, _, _, _, bad, ok = verify(tx, "audit_log")
    assert not ok and bad == seqs[2]


def test_a_reordered_pair_is_detected(tx: Tx) -> None:
    seqs = add_audit(tx, 2)
    tx.execute("alter table audit_log disable trigger audit_log_no_update_delete")
    tx.execute("drop index audit_log_chain_seq_key")
    tx.execute("update audit_log set chain_seq = -1 where chain_seq = %s", (seqs[0],))
    tx.execute("update audit_log set chain_seq = %s where chain_seq = %s", (seqs[0], seqs[1]))
    tx.execute("update audit_log set chain_seq = %s where chain_seq = -1", (seqs[1],))
    assert not verify(tx, "audit_log")[5]


def test_the_hash_does_not_depend_on_the_session_time_zone(tx: Tx) -> None:
    tx.execute("set local timezone = 'Asia/Kolkata'")
    add_audit(tx, 1)
    tx.execute("set local timezone = 'America/Los_Angeles'")
    assert verify(tx, "audit_log")[5]


def test_approvals_are_chained_too(tx: Tx) -> None:
    s: Seed = tx.seed
    args = (s.campaign_a, s.org_a, s.users["approver_a"])
    first = tx.execute(UNDECIDED, args)[0][0]
    second = tx.execute(UNDECIDED, args)[0][0]
    assert second == first + 1  # type: ignore[operator]
    assert verify(tx, "approvals")[5]


def test_a_refused_insert_leaves_the_chain_unbroken(tx: Tx) -> None:
    s: Seed = tx.seed
    tx.execute(
        "insert into approvals (campaign_id, org_id, approver_id, outcome, seconds_spent, "
        "revision_round, kind) values (%s, %s, %s, 'approve', 1, 7, 'evaluation')",
        (s.campaign_a, s.org_a, s.users["approver_a"]),
    )
    with pytest.raises(errors.UniqueViolation), tx.conn.transaction():
        tx.execute(
            "insert into approvals (campaign_id, org_id, approver_id, outcome, seconds_spent, "
            "revision_round, kind) values (%s, %s, %s, 'approve', 1, 7, 'evaluation')",
            (s.campaign_a, s.org_a, s.users["approver_a"]),
        )
    assert verify(tx, "approvals")[5]


def test_one_decision_per_round_is_a_database_rule(tx: Tx) -> None:
    s: Seed = tx.seed
    base = (s.campaign_a, s.org_a, s.users["approver_a"])
    sql = (
        "insert into approvals (campaign_id, org_id, approver_id, outcome, reason, seconds_spent, "
        "revision_round, kind) values (%s, %s, %s, %s, 'r', 1, 9, 'evaluation')"
    )
    tx.execute(sql, (*base, "revise"))
    for outcome in ("revise", "approve", "reject"):
        with pytest.raises(errors.UniqueViolation), tx.conn.transaction():
            tx.execute(sql, (*base, outcome))
    # An `undecided` row is no decision: it neither blocks nor is blocked.
    tx.execute(sql, (*base, "undecided"))
    # The next round is free.
    tx.execute(sql.replace(", 9,", ", 10,"), (*base, "approve"))


def test_calibration_rows_are_append_only_re_scores_not_one_per_round(tx: Tx) -> None:
    """Rubric section 6: two scorers decide one campaign, and after an amendment a scorer's
    RE-SCORE is a new row for the same campaign and round. The one-decision key is for evaluation
    organisations only; an index that refused these rows would block the rubric's own procedure."""
    s: Seed = tx.seed
    sql = (
        "insert into approvals (campaign_id, org_id, approver_id, outcome, reason, seconds_spent, "
        "revision_round, is_calibration, kind) values (%s, %s, %s, %s, 'r', 1, 0, true, "
        "'calibration')"
    )
    for scorer, outcome in [
        ("approver_a", "reject"),
        ("second_scorer", "approve"),
        ("approver_a", "approve"),  # the re-score after the rubric was amended
        ("approver_a", "approve"),
    ]:
        tx.execute(sql, (s.campaign_practice, s.org_practice, s.users[scorer], outcome))
    assert verify(tx, "approvals")[5]


def test_concurrent_inserts_make_one_valid_chain(db_url: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as c:
        before = c.execute("select coalesce(max(chain_seq), 0) from audit_log").fetchone()
    assert before is not None
    gate = threading.Barrier(8)

    def worker(i: int) -> None:
        with psycopg.connect(db_url, autocommit=True) as c:
            gate.wait()
            for j in range(5):
                c.execute(AUDIT, (f"c{i}-{j}", "{}"))

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(worker, range(8)))
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute("select * from app.chain_verify('audit_log')").fetchone()
        assert row is not None
        _, _, head_seq, _, bad, ok = row
        assert ok and bad is None and head_seq == before[0] + 40


def test_verification_is_not_for_the_api_roles_and_knows_only_two_tables(tx: Tx) -> None:
    with pytest.raises(errors.RaiseException), tx.conn.transaction():
        tx.execute("select * from app.chain_verify('runs')")
    tx.as_user("owner_a")
    with pytest.raises(errors.InsufficientPrivilege), tx.conn.transaction():
        tx.execute("select * from app.chain_verify('audit_log')")


def test_rows_before_the_chain_are_counted_not_hidden(tx: Tx) -> None:
    tx.execute("alter table audit_log disable trigger z_chain_stamp")
    tx.execute(
        "insert into audit_log (actor_type, actor_id, action, entity_type, entity_id) "
        "values ('service', 'test', 'unchained', 'test', 'x')"
    )
    _, unchained, _, _, bad, ok = verify(tx, "audit_log")
    assert unchained >= 1 and ok and bad is None
