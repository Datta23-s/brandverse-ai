"""backend/scripts/flag_test_organisation.py: an organisation is flagged `is_test` through its
owner and its name, never by a stored id, and only when exactly one matches."""

from uuid import UUID, uuid4

import psycopg
import pytest

from scripts.flag_test_organisation import NotExactlyOne, flag

pytestmark = pytest.mark.db


def _owner_with_orgs(db_url: str, *names: str) -> tuple[str, dict[str, UUID]]:
    email = f"owner-{uuid4().hex[:8]}@example.invalid"
    user = uuid4()
    orgs: dict[str, UUID] = {}
    with psycopg.connect(db_url, autocommit=True) as c:
        c.execute("insert into auth.users (id, email) values (%s, %s)", (user, email))
        for name in names:
            row = c.execute(
                "insert into organisations (name) values (%s) returning id", (name,)
            ).fetchone()
            assert row is not None
            orgs.setdefault(name, row[0])
            c.execute(
                "insert into org_members (org_id, user_id, role) values (%s, %s, 'owner')",
                (row[0], user),
            )
    return email, orgs


def _is_test(db_url: str, org: UUID) -> bool:
    with psycopg.connect(db_url) as c:
        row = c.execute("select is_test from organisations where id = %s", (org,)).fetchone()
    assert row is not None
    return bool(row[0])


def test_only_the_named_org_of_that_owner_is_flagged(db_url: str) -> None:
    email, orgs = _owner_with_orgs(db_url, "Demo", "Evaluation brand 1")
    with psycopg.connect(db_url) as c:
        org_id, name, is_test = flag(c, email.upper(), "Demo")
    assert (org_id, name, is_test) == (orgs["Demo"], "Demo", True)
    assert _is_test(db_url, orgs["Demo"])
    assert not _is_test(db_url, orgs["Evaluation brand 1"])  # same owner, never touched


def test_an_org_the_user_does_not_own_is_not_flagged(db_url: str) -> None:
    email, _ = _owner_with_orgs(db_url, "Mine")
    _, theirs = _owner_with_orgs(db_url, "Theirs")
    with psycopg.connect(db_url) as c, pytest.raises(NotExactlyOne, match="0 organisations"):
        flag(c, email, "Theirs")
    assert not _is_test(db_url, theirs["Theirs"])


def test_an_ambiguous_name_changes_nothing(db_url: str) -> None:
    email = f"owner-{uuid4().hex[:8]}@example.invalid"
    user = uuid4()
    ids = []
    with psycopg.connect(db_url, autocommit=True) as c:
        c.execute("insert into auth.users (id, email) values (%s, %s)", (user, email))
        for _ in range(2):
            row = c.execute(
                "insert into organisations (name) values ('Twin') returning id"
            ).fetchone()
            assert row is not None
            ids.append(row[0])
            c.execute(
                "insert into org_members (org_id, user_id, role) values (%s, %s, 'owner')",
                (row[0], user),
            )
    with psycopg.connect(db_url) as c:
        with pytest.raises(NotExactlyOne, match="2 organisations"):
            flag(c, email, "Twin")
        c.rollback()
    assert not any(_is_test(db_url, i) for i in ids)
