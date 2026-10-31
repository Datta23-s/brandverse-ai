"""Give the fixed end-to-end user A the approver role in the organisations they own (stub only).

Deciding a campaign needs the `approver` role, which an owner does not hold (Rules.md T6). The
end-to-end approval specs sign in as user A, who creates their organisation through the UI and so
owns it; this adds the approver role beside it, idempotently. It refuses a managed Supabase host:
the live project's memberships are never written by a test.

Usage (from backend/):  uv run python -m scripts.e2e_grant_approver <database url>
"""

import sys
import uuid

import psycopg

from tests.db.target_guard import check_not_managed_supabase

USER_A = uuid.UUID("aaaaaaaa-0000-4000-8000-00000000000a")


def main(database_url: str) -> int:
    check_not_managed_supabase(database_url, "the e2e database URL")
    with psycopg.connect(database_url, autocommit=True) as conn:
        cur = conn.execute(
            "insert into org_members (org_id, user_id, role) "
            "select org_id, user_id, 'approver' from org_members "
            "where user_id = %s and role = 'owner' and revoked_at is null "
            "on conflict do nothing",
            (USER_A,),
        )
        print(f"approver role added in {cur.rowcount} organisation(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
