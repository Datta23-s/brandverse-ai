"""The metrics reads against a real database (`app/evaluation/metrics.py`).

The pure functions are tested in `tests/test_evaluation_metrics.py`; this is the half that only a
database can check: the SQL runs, the supersession and provider-retry flags are read from the rows
and not from a Python guess, a brief the test set does not hold is REFUSED, and practice and test
organisations are not evaluation data.
"""

import json
from collections.abc import AsyncIterator
from uuid import UUID

import psycopg
import pytest
from psycopg import AsyncConnection, sql

from app.evaluation.metrics import run_records, scheduling_defects
from app.evaluation.test_set import request_inputs

pytestmark = [pytest.mark.anyio, pytest.mark.db]

INPUTS = request_inputs()


@pytest.fixture
async def conn(db_url: str) -> AsyncIterator[AsyncConnection]:
    async with await AsyncConnection.connect(db_url) as c:
        yield c


def organisation(db_url: str, *, practice: bool = False, test: bool = False) -> UUID:
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "insert into organisations (name, is_practice, is_test) "
            "values ('metrics read', %s, %s) returning id",
            (practice, test),
        ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def a_run(db_url: str, org: UUID, request_id: str, arm: str, **columns: object) -> UUID:
    """A run of `request_id` in `arm`, with whatever terminal columns the case needs."""
    brief = INPUTS[request_id].brief
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "insert into runs (org_id, arm, request) values (%s, %s, %s) returning id",
            (org, arm, json.dumps({"brief": brief, "depth_policy": "necessity"})),
        ).fetchone()
        assert row is not None
        run_id = UUID(str(row[0]))
        if columns:
            # Composed, never interpolated: the column names are identifiers (Rules.md).
            assignments = sql.SQL(", ").join(
                sql.SQL("{} = {}").format(sql.Identifier(name), sql.Placeholder())
                for name in columns
            )
            values = [json.dumps(v) if isinstance(v, dict | list) else v for v in columns.values()]
            c.execute(
                sql.SQL("update runs set {} where id = {}").format(assignments, sql.Placeholder()),
                [*values, run_id],
            )
    return run_id


async def test_the_read_carries_the_supersession_and_retry_flags_from_the_rows(
    conn: AsyncConnection, db_url: str
) -> None:
    org = organisation(db_url)
    invalid = a_run(
        db_url,
        org,
        "R01",
        "proposed",
        status="failed",
        failure={"infrastructure_failure": "network_error"},
        depth="D2",
    )
    rerun = a_run(
        db_url, org, "R01", "proposed", status="completed", depth="D1", pages_fetched=30,
        search_count=3, supersedes_run_id=invalid,
    )  # fmt: skip
    records = {r.run_id: r for r in await run_records(conn, [org])}
    assert records[invalid].superseded is True
    assert records[invalid].ending == "invalidated"
    assert records[rerun].supersedes_run_id == invalid
    assert records[rerun].superseded is False
    assert (records[rerun].pages_fetched, records[rerun].searches) == (30, 3)
    assert records[rerun].request_id == "R01" and records[rerun].position == 1
    assert records[rerun].brand_id == "B01"
    assert all(r.provider_retry is False for r in records.values())


async def test_a_brief_that_is_in_no_registered_request_is_refused_not_skipped(
    conn: AsyncConnection, db_url: str
) -> None:
    """The run row carries no request id; the brief is the only link to the design, so an
    unregistered brief means the scope is wrong (decision-log 2.53)."""
    org = organisation(db_url)
    with psycopg.connect(db_url, autocommit=True) as c:
        c.execute(
            "insert into runs (org_id, arm, request) values (%s, 'proposed', %s)",
            (org, json.dumps({"brief": "a brief nobody registered"})),
        )
    with pytest.raises(ValueError, match="in no registered request"):
        await run_records(conn, [org])


async def test_practice_and_test_organisations_are_not_evaluation_data(
    conn: AsyncConnection, db_url: str
) -> None:
    for flags in ({"practice": True}, {"test": True}):
        org = organisation(db_url, **flags)  # type: ignore[arg-type]
        a_run(db_url, org, "R01", "proposed", status="completed")
        assert await run_records(conn, [org]) == []
        assert await scheduling_defects(conn, [org]) == []


async def test_a_rate_limited_call_is_read_as_a_scheduling_defect_with_its_arm(
    conn: AsyncConnection, db_url: str
) -> None:
    org = organisation(db_url)
    run_id = a_run(db_url, org, "R01", "baseline_b", status="completed")
    with psycopg.connect(db_url, autocommit=True) as c:
        for outcome in ("rate_limited", "ok"):
            c.execute(
                "insert into llm_calls (org_id, run_id, agent_id, model, tier, route_reason, "
                "outcome, prompt_id, prompt_version) values (%s, %s, 'A6', 'm', 'standard', "
                "'test', %s, 'a6_copy', 'v3')",
                (org, run_id, outcome),
            )
    defects = await scheduling_defects(conn, [org])
    assert len(defects) == 1
    assert defects[0].arm == "baseline_b" and defects[0].run_id == run_id
    assert defects[0].agent_id == "A6"
