"""Independence is kept with a memory item's evidence (migration 0040, threat V6).

A source row says whether the page was the brand's own (`first_party`: true, false, or NULL for
unknown), and the item carries a DERIVED flag, `evidence_first_party_only`, so a reused item keeps
the provenance that made its evidence first-party. Authority and independence stay separate: the
flag is not an input to the M-score. Each test runs in a rolled-back transaction.
"""

from uuid import UUID

import pytest
from psycopg import errors

from tests.db.conftest import Tx
from tests.db.test_source_evidence import COMPONENTS, item, source

pytestmark = pytest.mark.db

VECTOR = "[" + ",".join(["0.01"] * 768) + "]"


def link(tx: Tx, item_id: UUID, source_id: UUID, first_party: bool | None) -> None:
    tx.execute(
        "insert into memory_item_sources (memory_item_id, source_id, org_id, score, components, "
        "scorer, config_version, first_party) values (%s, %s, %s, 0.8, %s::jsonb, "
        "'source_scoring/1', 'defaults', %s)",
        (item_id, source_id, tx.seed.org_a, COMPONENTS, first_party),
    )


def flag(tx: Tx, item_id: UUID) -> object:
    rows = tx.execute(
        "select evidence_first_party_only from memory_items where id = %s", (item_id,)
    )
    return rows[0][0]


# ── The derived flag ─────────────────────────────────────────────────────────────────────────


def test_an_item_with_no_sources_is_unknown_not_independent(tx: Tx) -> None:
    assert flag(tx, item(tx)) is None


def test_every_source_first_party_makes_the_item_first_party_only(tx: Tx) -> None:
    i = item(tx)
    link(tx, i, source(tx, "https://brand.example/a"), True)
    assert flag(tx, i) is True
    link(tx, i, source(tx, "https://brand.example/b"), True)
    assert flag(tx, i) is True


def test_one_independent_source_makes_it_not_first_party_only_and_it_stays_so(tx: Tx) -> None:
    i = item(tx)
    link(tx, i, source(tx, "https://brand.example/a"), True)
    link(tx, i, source(tx, "https://reviews.example/a"), False)
    assert flag(tx, i) is False
    link(tx, i, source(tx, "https://brand.example/c"), True)  # more first-party evidence
    assert flag(tx, i) is False  # adding a source never takes independence away


def test_an_unknown_source_is_never_read_as_independent(tx: Tx) -> None:
    i = item(tx)
    link(tx, i, source(tx, "https://brand.example/a"), True)
    link(tx, i, source(tx, "https://unknown.example/a"), None)
    # first-party plus unknown: not proven first-party-only, and not independent either
    assert flag(tx, i) is None
    only_unknown = item(tx)
    link(tx, only_unknown, source(tx, "https://unknown.example/b"), None)
    assert flag(tx, only_unknown) is None


def test_the_flag_belongs_to_each_item_separately(tx: Tx) -> None:
    a, b = item(tx), item(tx)
    link(tx, a, source(tx, "https://brand.example/a"), True)
    link(tx, b, source(tx, "https://reviews.example/a"), False)
    assert (flag(tx, a), flag(tx, b)) == (True, False)


# ── Derived, backend-written, never revised ──────────────────────────────────────────────────


def test_the_flag_cannot_be_written_directly(tx: Tx) -> None:
    i = item(tx)
    with pytest.raises(errors.RaiseException, match="derived from memory_item_sources"):
        tx.execute("update memory_items set evidence_first_party_only = true where id = %s", (i,))


def test_an_item_cannot_arrive_with_the_flag_set(tx: Tx) -> None:
    with pytest.raises(errors.RaiseException, match="derived from memory_item_sources"):
        tx.execute(
            "insert into memory_items (org_id, kind, content, provenance, confidence, "
            "evidence_first_party_only) values (%s, 'insight', 'x', 'test', 0.5, false)",
            (tx.seed.org_a,),
        )


def test_a_source_rows_first_party_is_never_revised(tx: Tx) -> None:
    i = item(tx)
    link(tx, i, source(tx, "https://brand.example/a"), True)
    with pytest.raises(errors.RaiseException, match="append-only"):
        tx.execute(
            "update memory_item_sources set first_party = false where memory_item_id = %s", (i,)
        )


def test_an_api_role_cannot_insert_a_source_row(tx: Tx) -> None:
    i = item(tx)
    s = source(tx, "https://brand.example/z")
    tx.as_user("owner_a")
    with pytest.raises(errors.InsufficientPrivilege):
        link(tx, i, s, False)


# ── It travels with the item when the item is recalled ───────────────────────────────────────


def test_match_memory_returns_the_flag(tx: Tx) -> None:
    i = item(tx)
    link(tx, i, source(tx, "https://brand.example/a"), True)
    tx.execute(
        "update memory_items set approved = true, embedding = %s::vector, "
        "embedding_model = 'test', embedding_version = '1' where id = %s",
        (VECTOR, i),
    )
    rows = tx.execute(
        "select id, evidence_first_party_only from app.match_memory(%s, %s::vector, 'fact') "
        "where id = %s",
        (tx.seed.org_a, VECTOR, i),
    )
    assert rows == [(i, True)]
