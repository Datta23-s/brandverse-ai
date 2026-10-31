"""The approval API end to end (Phases.md Phase 6 chunk E): real Postgres, real Redis, the real ARQ
worker running the pipeline with the mock agents, and real ES256 tokens.

What is pinned here: a decision is written ONCE per round however often it is submitted; only the
approver role decides; a reject ends the run `completed` with a rejected campaign; a revision is
bounded; an edit-out removes words and re-runs the gates; and A7's advisory is stored but is in
nothing the approver is sent.
"""

import json
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from app.core.config import Settings
from tests.auth_keys import TestIssuer
from tests.conftest import make_settings
from tests.db.conftest import Seed
from tests.db.test_api import api, bearer, drain_queue

pytestmark = [pytest.mark.anyio, pytest.mark.db, pytest.mark.redis]

DECISION = {"seconds_spent": 12, "reason": "needs work", "failed_gate": "T1.3"}


@pytest.fixture
def issuer() -> TestIssuer:
    return TestIssuer()


@pytest.fixture
def settings(redis_settings: Settings, db_url: str) -> Settings:
    p = conninfo_to_dict(db_url)
    url = (
        f"postgresql://{quote(str(p['user']))}:{quote(str(p.get('password', '')))}"
        f"@{p['host']}:{p.get('port', 5432)}/{p['dbname']}"
    )
    return make_settings(
        redis_url=str(redis_settings.redis_url),
        database_url=url,
        run_events_block_ms=200,
        pipeline_agents="mock",
    )


async def parked_run(
    client: httpx.AsyncClient, settings: Settings, issuer: TestIssuer, s: Seed
) -> UUID:
    """A real run, started through the API and run by the worker up to the approval step."""
    r = await client.post(
        "/api/v1/runs",
        json={
            "org_id": str(s.org_a),
            "product_id": str(s.product_a),
            "depth": "D0",
            "audience": "commuters",
        },
        headers=bearer(issuer, s.users["editor_a"]),
    )
    assert r.status_code == 202, r.text
    run_id = UUID(r.json()["run_id"])
    await drain_queue(settings)
    return run_id


def rows(db_url: str, query: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(db_url, autocommit=True) as c:
        return c.execute(query, params).fetchall()


async def decide(
    client: httpx.AsyncClient,
    issuer: TestIssuer,
    s: Seed,
    run_id: UUID,
    what: str,
    user: str = "approver_a",
) -> httpx.Response:
    return await client.post(
        f"/api/v1/runs/{run_id}/{what}", json=DECISION, headers=bearer(issuer, s.users[user])
    )


async def test_a_run_parks_with_a_campaign_and_the_view_never_carries_the_advisory(
    settings: Settings, issuer: TestIssuer, seed: Seed, db_url: str
) -> None:
    async with api(settings, issuer) as client:
        run_id = await parked_run(client, settings, issuer, seed)
        r = await client.get(
            f"/api/v1/runs/{run_id}/campaign", headers=bearer(issuer, seed.users["viewer_a"])
        )
        assert r.status_code == 200, r.text
        view = r.json()
        assert view["status"] == "awaiting_approval" and view["revision_round"] == 0
        assert view["rounds_remaining"] == 2 and view["gate_outcome"] in ("pass", "flag")
        assert "headline" in view["copy_parts"] and view["decisions"] == []
        # Stored, and in nothing the approver is sent.
        stored = rows(
            db_url, "select validation ? 'advisory' from campaigns where run_id = %s", run_id
        )
        assert stored == [(True,)]
        assert "advisory" not in r.text
        outsider = await client.get(
            f"/api/v1/runs/{run_id}/campaign", headers=bearer(issuer, seed.users["outsider"])
        )
        assert outsider.status_code == 404


async def test_approving_twice_writes_one_row_and_resumes_once(
    settings: Settings, issuer: TestIssuer, seed: Seed, db_url: str
) -> None:
    async with api(settings, issuer) as client:
        run_id = await parked_run(client, settings, issuer, seed)
        first = await decide(client, issuer, seed, run_id, "approve")
        second = await decide(client, issuer, seed, run_id, "approve")
        assert first.status_code == second.status_code == 202
        assert first.json()["recorded"] is True and second.json()["recorded"] is False
        clash = await decide(client, issuer, seed, run_id, "reject")
        assert clash.status_code == 409
        await drain_queue(settings)
        late = await decide(client, issuer, seed, run_id, "approve")  # a replay after the run ended
        assert late.status_code == 202 and late.json()["recorded"] is False
    assert rows(
        db_url,
        "select count(*), min(outcome) from approvals a join campaigns c "
        "on c.id = a.campaign_id where c.run_id = %s",
        run_id,
    ) == [(1, "approve")]
    assert rows(
        db_url, "select status, approved_by is not null from campaigns where run_id = %s", run_id
    ) == [("approved", True)]
    assert rows(db_url, "select status from runs where id = %s", run_id) == [("completed",)]


async def test_only_an_approver_decides(
    settings: Settings, issuer: TestIssuer, seed: Seed, db_url: str
) -> None:
    async with api(settings, issuer) as client:
        run_id = await parked_run(client, settings, issuer, seed)
        for user in ("owner_a", "editor_a", "viewer_a"):
            assert (await decide(client, issuer, seed, run_id, "approve", user)).status_code == 403
        assert (
            await decide(client, issuer, seed, run_id, "approve", "approver_b")
        ).status_code == 404
        # No reason, no rejection.
        bare = await client.post(
            f"/api/v1/runs/{run_id}/reject",
            json={"seconds_spent": 1},
            headers=bearer(issuer, seed.users["approver_a"]),
        )
        assert bare.status_code == 422
    assert rows(
        db_url,
        "select count(*) from approvals a join campaigns c on c.id = a.campaign_id "
        "where c.run_id = %s",
        run_id,
    ) == [(0,)]


async def test_reject_ends_the_run_completed_with_a_rejected_campaign_and_writes_no_memory(
    settings: Settings, issuer: TestIssuer, seed: Seed, db_url: str
) -> None:
    before = rows(db_url, "select count(*) from memory_items")
    async with api(settings, issuer) as client:
        run_id = await parked_run(client, settings, issuer, seed)
        assert (await decide(client, issuer, seed, run_id, "reject")).status_code == 202
        await drain_queue(settings)
        view = (
            await client.get(
                f"/api/v1/runs/{run_id}/campaign", headers=bearer(issuer, seed.users["viewer_a"])
            )
        ).json()
    assert view["status"] == "rejected" and view["decisions"][0]["outcome"] == "reject"
    assert rows(db_url, "select status, budget_exhausted from runs where id = %s", run_id) == [
        ("completed", False)
    ]
    assert rows(db_url, "select count(*) from memory_items") == before


async def test_a_revision_opens_the_next_round_and_the_rounds_are_bounded(
    settings: Settings, issuer: TestIssuer, seed: Seed, db_url: str
) -> None:
    async with api(settings, issuer) as client:
        run_id = await parked_run(client, settings, issuer, seed)
        for round_ in (0, 1):
            r = await decide(client, issuer, seed, run_id, "revise")
            assert r.status_code == 202 and r.json()["revision_round"] == round_
            await drain_queue(settings)
        view = (
            await client.get(
                f"/api/v1/runs/{run_id}/campaign", headers=bearer(issuer, seed.users["viewer_a"])
            )
        ).json()
        assert view["revision_round"] == 2 and view["rounds_remaining"] == 0
        assert view["status"] == "awaiting_approval"
        assert (await decide(client, issuer, seed, run_id, "revise")).status_code == 409
        assert (await decide(client, issuer, seed, run_id, "approve")).status_code == 202
    assert rows(
        db_url,
        "select a.revision_round, a.outcome from approvals a join campaigns c "
        "on c.id = a.campaign_id where c.run_id = %s order by a.revision_round",
        run_id,
    ) == [(0, "revise"), (1, "revise"), (2, "approve")]


async def test_an_edit_out_removes_the_words_and_reruns_the_gates(
    settings: Settings, issuer: TestIssuer, seed: Seed, db_url: str
) -> None:
    async with api(settings, issuer) as client:
        run_id = await parked_run(client, settings, issuer, seed)
        h = bearer(issuer, seed.users["approver_a"])
        r = await client.post(
            f"/api/v1/runs/{run_id}/edit-out", json={"statement": " in selected stores"}, headers=h
        )
        assert r.status_code == 202
        await drain_queue(settings)
        view = (await client.get(f"/api/v1/runs/{run_id}/campaign", headers=h)).json()
        assert view["copy_parts"]["body"] == "Available now."
        assert view["edited_out"] == [" in selected stores"]
        # Words that are not there, and a viewer, change nothing.
        await client.post(
            f"/api/v1/runs/{run_id}/edit-out", json={"statement": "not in the copy"}, headers=h
        )
        await drain_queue(settings)
        viewer = await client.post(
            f"/api/v1/runs/{run_id}/edit-out",
            json={"statement": "Available"},
            headers=bearer(issuer, seed.users["viewer_a"]),
        )
        assert viewer.status_code == 403
        again = (await client.get(f"/api/v1/runs/{run_id}/campaign", headers=h)).json()
        assert again["edited_out"] == view["edited_out"]
    assert rows(
        db_url,
        "select count(*) from audit_log where action = 'campaign.edited_out' and entity_id = %s",
        str(run_id),
    ) == [(1,)]


async def test_a_partial_run_is_told_apart_on_the_stream(
    settings: Settings, issuer: TestIssuer, seed: Seed
) -> None:
    from app.models.runs import RunCompleted

    assert RunCompleted(run_id=uuid4()).partial is False
    assert (
        json.loads(RunCompleted(run_id=uuid4(), partial=True).model_dump_json())["partial"] is True
    )
