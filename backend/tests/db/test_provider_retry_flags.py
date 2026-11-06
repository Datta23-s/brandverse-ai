"""A campaign that contained a provider-unavailable answer is flagged, from llm_calls alone
(owner, 2026-11-06). Each test works in a fresh organisation of its own."""

import psycopg
import pytest

from app.evaluation.provider_retries import runs_with_provider_retry

pytestmark = [pytest.mark.anyio, pytest.mark.db]


def make_org(db_url: str, *, test: bool = False) -> object:
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "insert into organisations (name, is_test) values ('retry-flag test', %s) returning id",
            (test,),
        ).fetchone()
    assert row is not None
    return row[0]


def run_with(db_url: str, org: object, arm: str, *outcomes: str) -> object:
    with psycopg.connect(db_url, autocommit=True) as c:
        row = c.execute(
            "insert into runs (org_id, arm, request) values (%s, %s, '{}') returning id",
            (org, arm),
        ).fetchone()
        assert row is not None
        for outcome in outcomes:
            c.execute(
                "insert into llm_calls (org_id, run_id, model, tier, prompt_id, prompt_version, "
                "route_reason, outcome) values (%s, %s, 'm', 'cheap', 'p', 'v1', 'test', %s)",
                (org, row[0], outcome),
            )
    return row[0]


async def test_only_runs_with_a_provider_unavailable_row_are_flagged(db_url: str) -> None:
    org = make_org(db_url)
    retried = run_with(db_url, org, "proposed", "provider_unavailable", "ok", "ok")
    twice = run_with(
        db_url, org, "baseline_a", "provider_unavailable", "provider_unavailable", "ok"
    )
    run_with(db_url, org, "baseline_b", "ok", "ok")  # clean
    run_with(db_url, org, "proposed", "invalid_output", "ok")  # an agent's own failure: not a retry
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        flags = await runs_with_provider_retry(conn, [org])  # type: ignore[list-item]
    assert {(f.run_id, f.arm, f.unavailable_rows) for f in flags} == {
        (retried, "proposed", 1),
        (twice, "baseline_a", 2),
    }


async def test_test_organisations_are_not_evaluation_data(db_url: str) -> None:
    org = make_org(db_url, test=True)
    run_with(db_url, org, "proposed", "provider_unavailable", "ok")
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        assert await runs_with_provider_retry(conn, [org]) == []  # type: ignore[list-item]
