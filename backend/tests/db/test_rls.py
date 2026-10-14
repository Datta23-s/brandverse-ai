"""Tenant isolation, RBAC and schema safety, against the real migrations.

Merge-blocking (Rules.md §6). Runs only with a database (see conftest.py); in CI it always runs.
Each test is one rolled-back transaction. Setup runs as the superuser before `as_user` switches
to the `authenticated` role with JWT claims set through `request.jwt.claims`, as on Supabase.
"""

import psycopg
import pytest
from psycopg import errors

from app.evaluation.approvals import EVALUATION_APPROVALS_SQL
from tests.db.conftest import Tx

pytestmark = pytest.mark.db

ALL_ROLES = ["owner", "editor", "approver", "viewer"]


def _raises_rls(tx: Tx, query: str, params: tuple[object, ...] = ()) -> None:
    with pytest.raises(errors.InsufficientPrivilege, match="row-level security"):
        tx.execute(query, params)


def _raises_permission(tx: Tx, query: str, params: tuple[object, ...] = ()) -> None:
    with pytest.raises(errors.InsufficientPrivilege, match="permission denied"):
        tx.execute(query, params)


# ── Extensions and indexes build for real ────────────────────────────────────────────────


def test_pgvector_and_trigram_extensions_installed(tx: Tx) -> None:
    names = {r[0] for r in tx.execute("select extname from pg_extension")}
    assert {"vector", "pg_trgm"} <= names


def test_memory_hnsw_and_gin_indexes_exist(tx: Tx) -> None:
    methods = {
        r[0]
        for r in tx.execute(
            "select am.amname from pg_index i join pg_class c on c.oid = i.indexrelid "
            "join pg_am am on am.oid = c.relam where i.indrelid = 'memory_items'::regclass"
        )
    }
    assert {"hnsw", "gin"} <= methods


def test_every_public_table_has_rls_enabled(tx: Tx) -> None:
    rows = tx.execute(
        "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity"
    )
    assert rows == [], f"tables without RLS: {rows}"


# ── Tenant consistency (composite FKs, sector FK) ────────────────────────────────────────


def test_child_row_cannot_point_at_another_orgs_parent(tx: Tx) -> None:
    with pytest.raises(errors.ForeignKeyViolation):
        tx.execute(
            "insert into products (org_id, brand_id, name) values (%s, %s, 'x')",
            (tx.seed.org_a, tx.seed.brand_b),
        )


def test_run_cannot_reference_another_orgs_product(tx: Tx) -> None:
    with pytest.raises(errors.ForeignKeyViolation):
        tx.execute(
            "insert into runs (org_id, product_id, request) values (%s, %s, '{}')",
            (tx.seed.org_b, tx.seed.product_a),
        )


def test_misspelled_sector_rejected(tx: Tx) -> None:
    with pytest.raises(errors.ForeignKeyViolation):
        tx.execute(
            "insert into brands (org_id, name, sector) values (%s, 'x', 'Footwear ')",
            (tx.seed.org_a,),
        )


# ── RBAC through RLS ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("user", ["owner_a", "editor_a", "viewer_a", "approver_a"])
def test_every_role_reads_only_its_own_orgs(tx: Tx, user: str) -> None:
    tx.as_user(user)
    rows = {r[0] for r in tx.execute("select id from brands")}
    # approver_a also approves in the practice org (rubric §6); nobody here sees org B.
    practice = {tx.seed.brand_practice} if user == "approver_a" else set()
    assert rows == {tx.seed.brand_a} | practice


def test_viewer_cannot_insert(tx: Tx) -> None:
    tx.as_user("viewer_a")
    _raises_rls(tx, "insert into brands (org_id, name) values (%s, 'v')", (tx.seed.org_a,))


def test_approver_cannot_insert(tx: Tx) -> None:
    tx.as_user("approver_a")
    _raises_rls(tx, "insert into brands (org_id, name) values (%s, 'v')", (tx.seed.org_a,))


def test_editor_can_insert(tx: Tx) -> None:
    tx.as_user("editor_a")
    tx.execute("insert into brands (org_id, name) values (%s, 'e')", (tx.seed.org_a,))


def test_editor_cannot_insert_into_another_org(tx: Tx) -> None:
    tx.as_user("editor_a")
    _raises_rls(tx, "insert into brands (org_id, name) values (%s, 'e')", (tx.seed.org_b,))


def test_editor_cannot_delete(tx: Tx) -> None:
    tx.as_user("editor_a")
    assert tx.execute("delete from brands where id = %s returning id", (tx.seed.brand_a,)) == []


def test_owner_can_delete_brand_without_runs(tx: Tx) -> None:
    brand = tx.execute(
        "insert into brands (org_id, name) values (%s, 'unused') returning id", (tx.seed.org_a,)
    )[0][0]
    tx.as_user("owner_a")
    deleted = tx.execute("delete from brands where id = %s returning id", (brand,))
    assert len(deleted) == 1


def test_product_with_runs_cannot_be_deleted(tx: Tx) -> None:
    # Runs are the evaluation's raw logs (Rules.md A1); deleting a product must not remove them.
    with pytest.raises(errors.ForeignKeyViolation):
        tx.execute("delete from products where id = %s", (tx.seed.product_a,))


def test_non_member_with_forged_org_claim_sees_nothing(tx: Tx) -> None:
    # Policies never read an org claim; a forged one grants nothing.
    tx.as_user("outsider", org_id=str(tx.seed.org_a))
    assert tx.execute("select id from brands") == []
    assert tx.execute("select id from organisations") == []


def test_org_claim_cannot_narrow_or_redirect_a_member(tx: Tx) -> None:
    tx.as_user("viewer_a", org_id=str(tx.seed.org_b))
    assert [r[0] for r in tx.execute("select id from brands")] == [tx.seed.brand_a]


def test_member_of_two_orgs_reads_both(tx: Tx) -> None:
    tx.as_user("agency")
    rows = {r[0] for r in tx.execute("select id from brands")}
    assert rows == {tx.seed.brand_a, tx.seed.brand_b}


def test_member_of_two_orgs_has_each_orgs_role_only(tx: Tx) -> None:
    # agency is editor in A, viewer in B: a role in one org confers nothing in the other.
    tx.as_user("agency")
    tx.execute("insert into brands (org_id, name) values (%s, 'ok')", (tx.seed.org_a,))
    _raises_rls(tx, "insert into brands (org_id, name) values (%s, 'no')", (tx.seed.org_b,))


def test_no_sub_claim_sees_nothing(tx: Tx) -> None:
    tx.set_claims({"role": "authenticated"})
    assert tx.execute("select id from brands") == []
    assert tx.execute("select id from organisations") == []


def test_non_uuid_sub_fails_closed(tx: Tx) -> None:
    # auth.uid() casts sub to uuid and raises rather than matching anything.
    tx.set_claims({"sub": "not-a-uuid", "role": "authenticated"})
    with pytest.raises(errors.InvalidTextRepresentation):
        tx.execute("select id from brands")


def test_revoked_member_loses_access(tx: Tx) -> None:
    tx.execute(
        "update org_members set revoked_at = now(), revoked_by = %s "
        "where user_id = %s and org_id = %s",
        (tx.seed.users["owner_a"], tx.seed.users["viewer_a"], tx.seed.org_a),
    )
    tx.as_user("viewer_a")
    assert tx.execute("select id from brands") == []


def test_anon_sees_nothing(tx: Tx) -> None:
    tx.execute("set local role anon")
    assert tx.execute("select id from brands") == []


def test_editor_cannot_add_members(tx: Tx) -> None:
    tx.as_user("editor_a")
    _raises_rls(
        tx,
        "insert into org_members (org_id, user_id, role) values (%s, %s, 'owner')",
        (tx.seed.org_a, tx.seed.users["outsider"]),
    )


def test_owner_can_add_member(tx: Tx) -> None:
    tx.as_user("owner_a")
    tx.execute(
        "insert into org_members (org_id, user_id, role) values (%s, %s, 'viewer')",
        (tx.seed.org_a, tx.seed.users["outsider"]),
    )


def test_only_owner_updates_org_budget(tx: Tx) -> None:
    tx.as_user("editor_a")
    updated = tx.execute(
        "update organisations set monthly_budget_cap_usd = 999 where id = %s returning id",
        (tx.seed.org_a,),
    )
    assert updated == []


def test_users_cannot_write_llm_calls(tx: Tx) -> None:
    tx.as_user("owner_a")
    _raises_rls(
        tx,
        "insert into llm_calls (org_id, run_id, model, tier, route_reason) "
        "values (%s, %s, 'm', 'low', 'r')",
        (tx.seed.org_a, tx.seed.run_a),
    )


# ── Memory: revoke, never approve ────────────────────────────────────────────────────────


def _memory_item(tx: Tx) -> object:
    rows = tx.execute(
        "insert into memory_items (org_id, brand_id, kind, content, provenance) "
        "values (%s, %s, 'insight', 'x', 'human') returning id",
        (tx.seed.org_a, tx.seed.brand_a),
    )
    return rows[0][0]


def test_editor_cannot_approve_memory(tx: Tx) -> None:
    item = _memory_item(tx)
    tx.as_user("editor_a")
    _raises_permission(tx, "update memory_items set approved = true where id = %s", (item,))


def test_editor_can_revoke_memory_as_themself(tx: Tx) -> None:
    item = _memory_item(tx)
    tx.as_user("editor_a")
    tx.execute(
        "update memory_items set revoked = true, revoked_by = %s, revoked_at = now(), "
        "revoked_reason = 'wrong' where id = %s",
        (tx.seed.users["editor_a"], item),
    )


def test_editor_cannot_revoke_in_someone_elses_name(tx: Tx) -> None:
    item = _memory_item(tx)
    tx.as_user("editor_a")
    _raises_rls(
        tx,
        "update memory_items set revoked_by = %s where id = %s",
        (tx.seed.users["owner_a"], item),
    )


def test_viewer_cannot_revoke_memory(tx: Tx) -> None:
    item = _memory_item(tx)
    tx.as_user("viewer_a")
    updated = tx.execute(
        "update memory_items set revoked_reason = 'x' where id = %s returning id", (item,)
    )
    assert updated == []


def test_deleting_product_nulls_only_memory_product_id(tx: Tx) -> None:
    product = tx.execute(
        "insert into products (org_id, brand_id, name) values (%s, %s, 'P2') returning id",
        (tx.seed.org_a, tx.seed.brand_a),
    )[0][0]
    item = tx.execute(
        "insert into memory_items (org_id, product_id, kind, content, provenance) "
        "values (%s, %s, 'insight', 'y', 'human') returning id",
        (tx.seed.org_a, product),
    )[0][0]
    tx.execute("delete from products where id = %s", (product,))
    row = tx.execute("select org_id, product_id from memory_items where id = %s", (item,))
    assert row == [(tx.seed.org_a, None)]


# ── Approval (database backstop for Rules.md T6) ─────────────────────────────────────────


def _approve(tx: Tx, approver: str) -> None:
    tx.execute(
        "insert into campaigns (org_id, run_id, status, approved_by, approved_at) "
        "values (%s, %s, 'approved', %s, now())",
        (tx.seed.org_a, tx.seed.run_a, tx.seed.users[approver]),
    )


def test_this_orgs_approver_can_approve(tx: Tx) -> None:
    _approve(tx, "approver_a")


@pytest.mark.parametrize("user", ["owner_a", "editor_a", "viewer_a", "approver_b"])
def test_non_approver_or_other_org_cannot_approve(tx: Tx, user: str) -> None:
    with pytest.raises(errors.RaiseException, match="active approver"):
        _approve(tx, user)


def test_approver_fk_holds_even_without_trigger(tx: Tx) -> None:
    tx.execute("alter table campaigns disable trigger campaigns_active_approver")
    with pytest.raises(errors.ForeignKeyViolation):
        _approve(tx, "viewer_a")


def test_approved_campaign_must_name_approver(tx: Tx) -> None:
    with pytest.raises(errors.CheckViolation):
        tx.execute(
            "insert into campaigns (org_id, run_id, status) values (%s, %s, 'approved')",
            (tx.seed.org_a, tx.seed.run_a),
        )


def test_revoked_approver_cannot_approve(tx: Tx) -> None:
    tx.execute(
        "update org_members set revoked_at = now(), revoked_by = %s "
        "where user_id = %s and role = 'approver'",
        (tx.seed.users["owner_a"], tx.seed.users["approver_a"]),
    )
    with pytest.raises(errors.RaiseException, match="active approver"):
        _approve(tx, "approver_a")


def test_approver_membership_cannot_be_deleted_once_used(tx: Tx) -> None:
    _approve(tx, "approver_a")
    with pytest.raises(errors.ForeignKeyViolation):
        tx.execute(
            "delete from org_members where user_id = %s and role = 'approver'",
            (tx.seed.users["approver_a"],),
        )


# ── Audit log: append-only ───────────────────────────────────────────────────────────────


def _audit_row(tx: Tx) -> None:
    tx.execute(
        "insert into audit_log (org_id, actor_type, actor_id, action, entity_type, entity_id) "
        "values (%s, 'user', %s, 'approve', 'campaign', 'c1')",
        (tx.seed.org_a, str(tx.seed.users["owner_a"])),
    )


def test_service_role_can_append_audit(tx: Tx) -> None:
    tx.as_service_role()
    tx.execute(
        "insert into audit_log (actor_type, actor_id, action, entity_type, entity_id) "
        "values ('service', 'api', 'x', 'y', 'z')"
    )


@pytest.mark.parametrize(
    "stmt", ["update audit_log set action = 'edited'", "delete from audit_log"]
)
def test_service_role_cannot_alter_audit(tx: Tx, stmt: str) -> None:
    _audit_row(tx)
    tx.as_service_role()
    _raises_permission(tx, stmt)


def test_authenticated_cannot_insert_audit(tx: Tx) -> None:
    tx.as_user("owner_a")
    _raises_permission(
        tx,
        "insert into audit_log (actor_type, actor_id, action, entity_type, entity_id) "
        "values ('user', 'u', 'x', 'y', 'z')",
    )


@pytest.mark.parametrize(
    "stmt",
    ["update audit_log set action = 'edited'", "delete from audit_log", "truncate audit_log"],
)
def test_table_owner_blocked_by_trigger(tx: Tx, stmt: str) -> None:
    _audit_row(tx)
    with pytest.raises(errors.RaiseException, match="append-only"):
        tx.execute(stmt)


def test_org_owner_reads_own_audit_editor_does_not(tx: Tx) -> None:
    _audit_row(tx)
    tx.execute("savepoint s")
    tx.as_user("owner_a")
    assert len(tx.execute("select id from audit_log")) == 1
    tx.execute("rollback to savepoint s")
    tx.as_user("editor_a")
    assert tx.execute("select id from audit_log") == []


# ── Sector constraint registry ───────────────────────────────────────────────────────────


def _rule(tx: Tx) -> object:
    rows = tx.execute(
        "insert into sector_constraints (sector_id, kind, match_type, pattern, source_url, "
        "effective_from, created_by) values ('health-supplements', 'prohibited_phrase', 'phrase', "
        "'test-only-phrase', 'https://example.invalid/test-only', current_date, %s) returning id",
        (tx.seed.users["admin"],),
    )
    return rows[0][0]


def test_users_cannot_write_registry(tx: Tx) -> None:
    tx.as_user("owner_a")
    _raises_permission(
        tx,
        "insert into sector_constraints (sector_id, kind, match_type, pattern, source_url, "
        "effective_from, created_by) values ('footwear', 'prohibited_phrase', 'phrase', 'x', 'u', "
        "current_date, %s)",
        (tx.seed.users["owner_a"],),
    )


def test_registry_readable_by_members(tx: Tx) -> None:
    _rule(tx)
    tx.as_user("viewer_a")
    assert len(tx.execute("select id from sector_constraints")) == 1


def test_registry_rule_text_cannot_change(tx: Tx) -> None:
    rule = _rule(tx)
    with pytest.raises(errors.RaiseException, match="versioned"):
        tx.execute("update sector_constraints set pattern = 'other' where id = %s", (rule,))


def test_registry_rule_can_be_retired_not_deleted(tx: Tx) -> None:
    rule = _rule(tx)
    tx.execute("update sector_constraints set retired_at = current_date where id = %s", (rule,))
    with pytest.raises(errors.RaiseException, match="never deleted"):
        tx.execute("delete from sector_constraints where id = %s", (rule,))


def test_disclaimer_rule_needs_disclaimer_text(tx: Tx) -> None:
    with pytest.raises(errors.CheckViolation):
        tx.execute(
            "insert into sector_constraints (sector_id, kind, match_type, source_url, "
            "effective_from, created_by) values ('health-supplements', 'required_disclaimer', "
            "'phrase', 'u', current_date, %s)",
            (tx.seed.users["admin"],),
        )


def test_registry_author_must_be_a_user(tx: Tx) -> None:
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        tx.execute(
            "insert into sector_constraints (sector_id, kind, match_type, pattern, source_url, "
            "effective_from, created_by) values ('footwear', 'prohibited_phrase', 'phrase', 'x', "
            "'u', current_date, 'A7')"
        )


# ── Organisation caps ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "column",
    ["monthly_budget_cap_usd", "rate_limit_runs_per_hour", "rate_limit_requests_per_minute"],
)
def test_org_caps_must_be_positive(tx: Tx, column: str) -> None:
    with pytest.raises(errors.CheckViolation):
        tx.execute(f"update organisations set {column} = 0 where id = %s", (tx.seed.org_a,))  # noqa: S608 - column from fixed list


# ── Approval decisions (docs/approval-rubric.md §5) ──────────────────────────────────────

_DECISION = (
    "insert into approvals (campaign_id, org_id, approver_id, outcome, failed_gate, reason, "
    "seconds_spent, arm_inferred, arm_inference_note) values (%s, %s, %s, %s, %s, %s, 120, %s, %s)"
)


def _decide(
    tx: Tx,
    approver: str = "approver_a",
    outcome: str = "reject",
    failed_gate: str | None = "T1.3",
    reason: str | None = "unsourced claim",
    arm_inferred: bool = False,
    note: str | None = None,
) -> None:
    tx.execute(
        _DECISION,
        (
            tx.seed.campaign_a,
            tx.seed.org_a,
            tx.seed.users[approver],
            outcome,
            failed_gate,
            reason,
            arm_inferred,
            note,
        ),
    )


@pytest.mark.parametrize("outcome", ["approve", "revise", "reject", "undecided"])
def test_approver_records_every_outcome(tx: Tx, outcome: str) -> None:
    if outcome == "approve":
        _decide(tx, outcome=outcome, failed_gate=None, reason=None)
    else:
        _decide(tx, outcome=outcome)


def test_revised_campaign_gets_a_further_decision(tx: Tx) -> None:
    _decide(tx, outcome="revise")
    _decide(tx, outcome="approve", failed_gate=None, reason=None)
    rows = tx.execute(
        "select outcome from approvals where campaign_id = %s order by decided_at",
        (tx.seed.campaign_a,),
    )
    assert len(rows) == 2


@pytest.mark.parametrize("user", ["owner_a", "editor_a", "viewer_a", "approver_b", "agency"])
def test_non_approver_or_other_org_cannot_decide(tx: Tx, user: str) -> None:
    with pytest.raises(errors.RaiseException, match="active approver"):
        _decide(tx, approver=user)


def test_decision_approver_fk_holds_even_without_trigger(tx: Tx) -> None:
    tx.execute("alter table approvals disable trigger approvals_active_approver")
    with pytest.raises(errors.ForeignKeyViolation):
        _decide(tx, approver="viewer_a")


def test_revoked_approver_cannot_decide(tx: Tx) -> None:
    tx.execute(
        "update org_members set revoked_at = now(), revoked_by = %s "
        "where user_id = %s and role = 'approver'",
        (tx.seed.users["owner_a"], tx.seed.users["approver_a"]),
    )
    with pytest.raises(errors.RaiseException, match="active approver"):
        _decide(tx)


def test_decision_cannot_point_at_another_orgs_campaign(tx: Tx) -> None:
    with pytest.raises(errors.ForeignKeyViolation):
        tx.execute(
            _DECISION,
            (
                tx.seed.campaign_a,
                tx.seed.org_b,
                tx.seed.users["approver_b"],
                "reject",
                "T1.3",
                "x",
                False,
                None,
            ),
        )


@pytest.mark.parametrize("reason", [None, "  "])
def test_non_approval_needs_a_reason(tx: Tx, reason: str | None) -> None:
    with pytest.raises(errors.CheckViolation):
        _decide(tx, outcome="reject", reason=reason)


def test_approval_has_no_failed_gate(tx: Tx) -> None:
    with pytest.raises(errors.CheckViolation):
        _decide(tx, outcome="approve", failed_gate="T1.3", reason=None)


def test_inferred_arm_needs_a_note(tx: Tx) -> None:
    with pytest.raises(errors.CheckViolation):
        _decide(tx, arm_inferred=True, note=None)


def test_inferred_arm_with_note_is_recorded(tx: Tx) -> None:
    _decide(tx, arm_inferred=True, note="evidence pack empty")


def test_users_cannot_write_decisions(tx: Tx) -> None:
    tx.as_user("approver_a")
    _raises_rls(
        tx,
        _DECISION,
        (
            tx.seed.campaign_a,
            tx.seed.org_a,
            tx.seed.users["approver_a"],
            "reject",
            "T1.3",
            "x",
            False,
            None,
        ),
    )


@pytest.mark.parametrize(
    "stmt", ["update approvals set outcome = 'approve'", "delete from approvals"]
)
def test_service_role_cannot_alter_decisions(tx: Tx, stmt: str) -> None:
    _decide(tx)
    tx.as_service_role()
    _raises_permission(tx, stmt)


def test_decisions_readable_only_by_own_org(tx: Tx) -> None:
    _decide(tx)
    tx.execute("savepoint s")
    tx.as_user("viewer_a")
    assert len(tx.execute("select id from approvals")) == 1
    tx.execute("rollback to savepoint s")
    tx.as_user("approver_b")
    assert tx.execute("select id from approvals") == []


# ── Calibration rows (docs/approval-rubric.md §6) ────────────────────────────────────────


def _calibration_decision(tx: Tx, scorer: str, campaign: object, org: object) -> None:
    tx.execute(
        "insert into approvals (campaign_id, org_id, approver_id, outcome, seconds_spent, "
        "is_calibration) values (%s, %s, %s, 'approve', 60, true)",
        (campaign, org, tx.seed.users[scorer]),
    )


def test_second_scorer_decides_in_practice_org_only(tx: Tx) -> None:
    _calibration_decision(tx, "second_scorer", tx.seed.campaign_practice, tx.seed.org_practice)
    with pytest.raises(errors.RaiseException, match="active approver"):
        _calibration_decision(tx, "second_scorer", tx.seed.campaign_a, tx.seed.org_a)


def test_calibration_row_never_reaches_evaluation_query(tx: Tx) -> None:
    _decide(tx)  # the one evaluation decision
    _calibration_decision(tx, "approver_a", tx.seed.campaign_practice, tx.seed.org_practice)
    _calibration_decision(tx, "second_scorer", tx.seed.campaign_practice, tx.seed.org_practice)
    # Flagged inside an evaluation org too: the filter is the flag, not the org id.
    _calibration_decision(tx, "approver_a", tx.seed.campaign_a, tx.seed.org_a)
    tx.as_service_role()
    rows = tx.execute(
        EVALUATION_APPROVALS_SQL,
        {"org_ids": [tx.seed.org_a, tx.seed.org_b, tx.seed.org_practice]},
    )
    assert [(r[1], r[4]) for r in rows] == [(tx.seed.campaign_a, "reject")]


def _unflagged_practice_decision(tx: Tx) -> None:
    tx.execute(
        "insert into approvals (campaign_id, org_id, approver_id, outcome, seconds_spent, "
        "is_calibration) values (%s, %s, %s, 'approve', 60, false)",
        (tx.seed.campaign_practice, tx.seed.org_practice, tx.seed.users["second_scorer"]),
    )


def test_unflagged_practice_approval_is_rejected(tx: Tx) -> None:
    with pytest.raises(errors.RaiseException, match="practice organisation"):
        _unflagged_practice_decision(tx)


def test_unflagged_practice_approval_never_reaches_evaluation_query(tx: Tx) -> None:
    # The harness excludes practice orgs itself, even with the database trigger bypassed.
    tx.execute("alter table approvals disable trigger approvals_practice_is_calibration")
    _unflagged_practice_decision(tx)
    _decide(tx)
    tx.as_service_role()
    rows = tx.execute(
        EVALUATION_APPROVALS_SQL,
        {"org_ids": [tx.seed.org_a, tx.seed.org_b, tx.seed.org_practice]},
    )
    assert [(r[1], r[4]) for r in rows] == [(tx.seed.campaign_a, "reject")]


@pytest.mark.parametrize("org", ["org_a", "org_practice"])
def test_owner_cannot_change_is_practice(tx: Tx, org: str) -> None:
    tx.execute(
        "insert into org_members (org_id, user_id, role) values (%s, %s, 'owner')",
        (tx.seed.org_practice, tx.seed.users["owner_a"]),
    )
    tx.as_user("owner_a")
    _raises_permission(
        tx,
        "update organisations set is_practice = not is_practice where id = %s",
        (getattr(tx.seed, org),),
    )


def test_owner_still_updates_org_budget(tx: Tx) -> None:
    tx.as_user("owner_a")
    updated = tx.execute(
        "update organisations set monthly_budget_cap_usd = 75 where id = %s returning id",
        (tx.seed.org_a,),
    )
    assert len(updated) == 1
