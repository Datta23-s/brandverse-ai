"""Mark one organisation as a test organisation (`organisations.is_test`, migration 0020), so every
evaluation read excludes it and everything in it.

Found through its owner, never by a stored id: the organisation must be owned (active owner
membership) by the user with this email AND have this name, and exactly one may match — the
owner may also own evaluation organisations, which must never be flagged by accident. Written with
the backend's own database role (DATABASE_URL); API roles cannot set the flag.

Usage (from backend/):
  uv run python -m scripts.flag_test_organisation --owner-email someone@example.com --name Datta
Idempotent: flagging an already flagged organisation reports it again and changes nothing.
"""

import argparse
import sys

import psycopg

from app.core.config import get_settings

FLAG_SQL = """
update public.organisations o set is_test = true
where o.name = %(name)s
  and exists (select 1 from public.org_members m join auth.users u on u.id = m.user_id
              where m.org_id = o.id and m.role = 'owner' and m.revoked_at is null
                and lower(u.email) = lower(%(email)s))
returning o.id, o.name, o.is_test
"""


class NotExactlyOne(Exception):
    """Zero or several organisations matched; nothing was changed."""


def flag(conn: psycopg.Connection, owner_email: str, name: str) -> tuple[object, ...]:
    """Flag the one organisation `owner_email` owns named `name`, in the caller's transaction.
    Raises NotExactlyOne (and the caller rolls back) unless exactly one matched."""
    rows = conn.execute(FLAG_SQL, {"email": owner_email, "name": name}).fetchall()
    if len(rows) != 1:
        raise NotExactlyOne(f"{len(rows)} organisations named {name!r} owned by that user")
    return rows[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    settings = get_settings()
    if settings.database_url is None:
        print("DATABASE_URL must be set in .env")
        return 1
    with psycopg.connect(settings.database_url.get_secret_value()) as conn:
        try:
            org_id, org_name, is_test = flag(conn, args.owner_email, args.name)
        except NotExactlyOne as e:
            conn.rollback()
            print(f"not flagged: {e}")
            return 1
    print(f"flagged is_test={is_test}: {org_name} ({org_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
