"""Source evidence for memory items (migration 0028; Memory.md section 3.1).

An item's `source_score` is DERIVED: the maximum of its per-source rows. The score is
trust-deciding, so it is written by the backend only, recorded by the server, and never altered.
Each test runs in a rolled-back transaction.
"""

import json
import random
from uuid import UUID

import pytest
from psycopg import errors

from tests.db.conftest import Tx

pytestmark = pytest.mark.db

COMPONENTS = json.dumps({"authority": 0.5, "recency": 1.0, "specificity": 0.75})


def item(tx: Tx, *, org: UUID | None = None, brand: UUID | None | str = "a") -> UUID:
    org = org or tx.seed.org_a
    brand_id = tx.seed.brand_a if brand == "a" else brand
    rows = tx.execute(
        "insert into memory_items (org_id, brand_id, kind, content, provenance, confidence) "
        "values (%s, %s, 'insight', 'a fact', 'test', 0.5) returning id",
        (org, brand_id),
    )
    found: UUID = rows[0][0]
    return found


def source(tx: Tx, url: str = "https://example.test/a", *, org: UUID | None = None) -> UUID:
    host = url.split("/")[2].lower()
    rows = tx.execute(
        "insert into sources (org_id, url, host, published_on) values (%s, %s, %s, '2026-03-14') "
        "returning id",
        (org or tx.seed.org_a, url, host),
    )
    found: UUID = rows[0][0]
    return found


def link(tx: Tx, item_id: UUID, source_id: UUID, score: float, *, org: UUID | None = None) -> None:
    tx.execute(
        "insert into memory_item_sources (memory_item_id, source_id, org_id, score, components, "
        "scorer, config_version) values (%s, %s, %s, %s, %s::jsonb, 'source_scoring/1', "
        "'defaults-2026-10-18')",
        (item_id, source_id, org or tx.seed.org_a, score, COMPONENTS),
    )


def aggregate(tx: Tx, item_id: UUID) -> object:
    return tx.execute("select source_score from memory_items where id = %s", (item_id,))[0][0]


def stored_max(tx: Tx, item_id: UUID) -> object:
    return tx.execute(
        "select max(score) from memory_item_sources where memory_item_id = %s", (item_id,)
    )[0][0]


# ── The aggregate is the maximum of the rows ─────────────────────────────────────────────────


def test_an_item_with_no_sources_has_no_score(tx: Tx) -> None:
    assert aggregate(tx, item(tx)) is None


def test_the_aggregate_is_the_maximum_of_the_items_rows_after_every_insert(tx: Tx) -> None:
    rng = random.Random(28)  # noqa: S311 - a seeded test generator
    for n in range(6):
        it = item(tx)
        for k in range(rng.randint(1, 7)):
            link(tx, it, source(tx, f"https://example.test/{n}/{k}"), round(rng.random(), 4))
            assert aggregate(tx, it) == stored_max(tx, it)


def test_adding_a_weaker_source_never_lowers_the_aggregate(tx: Tx) -> None:
    it = item(tx)
    link(tx, it, source(tx, "https://example.test/strong"), 0.9)
    link(tx, it, source(tx, "https://example.test/mediocre"), 0.5)
    link(tx, it, source(tx, "https://example.test/weak"), 0.1)
    assert float(str(aggregate(tx, it))) == 0.9  # type: ignore[arg-type]


def test_every_items_aggregate_equals_the_maximum_of_its_rows(tx: Tx) -> None:
    a, b = item(tx), item(tx)
    shared = source(tx, "https://example.test/shared")
    link(tx, a, shared, 0.4)
    link(tx, b, shared, 0.7)
    link(tx, a, source(tx, "https://example.test/x"), 0.8)
    item(tx)  # an item with none
    mismatched = tx.execute(
        "select m.id from memory_items m "
        "where m.source_score is distinct from "
        "(select max(s.score) from memory_item_sources s where s.memory_item_id = m.id)"
    )
    assert mismatched == []


def test_a_source_is_stored_once_and_shared_by_the_items_resting_on_it(tx: Tx) -> None:
    shared = source(tx, "https://example.test/shared")
    link(tx, item(tx), shared, 0.4)
    link(tx, item(tx), shared, 0.7)
    assert tx.execute("select count(*) from sources where id = %s", (shared,))[0][0] == 1
    with pytest.raises(errors.UniqueViolation):
        source(tx, "https://example.test/shared")  # the same page again, same organisation
    tx.conn.rollback()


# ── A bad source can be found, and every item resting on it revoked ──────────────────────────


def test_every_item_resting_on_a_source_or_a_host_is_found_and_can_be_revoked(tx: Tx) -> None:
    bad = source(tx, "https://spam.example.test/post")
    other_page_same_host = source(tx, "https://spam.example.test/other")
    fine = source(tx, "https://good.example.test/page")
    a, b, c = item(tx), item(tx), item(tx)
    link(tx, a, bad, 0.6)
    link(tx, b, other_page_same_host, 0.5)
    link(tx, c, fine, 0.9)
    link(tx, c, bad, 0.2)  # c rests on the bad source too, among others

    resting = tx.execute(
        "select distinct memory_item_id from memory_item_sources where source_id = %s", (bad,)
    )
    assert {r[0] for r in resting} == {a, c}
    on_host = tx.execute(
        "select distinct l.memory_item_id from memory_item_sources l join sources s "
        "on s.id = l.source_id and s.org_id = l.org_id where s.org_id = %s and s.host = %s",
        (tx.seed.org_a, "spam.example.test"),
    )
    assert {r[0] for r in on_host} == {a, b, c}

    tx.as_user("owner_a")  # the existing revocation path (0006, 0023)
    tx.execute(
        "update memory_items set revoked = true, revoked_reason = 'source found to be spam' "
        "where id in (select memory_item_id from memory_item_sources where source_id = %s)",
        (bad,),
    )
    revoked = tx.execute("select id from memory_items where revoked and id = any(%s)", ([a, b, c],))
    assert {r[0] for r in revoked} == {a, c}


def test_the_lookup_by_source_uses_an_index(tx: Tx) -> None:
    indexed = tx.execute(
        "select a.attname from pg_index i join pg_attribute a "
        "on a.attrelid = i.indrelid and a.attnum = i.indkey[0] "
        "where i.indrelid = 'memory_item_sources'::regclass"
    )
    assert "source_id" in {r[0] for r in indexed}
    on_host = tx.execute(
        "select a.attname from pg_index i join pg_attribute a "
        "on a.attrelid = i.indrelid and a.attnum = i.indkey[0] "
        "where i.indrelid = 'sources'::regclass"
    )
    assert "org_id" in {r[0] for r in on_host}


# ── The score is derived, recorded by the server, and written by the backend only ────────────


def test_source_score_cannot_be_set_on_insert_or_by_a_direct_update(tx: Tx) -> None:
    with pytest.raises(errors.RaiseException, match="derived"):
        tx.execute(
            "insert into memory_items (org_id, kind, content, provenance, confidence, "
            "source_score) values (%s, 'insight', 'x', 'test', 0.5, 0.99)",
            (tx.seed.org_a,),
        )
    tx.conn.rollback()
    it = item(tx)
    link(tx, it, source(tx), 0.4)
    with pytest.raises(errors.RaiseException, match="derived"):
        tx.execute("update memory_items set source_score = 0.99 where id = %s", (it,))
    tx.conn.rollback()
    it = item(tx)
    tx.as_service_role()  # the backend's own role: still not by hand
    with pytest.raises(errors.RaiseException, match="derived"):
        tx.execute("update memory_items set source_score = 0.99 where id = %s", (it,))


def test_a_signed_in_user_cannot_set_the_score(tx: Tx) -> None:
    it = item(tx)
    link(tx, it, source(tx), 0.4)
    tx.as_user("owner_a")
    with pytest.raises(errors.InsufficientPrivilege, match="permission denied"):
        tx.execute("update memory_items set source_score = 1 where id = %s", (it,))
    tx.conn.rollback()
    tx.as_user("owner_a")
    with pytest.raises(errors.InsufficientPrivilege):  # and cannot insert an item at all
        tx.execute(
            "insert into memory_items (org_id, kind, content, provenance, confidence) "
            "values (%s, 'insight', 'x', 'test', 0.5)",
            (tx.seed.org_a,),
        )


@pytest.mark.parametrize("table", ["sources", "memory_item_sources"])
@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_no_api_role_but_the_backend_can_write_the_evidence(tx: Tx, table: str, role: str) -> None:
    for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
        rows = tx.execute(f"select has_table_privilege('{role}', 'public.{table}', '{privilege}')")
        assert rows == [(False,)], f"{role} holds {privilege} on {table}"


def test_anon_holds_no_write_privilege_on_memory_items_whatever_the_column(tx: Tx) -> None:
    """0029: anon's table-level INSERT and UPDATE (from 0006) are revoked, so the column grants
    that follow from them are gone too, source_score included."""
    for privilege in ("INSERT", "UPDATE"):
        rows = tx.execute(
            f"select has_table_privilege('anon', 'public.memory_items', '{privilege}')"
        )
        assert rows == [(False,)], f"anon holds {privilege} on memory_items"
        for column in ("source_score", "confidence", "content", "revoked"):
            rows = tx.execute(
                "select has_column_privilege("
                f"'anon', 'public.memory_items', '{column}', '{privilege}')"
            )
            assert rows == [(False,)], f"anon holds {privilege} on memory_items.{column}"


@pytest.mark.parametrize("table", ["sources", "memory_item_sources"])
def test_the_backend_role_may_insert_and_select_and_nothing_else(tx: Tx, table: str) -> None:
    held = {
        p: tx.execute(f"select has_table_privilege('service_role', 'public.{table}', '{p}')")[0][0]
        for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
    }
    assert held == {
        "SELECT": True,
        "INSERT": True,
        "UPDATE": False,
        "DELETE": False,
        "TRUNCATE": False,
        "REFERENCES": False,
        "TRIGGER": False,
    }


def test_rows_are_append_only_even_for_the_superuser(tx: Tx) -> None:
    it, s = item(tx), source(tx)
    link(tx, it, s, 0.4)
    for statement in (
        "update sources set host = 'other.test'",
        "delete from sources",
        "update memory_item_sources set score = 1",
        "delete from memory_item_sources",
        "truncate memory_item_sources",
        "truncate sources, memory_item_sources",  # alone, the foreign key refuses it
    ):
        with pytest.raises(errors.RaiseException, match="append-only"):
            tx.execute(statement)
        tx.conn.rollback()
        it, s = item(tx), source(tx)
        link(tx, it, s, 0.4)


def test_the_database_stamps_the_time_whatever_the_writer_sent(tx: Tx) -> None:
    it, s = item(tx), source(tx)
    tx.execute(
        "insert into memory_item_sources (memory_item_id, source_id, org_id, score, components, "
        "scorer, config_version, computed_at) values (%s, %s, %s, 0.5, %s::jsonb, "
        "'source_scoring/1', 'v', '2001-01-01')",
        (it, s, tx.seed.org_a, COMPONENTS),
    )
    rows = tx.execute("select computed_at = now(), scorer, config_version from memory_item_sources")
    assert rows == [(True, "source_scoring/1", "v")]


@pytest.mark.parametrize(
    ("score", "components", "scorer", "config_version"),
    [
        (1.2, COMPONENTS, "source_scoring/1", "v"),
        (-0.1, COMPONENTS, "source_scoring/1", "v"),
        (0.5, "[1, 2]", "source_scoring/1", "v"),  # components must be an object
        (0.5, COMPONENTS, "", "v"),  # what computed it must be named
        (0.5, COMPONENTS, "source_scoring/1", ""),
    ],
)
def test_a_row_must_say_what_computed_it_and_stay_in_range(
    tx: Tx, score: float, components: str, scorer: str, config_version: str
) -> None:
    it, s = item(tx), source(tx)
    with pytest.raises(errors.CheckViolation):
        tx.execute(
            "insert into memory_item_sources (memory_item_id, source_id, org_id, score, "
            "components, scorer, config_version) values (%s, %s, %s, %s, %s::jsonb, %s, %s)",
            (it, s, tx.seed.org_a, score, components, scorer, config_version),
        )


@pytest.mark.parametrize(
    ("url", "host"),
    [("ftp://example.test/a", "example.test"), ("https://example.test/a", "Example.Test"),
     ("https://example.test/a", "")],
)  # fmt: skip
def test_a_source_has_an_http_url_and_a_lower_case_host(tx: Tx, url: str, host: str) -> None:
    with pytest.raises(errors.CheckViolation):
        tx.execute(
            "insert into sources (org_id, url, host) values (%s, %s, %s)",
            (tx.seed.org_a, url, host),
        )


# ── Tenancy ──────────────────────────────────────────────────────────────────────────────────


def test_a_link_cannot_join_an_item_to_another_organisations_source(tx: Tx) -> None:
    it = item(tx)
    foreign = source(tx, "https://example.test/b", org=tx.seed.org_b)
    with pytest.raises(errors.ForeignKeyViolation):
        link(tx, it, foreign, 0.5)
    tx.conn.rollback()
    it = item(tx)
    own = source(tx)
    with pytest.raises(errors.ForeignKeyViolation):  # nor claim the wrong organisation
        link(tx, it, own, 0.5, org=tx.seed.org_b)


def test_members_read_their_organisations_evidence_and_no_one_elses(tx: Tx) -> None:
    it = item(tx)
    link(tx, it, source(tx), 0.5)
    b_item = item(tx, org=tx.seed.org_b, brand=tx.seed.brand_b)
    link(
        tx, b_item, source(tx, "https://example.test/b", org=tx.seed.org_b), 0.6, org=tx.seed.org_b
    )
    tx.as_user("viewer_a")
    assert tx.execute("select count(*) from sources")[0][0] == 1
    assert tx.execute("select count(*) from memory_item_sources")[0][0] == 1
    tx.conn.rollback()
    tx.as_user("outsider")
    assert tx.execute("select count(*) from sources")[0][0] == 0
    assert tx.execute("select count(*) from memory_item_sources")[0][0] == 0


def test_a_user_in_two_organisations_reads_both(tx: Tx) -> None:
    link(tx, item(tx), source(tx), 0.5)
    b_item = item(tx, org=tx.seed.org_b, brand=tx.seed.brand_b)
    link(
        tx, b_item, source(tx, "https://example.test/b", org=tx.seed.org_b), 0.6, org=tx.seed.org_b
    )
    tx.as_user("agency")
    assert tx.execute("select count(*) from memory_item_sources")[0][0] == 2


def test_a_member_cannot_write_evidence_through_row_level_security_either(tx: Tx) -> None:
    it, s = item(tx), source(tx)
    tx.as_user("owner_a")
    with pytest.raises(errors.InsufficientPrivilege):
        tx.execute(
            "insert into sources (org_id, url, host) values (%s, 'https://x.test/a', 'x.test')",
            (tx.seed.org_a,),
        )
    tx.conn.rollback()
    tx.as_user("owner_a")
    with pytest.raises(errors.InsufficientPrivilege):
        link(tx, it, s, 0.9)


def test_the_backend_role_writes_evidence_and_the_aggregate_follows(tx: Tx) -> None:
    it = item(tx)
    tx.as_service_role()
    link(tx, it, source(tx), 0.65)
    tx.conn.execute("reset role")
    assert float(str(aggregate(tx, it))) == 0.65  # type: ignore[arg-type]
