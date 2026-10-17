"""Delete and truncate posture of EVERY table, stated explicitly (migrations 0010, 0011).

Merge-blocking (Rules.md §6), like the cross-tenant tests. The expected posture is written out
below by hand, never derived from what happens to be granted: a table added in a later phase
fails `test_every_table_has_a_stated_posture` until someone adds it here deliberately.

"Effective delete" means the role can actually remove rows: it holds the DELETE grant AND either
bypasses row-level security or has a DELETE (or ALL) policy. TRUNCATE ignores row-level security,
so for truncate the grant alone decides.
"""

import pytest

from tests.db.conftest import Tx

pytestmark = pytest.mark.db

ROLES = ("anon", "authenticated", "service_role")
NONE = {"anon": (False, False), "authenticated": (False, False), "service_role": (False, False)}
# Owners delete via RLS policy (Architecture.md §9 role matrix); the backend may delete; nobody
# truncates.
OWNER_AND_BACKEND_DELETE = {
    "anon": (False, False),
    "authenticated": (True, False),
    "service_role": (True, False),
}

# table -> role -> (effective delete, truncate)
EXPECTED: dict[str, dict[str, tuple[bool, bool]]] = {
    "approvals": NONE,  # append-only (0009)
    "audit_log": NONE,  # append-only (0008)
    "brands": OWNER_AND_BACKEND_DELETE,  # not once it has memory items or runs (FKs)
    "campaigns": NONE,  # 0010
    "llm_calls": NONE,  # raw logs, Rules.md A1 (0011)
    "memory_items": NONE,  # never hard-deleted, Memory.md §6.4 (0010)
    "org_members": NONE,  # revoked, never deleted (0010)
    "organisations": NONE,  # manual postgres procedure only (0010)
    "product_claims": OWNER_AND_BACKEND_DELETE,
    "products": OWNER_AND_BACKEND_DELETE,  # not once it has runs (FK)
    "runs": NONE,  # raw logs, Rules.md A1 (0010)
    "sector_constraints": NONE,  # retired, never deleted (0011)
    "sectors": NONE,  # registry keys (0011)
}

POSTURE = """
select has_table_privilege(%(role)s, c.oid, 'DELETE')
         and (r.rolbypassrls
              or exists (select 1 from pg_policies p
                         where p.schemaname = 'public' and p.tablename = c.relname
                           and p.cmd in ('DELETE', 'ALL')
                           and (%(role)s = any (p.roles) or 'public' = any (p.roles)))),
       has_table_privilege(%(role)s, c.oid, 'TRUNCATE')
from pg_class c, pg_roles r
where c.oid = ('public.' || %(table)s)::regclass and r.rolname = %(role)s
"""


def test_every_table_has_a_stated_posture(tx: Tx) -> None:
    tables = {
        r[0]
        for r in tx.execute(
            "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind in ('r', 'p')"
        )
    }
    missing, stale = sorted(tables - EXPECTED.keys()), sorted(EXPECTED.keys() - tables)
    assert not missing, f"tables with no stated delete/truncate posture: {missing}"
    assert not stale, f"posture stated for tables that do not exist: {stale}"


@pytest.mark.parametrize("table", sorted(EXPECTED))
@pytest.mark.parametrize("role", ROLES)
def test_table_posture(tx: Tx, table: str, role: str) -> None:
    row = tx.execute(POSTURE, {"role": role, "table": table})
    assert row, f"{table} not found"
    actual = (bool(row[0][0]), bool(row[0][1]))
    assert actual == EXPECTED[table][role], (
        f"{table} as {role}: (delete, truncate) is {actual}, stated {EXPECTED[table][role]}"
    )


# ── Functions in schema app (migration 0012 on) ──────────────────────────────────────────────
#
# A security definer function acts with its owner's rights, so its posture is part of the
# tables' posture. Stated by hand like the tables: a new app.* function fails
# `test_every_app_function_has_a_stated_posture` until it is added here deliberately.
# owner "migration" = the role that applied the migrations (postgres on Supabase).

ALL_API = frozenset({"anon", "authenticated", "service_role"})

# function -> (security definer, owner, roles that may EXECUTE)
EXPECTED_FUNCTIONS: dict[str, tuple[bool, str, frozenset[str]]] = {
    # Trigger functions: executable by PUBLIC (the default), harmless — a trigger function
    # cannot be called directly.
    "approvals_active_approver": (False, "migration", ALL_API),
    "approvals_practice_is_calibration": (False, "migration", ALL_API),
    "audit_log_append_only": (False, "migration", ALL_API),
    "campaigns_active_approver": (False, "migration", ALL_API),
    "sector_constraints_versioned": (False, "migration", ALL_API),
    # Read-only role checks every RLS policy calls (0003).
    "has_role": (True, "migration", ALL_API),
    "member_org_ids": (True, "migration", ALL_API),
    # 0012: signed-in users only; owned by the minimal role below.
    "create_organisation": (True, "app_org_creator", frozenset({"authenticated"})),
}


def test_every_app_function_has_a_stated_posture(tx: Tx) -> None:
    functions = {
        r[0]
        for r in tx.execute(
            "select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
            "where n.nspname = 'app'"
        )
    }
    missing = sorted(functions - EXPECTED_FUNCTIONS.keys())
    stale = sorted(EXPECTED_FUNCTIONS.keys() - functions)
    assert not missing, f"app functions with no stated posture: {missing}"
    assert not stale, f"posture stated for app functions that do not exist: {stale}"


@pytest.mark.parametrize("function", sorted(EXPECTED_FUNCTIONS))
def test_app_function_posture(tx: Tx, function: str) -> None:
    definer, owner, executors = EXPECTED_FUNCTIONS[function]
    migration_role = tx.execute("select current_user")[0][0]
    rows = tx.execute(
        "select p.prosecdef, pg_get_userbyid(p.proowner), p.proconfig, p.oid "
        "from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'app' and p.proname = %s",
        (function,),
    )
    assert len(rows) == 1, f"app.{function}: expected one function, found {len(rows)}"
    secdef, actual_owner, config, oid = rows[0]
    assert secdef is definer, f"app.{function}: security definer is {secdef}"
    assert actual_owner == (migration_role if owner == "migration" else owner)
    assert config == ['search_path=""'], f"app.{function}: search_path not pinned: {config}"
    can_execute = {
        role
        for role in sorted(ALL_API)
        if tx.execute("select has_function_privilege(%s, %s::oid, 'EXECUTE')", (role, oid))[0][0]
    }
    assert can_execute == executors, f"app.{function}: executable by {sorted(can_execute)}"


def test_create_organisation_owner_has_insert_on_two_tables_and_nothing_else(tx: Tx) -> None:
    """app_org_creator's rights ARE app.create_organisation's: insert two kinds of row, delete,
    truncate, update or read nothing, and no way past row-level security or into other roles."""
    attrs = tx.execute(
        "select rolsuper, rolcanlogin, rolbypassrls, rolcreaterole, rolcreatedb, rolinherit "
        "from pg_roles where rolname = 'app_org_creator'"
    )
    assert attrs == [(False, False, False, False, False, False)]
    held = tx.execute(
        "select c.relname, p.privilege_type "
        "from pg_class c join pg_namespace n on n.oid = c.relnamespace, "
        "lateral (select unnest(array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE',"
        "'REFERENCES','TRIGGER']) as privilege_type) p "
        "where n.nspname in ('public', 'auth', 'app') and c.relkind in ('r', 'p', 'v', 'm') "
        "and has_table_privilege('app_org_creator', c.oid, p.privilege_type) "
        "order by 1, 2"
    )
    assert held == [("org_members", "INSERT"), ("organisations", "INSERT")]
    memberships = tx.execute(
        "select b.rolname from pg_auth_members m join pg_roles b on b.oid = m.roleid "
        "join pg_roles r on r.oid = m.member where r.rolname = 'app_org_creator'"
    )
    assert memberships == []


# ── llm_calls, column by column (migration 0013) ─────────────────────────────────────────────
#
# The raw log the budget and the evaluation read (Rules.md A1). Written only by the backend's own
# role: no API role may insert a row or update any column (0013), and none may delete or truncate
# (0010, 0011, above). Every column is listed by hand; a new column fails
# `test_every_llm_calls_column_has_a_stated_posture` until it is added here deliberately.

LLM_CALLS_COLUMNS = {
    "id", "org_id", "run_id", "agent_id", "model", "tier", "route_reason",
    "input_tokens", "output_tokens", "cost_usd", "latency_ms", "created_at",
    # 0013
    "rate_limit_wait_ms", "outcome", "finished_at",
}  # fmt: skip


def test_every_llm_calls_column_has_a_stated_posture(tx: Tx) -> None:
    columns = {
        r[0]
        for r in tx.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'public' and table_name = 'llm_calls'"
        )
    }
    assert columns == LLM_CALLS_COLUMNS, (
        f"unstated: {sorted(columns - LLM_CALLS_COLUMNS)}; "
        f"stale: {sorted(LLM_CALLS_COLUMNS - columns)}"
    )


@pytest.mark.parametrize("column", sorted(LLM_CALLS_COLUMNS))
@pytest.mark.parametrize("role", ROLES)
def test_no_api_role_can_write_an_llm_calls_column(tx: Tx, column: str, role: str) -> None:
    rows = tx.execute(
        "select has_column_privilege(%s, 'public.llm_calls', %s, 'INSERT'), "
        "has_column_privilege(%s, 'public.llm_calls', %s, 'UPDATE')",
        (role, column, role, column),
    )
    assert rows == [(False, False)], f"{role} can write llm_calls.{column}: {rows}"


def test_signed_in_members_can_still_read_their_orgs_llm_calls(tx: Tx) -> None:
    rows = tx.execute("select has_table_privilege('authenticated', 'public.llm_calls', 'SELECT')")
    assert rows == [(True,)]
