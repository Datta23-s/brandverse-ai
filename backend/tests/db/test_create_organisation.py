"""app.create_organisation (migration 0012): the only way a user gets their first organisation.

Merge-blocking with the rest of the database suite (Rules.md §6): it creates tenants, so it is
tenant isolation.
"""

import json
from uuid import UUID

import pytest
from psycopg import errors

from tests.db.conftest import Tx

pytestmark = pytest.mark.db


def create(tx: Tx, name: str) -> UUID:
    row = tx.execute("select app.create_organisation(%s)", (name,))
    value = row[0][0]
    assert isinstance(value, UUID)
    return value


def as_superuser(tx: Tx) -> None:
    tx.execute("reset role")
    tx.execute("select set_config('request.jwt.claims', '', true)")


def test_signed_in_user_creates_one_org_with_one_owner_membership_for_themselves(tx: Tx) -> None:
    user = tx.seed.users["outsider"]  # holds no role anywhere
    orgs_before = tx.execute("select count(*) from organisations")[0][0]
    tx.as_user("outsider")
    org = create(tx, "  Outsider Co  ")
    # Usable at once, under row-level security, as the new owner.
    assert tx.execute("select id, name from organisations") == [(org, "Outsider Co")]
    tx.execute(
        "insert into brands (org_id, name) values (%s, 'First brand')", (org,)
    )  # owners may write

    as_superuser(tx)
    assert tx.execute("select count(*) from organisations")[0][0] == orgs_before + 1
    members = tx.execute(
        "select user_id, role, granted_by, revoked_at from org_members where org_id = %s", (org,)
    )
    assert members == [(user, "owner", user, None)]
    assert tx.execute("select is_practice from organisations where id = %s", (org,)) == [(False,)]


def test_only_the_name_is_accepted(tx: Tx) -> None:
    # No user-id parameter: the owner can only ever be the caller.
    rows = tx.execute(
        "select pg_get_function_arguments(p.oid) from pg_proc p "
        "join pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'app' and p.proname = 'create_organisation'"
    )
    assert rows == [("p_name text",)]


def test_forged_claims_cannot_make_someone_else_owner(tx: Tx) -> None:
    # Extra claims are ignored; the owner is `sub`, the identity the token was issued to.
    victim = tx.seed.users["owner_a"]
    tx.as_user("outsider", user_id=str(victim), owner=str(victim))
    org = create(tx, "Mine")
    as_superuser(tx)
    owners = tx.execute("select user_id from org_members where org_id = %s", (org,))
    assert owners == [(tx.seed.users["outsider"],)]


def test_signed_out_caller_cannot_execute_it(tx: Tx) -> None:
    tx.execute("set local role anon")
    with pytest.raises(errors.InsufficientPrivilege, match="permission denied"):
        tx.execute("select app.create_organisation('Anon Co')")


def test_authenticated_role_without_an_identity_is_refused(tx: Tx) -> None:
    tx.set_claims({"role": "authenticated"})  # no sub
    with pytest.raises(errors.InsufficientPrivilege, match="not signed in"):
        tx.execute("select app.create_organisation('Nobody Co')")


def test_service_role_cannot_execute_it(tx: Tx) -> None:
    tx.as_service_role()
    with pytest.raises(errors.InsufficientPrivilege, match="permission denied"):
        tx.execute("select app.create_organisation('Backend Co')")


@pytest.mark.parametrize("name", ["", "   ", "x" * 121])
def test_name_must_be_1_to_120_characters(tx: Tx, name: str) -> None:
    tx.as_user("outsider")
    with pytest.raises(errors.InvalidParameterValue, match="1 to 120"):
        tx.execute("select app.create_organisation(%s)", (name,))


@pytest.mark.parametrize(
    "setting",
    [
        {"request.jwt.claims": json.dumps({"sub": "11111111-1111-4111-8111-111111111111"})},
        {"request.jwt.claim.sub": "22222222-2222-4222-8222-222222222222"},
        {
            "request.jwt.claim.sub": "33333333-3333-4333-8333-333333333333",
            "request.jwt.claims": json.dumps({"sub": "44444444-4444-4444-8444-444444444444"}),
        },
        {"request.jwt.claims": json.dumps({"role": "authenticated"})},
        {"request.jwt.claim.sub": ""},
    ],
)
def test_owner_is_derived_exactly_as_auth_uid(tx: Tx, setting: dict[str, str]) -> None:
    """The function mirrors auth.uid()'s expression (0012 header); they must never disagree."""
    for uid in (
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
        "44444444-4444-4444-8444-444444444444",
    ):
        tx.execute("insert into auth.users (id) values (%s) on conflict do nothing", (uid,))
    tx.execute("set local role authenticated")
    for key, value in setting.items():
        tx.execute("select set_config(%s, %s, true)", (key, value))
    expected = tx.execute("select auth.uid()")[0][0]
    if expected is None:
        with pytest.raises(errors.InsufficientPrivilege, match="not signed in"):
            tx.execute("select app.create_organisation('X')")
        return
    org = create(tx, "X")
    as_superuser(tx)
    assert tx.execute("select user_id from org_members where org_id = %s", (org,)) == [(expected,)]


def test_api_roles_cannot_act_as_the_function_owner(tx: Tx) -> None:
    for role in ("anon", "authenticated", "service_role"):
        member = tx.execute("select pg_has_role(%s, 'app_org_creator', 'MEMBER')", (role,))
        assert member == [(False,)], f"{role} is a member of app_org_creator"
