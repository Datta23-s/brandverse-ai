"""The live posture tool refuses anything that is not a read (scripts/live_posture.py).

Offline: the tool's own refusal is tested here; that the database session is read only is asserted
by the tool itself every time it connects, because only the connection can show that.
"""

import ast
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

from live_posture import NotARead, ReadOnlyTx, check_read_only  # noqa: E402

ACCEPTED = [
    "select 1",
    "  SELECT current_user;  ",
    "select has_table_privilege(%(role)s, c.oid, 'DELETE') from pg_class c",
    "select c.relname, p.privilege_type from pg_class c, lateral (select 1) p",
]
REFUSED = {
    "insert into t values (1)": "not a SELECT",
    "update t set x = 1": "not a SELECT",
    "delete from t": "not a SELECT",
    "truncate t": "not a SELECT",
    "with x as (delete from t returning *) select * from x": "not a SELECT",
    "select 1; delete from t": "more than one statement",
    "select * into t2 from t": "'into'",
    "select * from t for update": "'for update'",
    "select * from t for share": "'for share'",
    "select set_config('request.jwt.claims', '{}', false)": "'set_config'",
    "select nextval('s')": "'nextval'",
    "select pg_sleep(60)": "'pg_sleep'",
    "select pg_terminate_backend(1)": "'pg_terminate_backend'",
    "select 1 -- ; drop table t": "comments",
    "select /* x */ 1": "comments",
    "": "not a SELECT",
}


@pytest.mark.parametrize("statement", ACCEPTED)
def test_a_plain_select_is_accepted(statement: str) -> None:
    check_read_only(statement)


@pytest.mark.parametrize(("statement", "why"), REFUSED.items())
def test_anything_else_is_refused_before_it_reaches_the_server(statement: str, why: str) -> None:
    with pytest.raises(NotARead, match=why):
        check_read_only(statement)


def test_the_wrapper_checks_before_it_executes() -> None:
    class Conn:
        def execute(self, *_: object) -> None:
            raise AssertionError("reached the connection")

    with pytest.raises(NotARead):
        ReadOnlyTx(Conn()).execute("delete from public.runs")  # type: ignore[arg-type]


def test_every_select_in_the_posture_suite_passes_the_filter() -> None:
    """The tool runs that suite unchanged; a statement added to it that the filter would refuse
    must fail here, in CI, not on the first run against the project."""
    source = (BACKEND / "tests" / "db" / "test_table_posture.py").read_text(encoding="utf-8")
    statements = [
        n.value
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and re.match(
            r"\s*(select|insert\s+into|update\s+\w+\s+set|delete\s+from|truncate\s+\w)\b",
            n.value,
            re.IGNORECASE,
        )
    ]
    assert len(statements) >= 8  # the scan reaches the suite's queries
    for s in statements:
        check_read_only(s)
