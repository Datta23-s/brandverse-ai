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
