"""A brand's sector decides which registry rules bind its output, so every set or change is
audited, with the actor from the JWT (migration 0019) — through the REST API as through ours.
"""

import pytest
from psycopg import errors

from tests.db.conftest import Tx

pytestmark = pytest.mark.db

AUDIT = (
    "select actor_type, actor_id, details->>'from', details->>'to' from audit_log "
    "where action = 'brand.sector_set' and entity_id = %s order by id"
)


def _brand(tx: Tx, sector: str | None = None) -> str:
    rows = tx.execute(
        "insert into brands (org_id, name, sector) values (%s, 'audited', %s) returning id",
        (tx.seed.org_a, sector),
    )
    return str(rows[0][0])


def _audit(tx: Tx, brand: str) -> list[tuple[object, ...]]:
    tx.execute("reset role")  # API roles cannot read audit_log; the test reads as the owner role
    return tx.execute(AUDIT, (brand,))


def test_a_user_changing_a_sector_through_rest_is_recorded_as_that_user(tx: Tx) -> None:
    brand = _brand(tx, "footwear")
    tx.as_user("editor_a")
    tx.execute("update brands set sector = 'health-supplements' where id = %s", (brand,))
    editor = str(tx.seed.users["editor_a"])
    assert _audit(tx, brand)[-1] == ("user", editor, "footwear", "health-supplements")


def test_creating_a_brand_with_a_sector_is_recorded(tx: Tx) -> None:
    tx.as_user("editor_a")
    brand = _brand(tx, "kitchenware")
    assert _audit(tx, brand) == [("user", str(tx.seed.users["editor_a"]), None, "kitchenware")]


def test_no_row_when_the_sector_does_not_change(tx: Tx) -> None:
    tx.as_user("editor_a")
    brand = _brand(tx)
    tx.execute("update brands set name = 'renamed' where id = %s", (brand,))
    assert _audit(tx, brand) == []
    tx.as_user("editor_a")
    tx.execute("update brands set sector = 'footwear' where id = %s", (brand,))
    tx.execute("update brands set sector = 'footwear' where id = %s", (brand,))
    assert len(_audit(tx, brand)) == 1


def test_a_change_with_no_identity_is_recorded_as_the_service(tx: Tx) -> None:
    brand = _brand(tx)
    tx.execute("update brands set sector = 'footwear' where id = %s", (brand,))
    actor_type, actor_id, _, _ = _audit(tx, brand)[0]
    assert actor_type == "service"
    assert actor_id == tx.execute("select session_user")[0][0]


def test_a_user_cannot_write_a_sector_audit_row_directly(tx: Tx) -> None:
    tx.as_user("owner_a")
    with pytest.raises(errors.InsufficientPrivilege):
        tx.execute(
            "insert into audit_log (org_id, actor_type, actor_id, action, entity_type, entity_id) "
            "values (%s, 'user', 'x', 'brand.sector_set', 'brand', 'b')",
            (tx.seed.org_a,),
        )


def test_the_audit_writer_role_admits_only_sector_rows(tx: Tx) -> None:
    tx.execute("set local role app_audit_writer")
    with pytest.raises(errors.InsufficientPrivilege):
        tx.execute(
            "insert into audit_log (org_id, actor_type, actor_id, action, entity_type, entity_id) "
            "values (%s, 'user', 'x', 'approve', 'campaign', 'c')",
            (tx.seed.org_a,),
        )
