"""Liveness needs no dependency; the fake-run trigger is absent when disabled."""

import asyncio

import httpx
import pytest
import uvicorn

from app.api.main import UVICORN_LOOP, create_app
from tests.conftest import make_settings

pytestmark = pytest.mark.anyio


def client(**settings: object) -> httpx.AsyncClient:
    # ASGITransport does not run the lifespan: no Redis, no database.
    app = create_app(make_settings(**settings))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_answers_200_without_dependencies() -> None:
    async with client() as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_fake_trigger_not_mounted_when_disabled() -> None:
    async with client(fake_runs_enabled=False) as c:
        r = await c.post("/api/v1/runs/fake", json={})
    assert r.status_code in (404, 405)


async def test_stream_route_is_mounted_even_when_fakes_disabled() -> None:
    app = create_app(make_settings(fake_runs_enabled=False))
    paths = set(app.openapi()["paths"])
    assert "/api/v1/runs/{run_id}/stream" in paths
    assert "/api/v1/runs/fake" not in paths


def test_uvicorn_runs_the_api_on_a_selector_loop() -> None:
    # Resolved exactly as `uvicorn.run` does; psycopg's async mode refuses the Proactor loop.
    factory = uvicorn.Config(app=create_app(make_settings()), loop=UVICORN_LOOP).get_loop_factory()
    assert factory is not None
    loop = factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()
