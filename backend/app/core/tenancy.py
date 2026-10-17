"""Membership checks, resolved by migration 0003's functions under the caller's identity.

Callers pass a connection from `app.core.db.as_user`, so `auth.uid()` inside the functions is the
verified caller. Nothing here reads org_members directly: the same functions every RLS policy
uses decide.
"""

from collections.abc import Sequence
from uuid import UUID

from psycopg import AsyncConnection


async def is_member(conn: AsyncConnection, org_id: UUID) -> bool:
    """The caller holds any unrevoked role in the organisation (app.member_org_ids, 0003)."""
    cur = await conn.execute("select %s::uuid in (select app.member_org_ids())", (org_id,))
    row = await cur.fetchone()
    return bool(row and row[0])


async def has_role(conn: AsyncConnection, org_id: UUID, roles: Sequence[str]) -> bool:
    """The caller holds one of `roles` in the organisation (app.has_role, 0003)."""
    cur = await conn.execute("select app.has_role(%s, %s::text[])", (org_id, list(roles)))
    row = await cur.fetchone()
    return bool(row and row[0])
