"""Shared fixtures: the async backend, and an isolated Redis for `@pytest.mark.redis` tests.

Async tests run on a `SelectorEventLoop`, the loop the API and worker run on (psycopg needs it).

Redis tests use REDIS_TEST_URL (default `redis://127.0.0.1:6379/15`) and flush that database before
each test, so they refuse database 0, where the development queue lives. With
REQUIRE_REDIS_TESTS=1 (set in CI) an unreachable Redis is a failure, not a skip.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

import pytest
from redis.asyncio import Redis

from app.core.config import Settings

DEFAULT_REDIS_TEST_URL = "redis://127.0.0.1:6379/15"

# ── Nothing in the suite may reach a real model provider or load the real encoder ───────────────
# (owner, 3 November 2026). Every real provider call needs the owner's approval, and a test that
# could make one silently is a hole, not a risk to be understood. Found 3 November: importing
# LiteLLM loaded the repository's `.env` into os.environ, so a local run held a live key and a
# test worker built the real pipeline and loaded the 1 GB encoder (CI has no `.env`, so it never
# did). Three independent layers, each shown to refuse in tests/test_real_call_guard.py:
#   1. app/llm/_litellm.py stops LiteLLM reading `.env`;
#   2. every provider key is removed from the test process's environment;
#   3. the provider call and the real encoder construction themselves raise.
# A test that genuinely needs one must carry the marker AND be named in the set below; a marker not
# in the set, or a name in the set without the marker, fails collection. No test in the suite
# loads the real encoder (that set is empty); live work is scripts/, not tests/.
#
# The one entry: tests/test_llm_request_body.py builds the REAL provider client and intercepts its
# HTTP post, so the request body is inspected and nothing leaves the machine (it passes an offline
# dummy key and the ledger is a fake). Listed by function, so every parametrisation is covered.
REAL_PROVIDER_TESTS: frozenset[str] = frozenset(
    {
        "tests/test_llm_request_body.py::test_every_structured_schema_is_sent_in_the_provider_safe_form"
    }
)
REAL_ENCODER_TESTS: frozenset[str] = frozenset()
PROVIDER_KEYS = ("GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


class RealCallRefused(AssertionError):
    """A test tried to reach a real provider or load the real encoder without being marked."""


def real_call_allowed(marks: set[str], what: str, allowed: frozenset[str], nodeid: str) -> bool:
    """True only for a test that carries the marker AND is listed. Pure, so it can be tested."""
    return what in marks and nodeid.split("[")[0] in allowed


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for marker, allowed in (
        ("real_provider", REAL_PROVIDER_TESTS),
        ("real_encoder", REAL_ENCODER_TESTS),
    ):
        marked = {i.nodeid.split("[")[0] for i in items if i.get_closest_marker(marker) is not None}
        stray = marked - allowed
        if stray:
            raise pytest.UsageError(f"@{marker} on tests not listed in tests/conftest.py: {stray}")
        ids = {i.nodeid.split("[")[0] for i in items}
        listed_but_unmarked = (allowed & ids) - marked
        if listed_but_unmarked:
            raise pytest.UsageError(f"listed as {marker} but not marked: {listed_but_unmarked}")


@pytest.fixture(autouse=True)
def _no_real_provider_or_encoder(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    marks = {m.name for m in request.node.iter_markers()}
    nodeid = request.node.nodeid
    for key in PROVIDER_KEYS:  # always: even a listed test never needs a real key
        monkeypatch.delenv(key, raising=False)
    if not real_call_allowed(marks, "real_provider", REAL_PROVIDER_TESTS, nodeid):

        async def refuse_provider(*_a: Any, **_k: Any) -> Any:
            raise RealCallRefused(f"{nodeid}: a real model-provider call, not marked real_provider")

        import app.llm._litellm as provider

        monkeypatch.setattr(provider, "acompletion", refuse_provider)
    if not real_call_allowed(marks, "real_encoder", REAL_ENCODER_TESTS, nodeid):
        from app.memory.embeddings import LocalEncoder

        real_load = LocalEncoder.load.__func__  # type: ignore[attr-defined]

        def guarded_load(
            cls: type[LocalEncoder], cfg: Any, download: Any = None, construct: Any = None
        ) -> LocalEncoder:
            if construct is None:  # nothing injected: this would import sentence-transformers
                raise RealCallRefused(f"{nodeid}: the real encoder, not marked real_encoder")
            return real_load(cls, cfg, download, construct)  # type: ignore[no-any-return]

        monkeypatch.setattr(LocalEncoder, "load", classmethod(guarded_load))


@pytest.fixture
def anyio_backend() -> tuple[str, dict[str, Any]]:
    return ("asyncio", {"loop_factory": asyncio.SelectorEventLoop})


def make_settings(**overrides: Any) -> Settings:
    """Settings for tests: never read `.env`, never point at Supabase."""
    values: dict[str, Any] = {"environment": "test", "database_url": None}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
async def redis_settings() -> AsyncIterator[Settings]:
    url = os.environ.get("REDIS_TEST_URL", DEFAULT_REDIS_TEST_URL)
    db = urlsplit(url).path.lstrip("/") or "0"
    if db == "0":
        pytest.exit("REDIS_TEST_URL must not use database 0 (the development queue)", returncode=4)

    client = Redis.from_url(url)
    try:
        await client.ping()
    except Exception as e:
        await client.aclose()
        if os.environ.get("REQUIRE_REDIS_TESTS") == "1":
            pytest.fail(f"REQUIRE_REDIS_TESTS=1 but Redis at {url} is unreachable: {e}")
        pytest.skip(f"Redis at {url} unreachable; start it with `docker compose up -d redis`")
    await client.flushdb()
    try:
        yield make_settings(redis_url=url, run_events_block_ms=200)
    finally:
        await client.flushdb()
        await client.aclose()
