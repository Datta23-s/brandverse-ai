"""Rule provenance and the illustrative health-supplements rule set (migration 0021).

The claim is that the mechanism enforces whatever rules it is given, not that the rules shipped
are legally correct — so the synthetic status of the shipped rules must be impossible to miss or
to fake: marked by their source, confined to illustrative sectors, and printed with the sector.
"""

import re

import pytest
from psycopg import errors

from app.core.sector_registry import verbatim_form
from app.core.text_match import matches_rule
from tests.db.conftest import Tx

pytestmark = pytest.mark.db

SYNTHETIC = "Synthetic rule, not derived from any regulation. Illustrative only."
SEEDED = (
    "select kind, match_type, pattern, disclaimer_text, source_url, source_note, created_by "
    "from sector_constraints where sector_id = 'health-supplements' order by kind, pattern"
)


def test_health_supplements_has_four_synthetic_rules_of_both_kinds(tx: Tx) -> None:
    rows = tx.execute(SEEDED)
    assert len(rows) == 4
    assert sorted({r[0] for r in rows}) == ["prohibited_phrase", "required_disclaimer"]
    assert all(r[4] is None and r[5] == SYNTHETIC and r[6] is None for r in rows)
    disclaimers = [r[3] for r in rows if r[0] == "required_disclaimer"]
    # The copy itself says the disclaimer is illustrative.
    assert all(d.startswith("Illustrative disclaimer (synthetic rule): ") for d in disclaimers)
    # One disclaimer applies to all output (no trigger), one is triggered.
    assert sorted(r[2] is None for r in rows if r[0] == "required_disclaimer") == [False, True]


def test_no_sector_is_authoritative(tx: Tx) -> None:
    # No rule set in the system is cited from regulation; the default keeps it that way.
    assert tx.execute("select id from sectors where rules_provenance <> 'illustrative'") == []
    assert tx.execute(
        "select column_default from information_schema.columns where table_name = "
        "'sectors' and column_name = 'rules_provenance'"
    ) == [("'illustrative'::text",)]


def test_the_seeded_patterns_behave_as_declared(tx: Tx) -> None:
    patterns = {r[2]: r[1] for r in tx.execute(SEEDED) if r[0] == "prohibited_phrase"}
    regex = next(p for p, t in patterns.items() if t == "regex")
    re.compile(regex)  # a seeded regex that does not compile would block every run
    assert matches_rule("Guaranteed   RESULTS in a week", "regex", regex)
    assert not matches_rule("results are not guaranteed", "regex", regex)
    assert matches_rule("A Miracle-Cure formula", "phrase", "miracle cure")
    for (text,) in tx.execute(
        "select disclaimer_text from sector_constraints where kind = 'required_disclaimer' "
        "and sector_id = 'health-supplements'"
    ):
        assert verbatim_form(text) == text  # stored in its own verbatim form


def test_a_synthetic_rule_cannot_join_an_authoritative_sector(tx: Tx) -> None:
    tx.execute("update sectors set rules_provenance = 'authoritative' where id = 'footwear'")
    with pytest.raises(errors.RaiseException, match="only belong to an illustrative sector"):
        tx.execute(
            "insert into sector_constraints (sector_id, kind, match_type, pattern, source_note, "
            "effective_from) values ('footwear', 'prohibited_phrase', 'phrase', 'x', %s, "
            "current_date)",
            (SYNTHETIC,),
        )


def test_a_sector_with_active_synthetic_rules_cannot_be_called_authoritative(tx: Tx) -> None:
    with pytest.raises(errors.RaiseException, match="has active synthetic rules"):
        tx.execute(
            "update sectors set rules_provenance = 'authoritative' where id = 'health-supplements'"
        )


def test_once_its_synthetic_rules_are_retired_it_can_be(tx: Tx) -> None:
    tx.execute(
        "update sector_constraints set retired_at = current_date "
        "where sector_id = 'health-supplements' and source_url is null"
    )
    tx.execute(
        "update sectors set rules_provenance = 'authoritative' where id = 'health-supplements'"
    )


@pytest.mark.parametrize(
    ("url", "author", "note"),
    [
        (None, "admin", "a citation-free note"),  # no URL, but not marked synthetic
        ("https://example.invalid/reg", None, "cited"),  # cited, but no human author
        ("https://example.invalid/reg", "admin", SYNTHETIC),  # a URL dressed as synthetic
    ],
    ids=["unmarked-no-url", "cited-no-author", "url-with-synthetic-note"],
)
def test_a_rule_is_either_synthetic_or_cited_and_authored(
    tx: Tx, url: str | None, author: str | None, note: str
) -> None:
    with pytest.raises(errors.CheckViolation, match="sector_constraints_source"):
        tx.execute(
            "insert into sector_constraints (sector_id, kind, match_type, pattern, source_url, "
            "source_note, effective_from, created_by) values ('health-supplements', "
            "'prohibited_phrase', 'phrase', 'x', %s, %s, current_date, %s)",
            (url, note, None if author is None else tx.seed.users[author]),
        )


def test_users_cannot_change_a_sectors_provenance(tx: Tx) -> None:
    tx.as_user("owner_a")
    with pytest.raises(errors.InsufficientPrivilege):
        tx.execute("update sectors set rules_provenance = 'authoritative' where id = 'footwear'")
