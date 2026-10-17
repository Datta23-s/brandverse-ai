"""The authenticated API end to end: real Postgres (the migrations, RLS, 0003's membership
functions), real Redis, a real ARQ worker, and real ES256 tokens from a test issuer.

Merge-blocking with the database suite (Rules.md §6): the org check on the run stream is tenant
isolation. Needs TEST_DATABASE_URL and Redis (REDIS_TEST_URL).
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import uvicorn
from fastapi import FastAPI
from psycopg.conninfo import conninfo_to_dict

from app.api.main import create_app
from app.core.config import Settings
from app.core.pipeline import PIPELINE_STEPS
from app.workers.main import build_worker
from tests.auth_keys import SigningKey, TestIssuer
from tests.conftest import make_settings
from tests.db.conftest import Seed
from tests.sse import SseMessage, parse_stream

pytestmark = [pytest.mark.anyio, pytest.mark.db, pytest.mark.redis]


@pytest.fixture
def issuer() -> TestIssuer:
    return TestIssuer()


@pytest.fixture
def settings(redis_settings: Settings, db_url: str) -> Settings:
    # db_url is a libpq key-value string; Settings takes (and validates) a postgresql:// URL.
    p = conninfo_to_dict(db_url)
    url = (
        f"postgresql://{quote(str(p['user']))}:{quote(str(p.get('password', '')))}"
        f"@{p['host']}:{p.get('port', 5432)}/{p['dbname']}"
    )
    return make_settings(
        redis_url=str(redis_settings.redis_url), database_url=url, run_events_block_ms=200
    )


@pytest.fixture
def new_user(db_url: str) -> Callable[[], UUID]:
    """A user who exists in auth.users and belongs to no organisation yet."""

    def make() -> UUID:
        user = uuid4()
        with psycopg.connect(db_url, autocommit=True) as c:
            c.execute("insert into auth.users (id) values (%s)", (user,))
        return user

    return make


@asynccontextmanager
async def api(settings: Settings, issuer: TestIssuer) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, verifier=issuer.verifier())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def bearer(issuer: TestIssuer, user: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {issuer.token(user)}"}


async def drain_queue(settings: Settings) -> None:
    worker = build_worker(settings, burst=True)
    try:
        await worker.async_run()
    finally:
        await worker.close()
    assert worker.jobs_failed == 0


async def start_fake_run(
    client: httpx.AsyncClient, headers: dict[str, str], org: UUID, **body: object
) -> UUID:
    r = await client.post(
        "/api/v1/runs/fake",
        json={"org_id": str(org), "step_delay_ms": 0, **body},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    return UUID(r.json()["run_id"])


async def read_stream(
    client: httpx.AsyncClient,
    run_id: UUID,
    headers: dict[str, str],
    last_event_id: str | None = None,
) -> tuple[httpx.Response, list[SseMessage]]:
    h = dict(headers)
    if last_event_id is not None:
        h["Last-Event-ID"] = last_event_id
    # A stream wrongly opened for a run still in progress would wait for events: fail, not hang.
    async with asyncio.timeout(15):
        r = await client.get(f"/api/v1/runs/{run_id}/stream", headers=h)
    events = parse_stream(r.text) if r.headers.get("content-type", "").startswith("text/") else []
    return r, events


# ── Signed out ───────────────────────────────────────────────────────────────────────────────

SIGNED_IN_ONLY = [
    ("GET", "/api/v1/me", None),
    ("POST", "/api/v1/organisations", {"name": "X"}),
    ("GET", f"/api/v1/brands?org_id={uuid4()}", None),
    ("POST", "/api/v1/brands", {"org_id": str(uuid4()), "name": "X"}),
    ("GET", f"/api/v1/products?org_id={uuid4()}", None),
    ("POST", "/api/v1/runs/fake", {"org_id": str(uuid4())}),
    ("GET", f"/api/v1/runs/{uuid4()}/stream", None),
]


@pytest.mark.parametrize(("method", "path", "body"), SIGNED_IN_ONLY)
async def test_signed_out_requests_are_refused(
    settings: Settings, issuer: TestIssuer, method: str, path: str, body: object
) -> None:
    async with api(settings, issuer) as client:
        r = await client.request(method, path, json=body)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


async def test_a_signed_out_request_cannot_stream_a_real_run(
    settings: Settings, issuer: TestIssuer, seed: Seed
) -> None:
    owner = bearer(issuer, seed.users["owner_a"])
    async with api(settings, issuer) as client:
        run_id = await start_fake_run(client, owner, seed.org_a)
        await drain_queue(settings)
        r, events = await read_stream(client, run_id, {})
    assert r.status_code == 401
    assert events == []


@pytest.mark.parametrize("kind", ["garbage", "unknown_key", "expired", "anon_role"])
async def test_invalid_tokens_are_refused(
    settings: Settings, issuer: TestIssuer, seed: Seed, kind: str
) -> None:
    user = seed.users["owner_a"]
    token = {
        "garbage": "not-a-token",
        "unknown_key": issuer.token(user, key=SigningKey("forged")),
        "expired": issuer.token(user, expires_in=-60),
        "anon_role": issuer.token(user, role="anon"),
    }[kind]
    async with api(settings, issuer) as client:
        r = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ── The org check on the run stream ──────────────────────────────────────────────────────────


async def test_a_member_of_org_a_cannot_stream_org_bs_run(
    settings: Settings, issuer: TestIssuer, seed: Seed
) -> None:
    """approver_b belongs to org B only; owner_a's run is in org A."""
    owner_a = bearer(issuer, seed.users["owner_a"])
    member_b = bearer(issuer, seed.users["approver_b"])
    async with api(settings, issuer) as client:
        run_id = await start_fake_run(client, owner_a, seed.org_a)
        await drain_queue(settings)
        own, full = await read_stream(client, run_id, owner_a)
        other, leaked = await read_stream(client, run_id, member_b)
        unknown, _ = await read_stream(client, uuid4(), member_b)
        # Even holding the final event's ID, B learns nothing — no 204 confirming the run ended.
        resumed, _ = await read_stream(client, run_id, member_b, last_event_id=full[-1].id)
    assert own.status_code == 200 and full[-1].event == "run.completed"
    assert other.status_code == 404 and leaked == []
    # Indistinguishable from a run that does not exist.
    assert other.json() == unknown.json()
    assert resumed.status_code == 404


async def test_a_user_with_no_organisation_cannot_stream_or_start_runs(
    settings: Settings, issuer: TestIssuer, seed: Seed
) -> None:
    owner_a = bearer(issuer, seed.users["owner_a"])
    outsider = bearer(issuer, seed.users["outsider"])
    async with api(settings, issuer) as client:
        run_id = await start_fake_run(client, owner_a, seed.org_a)
        await drain_queue(settings)  # finished, so a wrongly opened stream returns, not hangs
        stream, _ = await read_stream(client, run_id, outsider)
        start = await client.post(
            "/api/v1/runs/fake", json={"org_id": str(seed.org_a)}, headers=outsider
        )
    assert stream.status_code == 404
    assert start.status_code == 404


async def test_a_viewer_may_watch_but_not_start_a_run(
    settings: Settings, issuer: TestIssuer, seed: Seed
) -> None:
    owner_a = bearer(issuer, seed.users["owner_a"])
    viewer_a = bearer(issuer, seed.users["viewer_a"])
    async with api(settings, issuer) as client:
        start = await client.post(
            "/api/v1/runs/fake", json={"org_id": str(seed.org_a)}, headers=viewer_a
        )
        run_id = await start_fake_run(client, owner_a, seed.org_a)
        await drain_queue(settings)
        r, events = await read_stream(client, run_id, viewer_a)
    assert start.status_code == 403
    assert r.status_code == 200 and events[-1].event == "run.completed"


async def test_a_member_of_two_orgs_starts_runs_only_where_their_role_allows(
    settings: Settings, issuer: TestIssuer, seed: Seed
) -> None:
    agency = bearer(issuer, seed.users["agency"])  # editor in A, viewer in B
    async with api(settings, issuer) as client:
        in_a = await client.post(
            "/api/v1/runs/fake", json={"org_id": str(seed.org_a)}, headers=agency
        )
        in_b = await client.post(
            "/api/v1/runs/fake", json={"org_id": str(seed.org_b)}, headers=agency
        )
    assert in_a.status_code == 202
    assert in_b.status_code == 403


# ── The stream itself (moved from the Redis-only suite; now authenticated) ─────────────────────


async def test_fake_run_streams_all_fifteen_steps(
    settings: Settings, issuer: TestIssuer, seed: Seed
) -> None:
    owner = bearer(issuer, seed.users["owner_a"])
    async with api(settings, issuer) as client:
        run_id = await start_fake_run(client, owner, seed.org_a)
        await drain_queue(settings)
        r, messages = await read_stream(client, run_id, owner)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert [m.event for m in messages[:2]] == ["run.queued", "run.started"]
    assert messages[0].data["org_id"] == str(seed.org_a)
    assert messages[-1].event == "run.completed"
    steps = [
        (m.data["step"], m.data["name"], m.data["status"]) for m in messages if m.event == "step"
    ]
    assert steps == [
        (s.number, s.name, status) for s in PIPELINE_STEPS for status in ("started", "completed")
    ]
    assert all(m.event == m.data["type"] and m.data["run_id"] == str(run_id) for m in messages)
    ids = [tuple(map(int, m.id.split("-"))) for m in messages if m.id]
    assert len(ids) == len(messages) == 33
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert messages[0].retry == 2000


async def test_simulated_failure_streams_run_failed(
    settings: Settings, issuer: TestIssuer, seed: Seed
) -> None:
    owner = bearer(issuer, seed.users["owner_a"])
    async with api(settings, issuer) as client:
        run_id = await start_fake_run(client, owner, seed.org_a, fail_at_step=3)
        await drain_queue(settings)
        _, messages = await read_stream(client, run_id, owner)
    assert [m.data.get("status") for m in messages[-2:]] == ["failed", None]
    assert messages[-1].event == "run.failed"
    assert messages[-1].data["step"] == 3


async def test_reconnect_resumes_after_last_event_id(
    settings: Settings, issuer: TestIssuer, seed: Seed
) -> None:
    owner = bearer(issuer, seed.users["owner_a"])
    async with api(settings, issuer) as client:
        run_id = await start_fake_run(client, owner, seed.org_a, depth="D0")
        await drain_queue(settings)
        _, full = await read_stream(client, run_id, owner)
        _, resumed = await read_stream(client, run_id, owner, last_event_id=full[9].id)
    assert [(m.event, m.id, m.data) for m in resumed] == [
        (m.event, m.id, m.data) for m in full[10:]
    ]
    assert resumed[0].retry == 2000


async def test_reconnect_after_final_event_gets_204(
    settings: Settings, issuer: TestIssuer, seed: Seed
) -> None:
    owner = bearer(issuer, seed.users["owner_a"])
    async with api(settings, issuer) as client:
        run_id = await start_fake_run(client, owner, seed.org_a)
        await drain_queue(settings)
        _, full = await read_stream(client, run_id, owner)
        r, _ = await read_stream(client, run_id, owner, last_event_id=full[-1].id)
    assert r.status_code == 204
    assert r.content == b""


async def test_malformed_last_event_id_replays_from_start(
    settings: Settings, issuer: TestIssuer, seed: Seed
) -> None:
    owner = bearer(issuer, seed.users["owner_a"])
    async with api(settings, issuer) as client:
        run_id = await start_fake_run(client, owner, seed.org_a)
        await drain_queue(settings)
        _, full = await read_stream(client, run_id, owner)
        _, replay = await read_stream(client, run_id, owner, last_event_id="not-an-id")
    assert [(m.event, m.id, m.data) for m in replay] == [(m.event, m.id, m.data) for m in full]


async def test_unknown_run_is_404(settings: Settings, issuer: TestIssuer, seed: Seed) -> None:
    async with api(settings, issuer) as client:
        r, _ = await read_stream(client, uuid4(), bearer(issuer, seed.users["owner_a"]))
    assert r.status_code == 404


async def test_invalid_fake_run_request_is_422(
    settings: Settings, issuer: TestIssuer, seed: Seed
) -> None:
    owner = bearer(issuer, seed.users["owner_a"])
    async with api(settings, issuer) as client:
        bad_step = await client.post(
            "/api/v1/runs/fake", json={"org_id": str(seed.org_a), "fail_at_step": 16}, headers=owner
        )
        no_org = await client.post("/api/v1/runs/fake", json={}, headers=owner)
    assert bad_step.status_code == 422
    assert no_org.status_code == 422


# ── Setup: organisation → brand → product ─────────────────────────────────────────────────────


async def test_sign_up_flow_create_org_then_brand_then_product(
    settings: Settings, issuer: TestIssuer, new_user: Callable[[], UUID]
) -> None:
    user = new_user()
    me = bearer(issuer, user)
    async with api(settings, issuer) as client:
        assert (await client.get("/api/v1/me", headers=me)).json()["organisations"] == []
        org = await client.post("/api/v1/organisations", json={"name": "  Acme  "}, headers=me)
        assert org.status_code == 201
        org_id = org.json()["id"]
        assert org.json() == {"id": org_id, "name": "Acme", "roles": ["owner"]}
        listed = (await client.get("/api/v1/me", headers=me)).json()
        assert listed["user_id"] == str(user)
        assert listed["organisations"] == [{"id": org_id, "name": "Acme", "roles": ["owner"]}]

        brand = await client.post(
            "/api/v1/brands", json={"org_id": org_id, "name": "Acme Shoes"}, headers=me
        )
        assert brand.status_code == 201
        brand_id = brand.json()["id"]
        product = await client.post(
            "/api/v1/products",
            json={
                "org_id": org_id,
                "brand_id": brand_id,
                "name": "Trail runner",
                "category": "shoes",
            },
            headers=me,
        )
        assert product.status_code == 201
        product_id = product.json()["id"]

        brands = (await client.get(f"/api/v1/brands?org_id={org_id}", headers=me)).json()
        assert [b["id"] for b in brands] == [brand_id]
        products = (
            await client.get(f"/api/v1/products?org_id={org_id}&brand_id={brand_id}", headers=me)
        ).json()
        assert [(p["id"], p["name"], p["category"]) for p in products] == [
            (product_id, "Trail runner", "shoes")
        ]

        # Owners may delete what they created (no runs, no memory hold it).
        deleted_p = await client.delete(
            f"/api/v1/products/{product_id}?org_id={org_id}", headers=me
        )
        deleted_b = await client.delete(f"/api/v1/brands/{brand_id}?org_id={org_id}", headers=me)
        assert (deleted_p.status_code, deleted_b.status_code) == (204, 204)
        assert (await client.get(f"/api/v1/brands?org_id={org_id}", headers=me)).json() == []


async def test_another_orgs_brands_and_products_are_not_reachable(
    settings: Settings, issuer: TestIssuer, seed: Seed
) -> None:
    member_b = bearer(issuer, seed.users["approver_b"])
    owner_a = bearer(issuer, seed.users["owner_a"])
    a = str(seed.org_a)
    async with api(settings, issuer) as client:
        read = await client.get(f"/api/v1/brands?org_id={a}", headers=member_b)
        write = await client.post(
            "/api/v1/brands", json={"org_id": a, "name": "Intruder"}, headers=member_b
        )
        products = await client.get(f"/api/v1/products?org_id={a}", headers=member_b)
        delete = await client.delete(f"/api/v1/brands/{seed.brand_a}?org_id={a}", headers=member_b)
        # A's owner cannot hang a product in A off B's brand: the (brand_id, org_id) FK refuses it.
        cross = await client.post(
            "/api/v1/products",
            json={"org_id": a, "brand_id": str(seed.brand_b), "name": "Cross"},
            headers=owner_a,
        )
    assert [r.status_code for r in (read, write, products, delete)] == [404, 404, 404, 404]
    assert cross.status_code == 404


async def test_organisation_name_is_validated(
    settings: Settings, issuer: TestIssuer, new_user: Callable[[], UUID]
) -> None:
    me = bearer(issuer, new_user())
    async with api(settings, issuer) as client:
        empty = await client.post("/api/v1/organisations", json={"name": "   "}, headers=me)
        long = await client.post("/api/v1/organisations", json={"name": "x" * 121}, headers=me)
        extra = await client.post(
            "/api/v1/organisations",
            json={"name": "Mine", "owner_id": str(uuid4())},  # no way to name another owner
            headers=me,
        )
    assert [empty.status_code, long.status_code, extra.status_code] == [422, 422, 422]


# ── Live, over the wire ──────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def live_server(app: FastAPI) -> AsyncIterator[str]:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, lifespan="on")
    )
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            # uvicorn exposes only a flag, no event, for "started".
            while not server.started:  # noqa: ASYNC110
                await asyncio.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


async def test_events_arrive_live_while_the_run_progresses(
    settings: Settings, issuer: TestIssuer, seed: Seed
) -> None:
    """A real HTTP client sees each step as it happens, not one buffered body at the end."""
    delay_ms = 150
    owner = bearer(issuer, seed.users["owner_a"])
    worker = build_worker(settings)
    worker_task = asyncio.create_task(worker.async_run())
    try:
        app = create_app(settings, verifier=issuer.verifier())
        async with (
            live_server(app) as base,
            httpx.AsyncClient(base_url=base, timeout=30) as client,
        ):
            run_id = await start_fake_run(client, owner, seed.org_a, step_delay_ms=delay_ms)
            arrivals: list[tuple[float, SseMessage]] = []
            async with client.stream("GET", f"/api/v1/runs/{run_id}/stream", headers=owner) as resp:
                assert resp.status_code == 200
                buffer = ""
                async for chunk in resp.aiter_text():
                    buffer += chunk
                    *blocks, buffer = buffer.replace("\r\n", "\n").split("\n\n")
                    now = time.monotonic()
                    arrivals += [(now, m) for m in parse_stream("\n\n".join(blocks))]
    finally:
        worker.handle_sig(2)
        await worker.close()
        worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)

    events = [m for _, m in arrivals]
    assert events[-1].event == "run.completed"
    assert sum(1 for m in events if m.event == "step") == 30
    first_step = next(t for t, m in arrivals if m.event == "step")
    completed = arrivals[-1][0]
    assert completed - first_step >= 15 * delay_ms / 1000 * 0.8
    assert len({round(t, 2) for t, m in arrivals if m.event == "step"}) >= 10
