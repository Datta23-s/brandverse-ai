"""The engine's record is read back from campaigns.strategy for the decomposition (decision-log
2.45). Each test works in a fresh organisation of its own."""

import json

import psycopg
import pytest

from app.evaluation.necessity_decomposition import necessity_records

pytestmark = [pytest.mark.anyio, pytest.mark.db]


def org(db_url: str, *, test: bool = False) -> object:
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "insert into organisations (name, is_test) values ('necessity test', %s) returning id",
            (test,),
        ).fetchone()
    assert row is not None
    return row[0]


def campaign(db_url: str, organisation: object, arm: str, strategy: dict | None) -> object:  # type: ignore[type-arg]
    with psycopg.connect(db_url, autocommit=True) as c:
        run = c.execute(
            "insert into runs (org_id, arm, request) values (%s, %s, '{}') returning id",
            (organisation, arm),
        ).fetchone()
        assert run is not None
        c.execute(
            "insert into campaigns (org_id, run_id, strategy) values (%s, %s, %s)",
            (organisation, run[0], json.dumps(strategy)),
        )
    return run[0]


async def test_the_record_is_read_back_for_evaluation_campaigns_only(db_url: str) -> None:
    o = org(db_url)
    rec = {"novelty": 0.15, "u": 0.4, "s_sector": 0.0, "depth": "D1", "policy": "necessity"}
    with_record = campaign(db_url, o, "proposed", {"necessity": rec, "claims": []})
    campaign(db_url, o, "baseline_b", {"claims": []})  # no engine record: not listed
    campaign(db_url, o, "baseline_a", {"necessity": None})  # JSON null: not a record
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        rows = await necessity_records(conn, [o])  # type: ignore[list-item]
    assert rows == [(with_record, "proposed", rec)]


async def test_test_organisations_are_not_evaluation_data(db_url: str) -> None:
    o = org(db_url, test=True)
    campaign(db_url, o, "proposed", {"necessity": {"novelty": 0.1, "u": 0.1, "s_sector": 0.0}})
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        assert await necessity_records(conn, [o]) == []  # type: ignore[list-item]
