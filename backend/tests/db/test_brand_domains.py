"""A brand's own domains (migration 0039, threat V6): their shape, their audit and what they do to a
loaded product truth.

`brands.domains` decides which pages count as the brand's own, so it is a trust-deciding column that
only the brand's owner can know: the caller supplies it and every set or change is audited, with the
actor from the JWT, through the REST API as through ours (the pattern of brands.sector, 0019).
"""

import pytest
from psycopg import errors

from tests.db.conftest import Seed, Tx

pytestmark = pytest.mark.db

AUDIT = (
    "select actor_type, actor_id, details->'from', details->'to' from audit_log "
    "where action = 'brand.domains_set' and entity_id = %s order by id"
)


def _brand(tx: Tx, domains: str | None = None) -> str:
    if domains is None:
        rows = tx.execute(
            "insert into brands (org_id, name) values (%s, 'dom') returning id", (tx.seed.org_a,)
        )
    else:
        rows = tx.execute(
            "insert into brands (org_id, name, domains) "
            "values (%s, 'dom', %s::text[]) returning id",
            (tx.seed.org_a, domains),
        )
    return str(rows[0][0])


def _audit(tx: Tx, brand: str) -> list[tuple[object, ...]]:
    tx.execute("reset role")  # API roles cannot read audit_log
    return tx.execute(AUDIT, (brand,))


# ── The shape ────────────────────────────────────────────────────────────────────────────────


def test_a_brand_has_no_domains_by_default_and_that_is_not_an_error(tx: Tx) -> None:
    brand = _brand(tx)
    assert tx.execute("select domains from brands where id = %s", (brand,)) == [([],)]


@pytest.mark.parametrize(
    "domains",
    ["{neemans.com}", "{neemans.com,shop.neemans.com}", "{a-b.co.in}", "{x1.io,y2.example.org}"],
)
def test_well_formed_domains_are_accepted(tx: Tx, domains: str) -> None:
    brand = _brand(tx, domains)
    assert tx.execute("select cardinality(domains) from brands where id = %s", (brand,))[0][0] >= 1


@pytest.mark.parametrize(
    "domains",
    [
        "{https://neemans.com}",  # a scheme
        "{neemans.com/shop}",  # a path
        "{neemans.com:443}",  # a port
        "{www.neemans.com}",  # a leading www is not significant and is not stored
        "{Neemans.com}",  # upper case
        "{neemans}",  # one label
        "{-bad.com}",  # a label starting with a hyphen
        "{bad-.com}",
        "{a..com}",
        '{"",neemans.com}',  # an empty element
        '{"neemans .com"}',  # a space inside
    ],
)
def test_malformed_domains_are_refused(tx: Tx, domains: str) -> None:
    with pytest.raises(errors.CheckViolation, match="brands_domains_check"):
        _brand(tx, domains)


def test_at_most_ten_domains(tx: Tx) -> None:
    ten = "{" + ",".join(f"d{i}.example.com" for i in range(10)) + "}"
    eleven = "{" + ",".join(f"d{i}.example.com" for i in range(11)) + "}"
    _brand(tx, ten)
    with pytest.raises(errors.CheckViolation, match="brands_domains_check"):
        _brand(tx, eleven)


# ── The audit ────────────────────────────────────────────────────────────────────────────────


def test_a_user_setting_domains_through_rest_is_recorded_as_that_user(tx: Tx) -> None:
    brand = _brand(tx)
    tx.as_user("editor_a")
    tx.execute("update brands set domains = '{neemans.com}' where id = %s", (brand,))
    rows = _audit(tx, brand)
    assert rows == [("user", str(tx.seed.users["editor_a"]), [], ["neemans.com"])]


def test_a_change_records_from_and_to(tx: Tx) -> None:
    brand = _brand(tx, "{old.example.com}")
    tx.as_user("owner_a")
    tx.execute("update brands set domains = '{new.example.com}' where id = %s", (brand,))
    last = _audit(tx, brand)[-1]
    assert (last[2], last[3]) == (["old.example.com"], ["new.example.com"])


def test_creating_a_brand_with_domains_is_recorded_and_without_is_not(tx: Tx) -> None:
    with_domains = _brand(tx, "{neemans.com}")
    without = _brand(tx)
    assert len(_audit(tx, with_domains)) == 1
    assert _audit(tx, without) == []


def test_no_row_when_the_domains_do_not_change(tx: Tx) -> None:
    brand = _brand(tx, "{neemans.com}")
    tx.execute("update brands set name = 'renamed' where id = %s", (brand,))
    tx.execute("update brands set domains = '{neemans.com}' where id = %s", (brand,))
    assert len(_audit(tx, brand)) == 1  # the insert only


def test_a_user_cannot_write_a_domains_audit_row_directly(tx: Tx) -> None:
    tx.as_user("owner_a")
    with pytest.raises(errors.InsufficientPrivilege):
        tx.execute(
            "insert into audit_log (org_id, actor_type, actor_id, action, entity_type, entity_id) "
            "values (%s, 'user', 'x', 'brand.domains_set', 'brand', 'b')",
            (tx.seed.org_a,),
        )


def test_the_audit_writer_admits_the_two_brand_actions_and_nothing_else(tx: Tx) -> None:
    tx.execute("set local role app_audit_writer")
    for action in ("brand.sector_set", "brand.domains_set"):
        tx.execute(
            "insert into audit_log (org_id, actor_type, actor_id, action, entity_type, entity_id) "
            "values (%s, 'service', 'x', %s, 'brand', 'b')",
            (tx.seed.org_a, action),
        )
    with pytest.raises(errors.InsufficientPrivilege):
        tx.execute(
            "insert into audit_log (org_id, actor_type, actor_id, action, entity_type, entity_id) "
            "values (%s, 'service', 'x', 'approve', 'campaign', 'c')",
            (tx.seed.org_a,),
        )


def test_the_trigger_function_is_owned_by_the_audit_writer_and_is_security_definer(tx: Tx) -> None:
    row = tx.execute(
        "select pg_get_userbyid(p.proowner), p.prosecdef from pg_proc p "
        "join pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'app' and p.proname = 'audit_brand_domains'"
    )
    assert row == [("app_audit_writer", True)]


# ── The product truth carries the domains ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_loaded_product_truth_carries_the_brands_domains(db_url: str, seed: Seed) -> None:
    from psycopg import AsyncConnection

    from app.core.truth import load_product_truth

    async with await AsyncConnection.connect(db_url) as conn:
        try:
            none = await load_product_truth(conn, seed.org_a, seed.product_a)
            await conn.execute(
                "update brands set domains = '{brand-a.example,shop.brand-a.example}' "
                "where id = %s",
                (seed.brand_a,),
            )
            some = await load_product_truth(conn, seed.org_a, seed.product_a)
        finally:
            await conn.rollback()
    assert none is not None and none.brand_domains == ()
    assert some is not None
    assert some.brand_domains == ("brand-a.example", "shop.brand-a.example")
    claim = some.claims[0]
    assert some.first_party_evidence(claim) is not None or claim.evidence_url is None
