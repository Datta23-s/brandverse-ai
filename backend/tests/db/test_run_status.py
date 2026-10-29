"""Run statuses (migration 0015), who may set them (0016), and the per-arm outcome report.

A gate-blocked run is a result in its own right in Phase 10, so its count must be both possible
to record (`blocked` is a status) and impossible to forge (a user cannot insert a run that is
already blocked, completed or costed).
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from app.evaluation.run_outcomes import RUN_STATUSES, run_outcomes_by_arm, summarise
from tests.db.conftest import Tx

pytestmark = pytest.mark.db


@pytest.mark.parametrize("status", RUN_STATUSES)
def test_every_reported_status_is_allowed(tx: Tx, status: str) -> None:
    tx.execute("update runs set status = %s where id = %s", (status, tx.seed.run_a))


@pytest.mark.parametrize("status", ["running", "approved", "BLOCKED", ""])
def test_any_other_status_is_refused(tx: Tx, status: str) -> None:
    with pytest.raises(errors.CheckViolation, match="runs_status_check"):
        tx.execute("update runs set status = %s where id = %s", (status, tx.seed.run_a))


def test_a_user_inserted_run_starts_in_the_servers_initial_state(tx: Tx) -> None:
    tx.as_user("editor_a")
    rows = tx.execute(
        "insert into runs (org_id, product_id, arm, request, status, budget_exhausted, "
        "llm_call_count, search_count, memory_hit_count, total_cost_usd, latency_ms, depth, "
        "research_score, selected_agents, created_at, completed_at) values (%s, %s, 'proposed', "
        "'{}', 'blocked', true, 7, 3, 9, 1.25, 900, 'D2', 0.9, '{A1}', '2001-01-01', now()) "
        "returning status, budget_exhausted, llm_call_count, search_count, memory_hit_count, "
        "total_cost_usd, latency_ms, depth, research_score, selected_agents, created_at = now(), "
        "completed_at, arm",
        (tx.seed.org_a, tx.seed.product_a),
    )
    assert rows == [("queued", False, 0, 0, 0, 0, None, None, None, None, True, None, "proposed")]


def test_a_user_cannot_move_a_run_on(tx: Tx) -> None:
    tx.as_user("owner_a")
    rows = tx.execute(
        "update runs set status = 'blocked' where id = %s returning id", (tx.seed.run_a,)
    )
    assert rows == []  # no update policy for any API role (0005)


def test_the_backend_records_a_blocked_run(tx: Tx) -> None:
    tx.execute(
        "update runs set status = 'blocked', completed_at = now() where id = %s", (tx.seed.run_a,)
    )
    assert tx.execute("select status from runs where id = %s", (tx.seed.run_a,)) == [("blocked",)]


# ── The per-arm report (app/evaluation/run_outcomes.py) ──────────────────────────────────────


def _org(c: psycopg.Connection, practice: bool = False) -> UUID:
    row = c.execute(
        "insert into organisations (name, is_practice) values ('run outcomes', %s) returning id",
        (practice,),
    ).fetchone()
    assert row is not None
    org: UUID = row[0]
    return org


def _runs(
    c: psycopg.Connection, org: UUID, arm: str, status: str, n: int, age: str = "0 s"
) -> None:
    for _ in range(n):
        c.execute(
            "insert into runs (org_id, arm, request, status, created_at) "
            "values (%s, %s, '{}', %s, now() - %s::interval)",
            (org, arm, status, age),
        )


@pytest.mark.anyio
async def test_blocked_runs_are_counted_per_arm_with_zeros_kept(db_url: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as c:
        org, practice = _org(c), _org(c, practice=True)
        _runs(c, org, "proposed", "blocked", 3)
        _runs(c, org, "proposed", "completed", 5)
        _runs(c, org, "baseline_b", "partial", 1)
        _runs(c, org, "baseline_a", "failed", 2)
        _runs(c, practice, "proposed", "blocked", 4)  # practice: never in the report
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        report = {a.arm: a for a in await run_outcomes_by_arm(conn, [org, practice])}
    assert report["proposed"].blocked == 3
    assert report["proposed"].total == 8
    assert report["baseline_b"].by_status["partial"] == 1
    assert report["baseline_b"].blocked == 0
    assert report["baseline_a"].by_status == {
        "queued": 0,
        "awaiting_approval": 0,
        "completed": 0,
        "partial": 0,
        "failed": 2,
        "blocked": 0,
    }


@pytest.mark.anyio
async def test_the_report_is_bounded_by_the_evaluation_period(db_url: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as c:
        org = _org(c)
        _runs(c, org, "proposed", "blocked", 2, age="2 days")
        _runs(c, org, "proposed", "blocked", 1)
    since = datetime.now(UTC) - timedelta(days=1)
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        report = {a.arm: a for a in await run_outcomes_by_arm(conn, [org], since=since)}
    assert report["proposed"].blocked == 1


def test_an_unknown_status_fails_loudly_instead_of_vanishing() -> None:
    with pytest.raises(ValueError, match="unknown arm or run status"):
        summarise([("proposed", "running", 1)])
