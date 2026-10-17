"""The registry's reviewed authoring path (backend/scripts/apply_sector_constraints.py).

Each test runs in a transaction that is rolled back, on a test-only sector.
"""

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import REPO_ROOT
from scripts.apply_sector_constraints import (
    SYNTHETIC_NOTE,
    Refused,
    RuleEntry,
    RuleFile,
    apply,
    committed_source,
    plan,
)
from tests.db.conftest import Tx

pytestmark = pytest.mark.db

SECTOR = "test-authoring"
SOURCE = {"source_file": "config/sector_constraints/test.yaml", "commit": "abc123"}
EMAIL = "registry-author@example.invalid"
REGEX = r"\b100\s*%\s*natural\b"


def _setup(tx: Tx) -> None:
    tx.execute("insert into sectors (id, label, regulated) values (%s, 'Test', true)", (SECTOR,))
    tx.execute("update auth.users set email = %s where id = %s", (EMAIL, tx.seed.users["admin"]))


def _entry(**fields: object) -> RuleEntry:
    base: dict[str, object] = {
        "kind": "prohibited_phrase",
        "match_type": "regex",
        "pattern": REGEX,
        "source_url": "https://example.invalid/guidance#4",
        "source_note": "test guidance, section 4",
        "effective_from": date(2026, 10, 1),
    }
    return RuleEntry.model_validate(base | fields)


DISCLAIMER = {
    "kind": "required_disclaimer",
    "match_type": "phrase",
    "pattern": None,
    "disclaimer_text": "Test disclaimer.",
}


def _file(*entries: RuleEntry) -> RuleFile:
    return RuleFile(sector=SECTOR, rules=list(entries))


def _rows(tx: Tx) -> list[tuple[object, ...]]:
    return tx.execute(
        "select kind, pattern, disclaimer_text, created_by, retired_at from sector_constraints "
        "where sector_id = %s order by kind",
        (SECTOR,),
    )


def test_new_rules_are_added_by_their_author_and_audited(tx: Tx) -> None:
    _setup(tx)
    changes = apply(tx.conn, _file(_entry(), _entry(**DISCLAIMER)), EMAIL, SOURCE)
    assert [c.action for c in changes] == ["add", "add"]
    admin = tx.seed.users["admin"]
    assert _rows(tx) == [
        ("prohibited_phrase", REGEX, None, admin, None),  # backslashes stored exactly
        ("required_disclaimer", None, "Test disclaimer.", admin, None),
    ]
    audit = tx.execute(
        "select actor_type, actor_id, action, details->>'commit', details->>'source_file' "
        "from audit_log where action like 'registry.%%' order by id"
    )
    assert (
        audit == [("user", str(admin), "registry.rule_added", "abc123", SOURCE["source_file"])] * 2
    )


def test_applying_the_same_file_again_changes_nothing(tx: Tx) -> None:
    _setup(tx)
    apply(tx.conn, _file(_entry()), EMAIL, SOURCE)
    assert apply(tx.conn, _file(_entry()), EMAIL, SOURCE) == []


def test_a_rule_given_retired_at_is_retired_never_deleted(tx: Tx) -> None:
    _setup(tx)
    apply(tx.conn, _file(_entry()), EMAIL, SOURCE)
    changes = apply(tx.conn, _file(_entry(retired_at=date(2026, 10, 17))), EMAIL, SOURCE)
    assert [c.action for c in changes] == ["retire"]
    assert _rows(tx)[0][4] == date(2026, 10, 17)
    assert tx.execute("select count(*) from audit_log where action = 'registry.rule_retired'") == [
        (1,)
    ]


def test_deleting_an_active_rule_from_the_file_is_refused(tx: Tx) -> None:
    _setup(tx)
    apply(tx.conn, _file(_entry(), _entry(**DISCLAIMER)), EMAIL, SOURCE)
    with pytest.raises(Refused, match="missing from the file"):
        plan(tx.conn, _file(_entry()))


def test_a_retired_rule_cannot_be_brought_back(tx: Tx) -> None:
    _setup(tx)
    apply(tx.conn, _file(_entry(retired_at=date(2026, 10, 17))), EMAIL, SOURCE)
    with pytest.raises(Refused, match="is retired"):
        plan(tx.conn, _file(_entry()))


def test_the_author_must_be_a_person(tx: Tx) -> None:
    _setup(tx)
    with pytest.raises(Refused, match="must be a person"):
        apply(tx.conn, _file(_entry()), "nobody@example.invalid", SOURCE)


def test_an_unknown_sector_is_refused(tx: Tx) -> None:
    with pytest.raises(Refused, match="no such sector"):
        plan(tx.conn, RuleFile(sector="no-such-sector", rules=[_entry()]))


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"source_note": SYNTHETIC_NOTE}, "seeded by migration 0021"),
        ({"source_url": "not a url"}, "should match pattern"),
        ({"pattern": "(unclosed"}, "does not compile"),
        (
            {"kind": "required_disclaimer", "match_type": "phrase", "pattern": None},
            "needs disclaimer",
        ),
        ({"retired_at": date(2026, 9, 1)}, "before effective_from"),
    ],
)
def test_entries_are_validated_before_any_database_work(
    fields: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _entry(**fields)


def test_an_uncommitted_rule_file_is_refused() -> None:
    path = REPO_ROOT / "config" / "uncommitted-test-rules.yaml"
    path.write_text("sector: x\nrules: []\n", encoding="utf-8")
    try:
        with pytest.raises(Refused, match="uncommitted changes"):
            committed_source(path)
    finally:
        path.unlink()


def test_a_committed_file_reports_its_commit() -> None:
    source = committed_source(REPO_ROOT / "config" / "sector_constraints" / "README.md")
    assert source["source_file"] == "config/sector_constraints/README.md"
    assert len(source["commit"]) == 40
    assert Path(REPO_ROOT / source["source_file"]).exists()
