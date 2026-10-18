"""Every structured schema the gateway sends, checked offline in the request body LiteLLM builds
(app/llm/schema.py; found by the failed V2 probe, 2026-10-18).

The schemas are DISCOVERED, not listed: every `<x>.structured(<Schema>, ...)` call in backend/app
and backend/scripts is found by parsing the source, so a new structured call is covered the moment
it is written. Each is sent through the real gateway and LiteLLM, and the HTTP post is intercepted
before anything leaves the machine; the schema in the body must use only the allow-listed
keywords — no `$defs`, `$ref`, `additionalProperties`, `anyOf` or `default`.
"""

import ast
import asyncio
import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from litellm.llms.custom_httpx import http_handler
from pydantic import BaseModel

from app.core.config import get_settings
from app.errors import LLMCallFailed
from app.llm.gateway import LLMGateway
from app.llm.registry import Tier, registry_from
from app.llm.schema import ALLOWED_KEYWORDS, SchemaNotSupported, provider_schema
from tests.llm_fakes import FakeLedger, ctx, judge, prompt_library

BACKEND = Path(__file__).resolve().parents[1]


def discovered_schemas() -> dict[str, type[BaseModel]]:
    """`module:Name` -> class, for the first argument of every `.structured(...)` call."""
    found: dict[str, type[BaseModel]] = {}
    for path in sorted(
        [*BACKEND.joinpath("app").rglob("*.py"), *BACKEND.joinpath("scripts").rglob("*.py")]
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = ".".join(path.relative_to(BACKEND).with_suffix("").parts)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "structured"
                and node.args
                and isinstance(node.args[0], ast.Name)
            ):
                cls = getattr(importlib.import_module(module), node.args[0].id)
                found[f"{module}:{node.args[0].id}"] = cls
    return found


SCHEMAS = discovered_schemas()


def test_discovery_finds_the_known_structured_calls() -> None:
    names = {k.split(":")[1] for k in SCHEMAS}
    assert {"ExtractedStatements", "CapitalCity"} <= names, sorted(SCHEMAS)
    assert all(issubclass(c, BaseModel) for c in SCHEMAS.values())


def _keywords(node: Any) -> set[str]:
    """Every schema keyword used, at any depth (property names are not keywords)."""
    if isinstance(node, list):
        return set().union(*map(_keywords, node)) if node else set()
    if not isinstance(node, dict):
        return set()
    out = set(node)
    for key, value in node.items():
        if key == "properties":
            out |= set().union(*map(_keywords, value.values())) if value else set()
        elif key == "items":
            out |= _keywords(value)
    return out


def _sent_body(
    schema: type[BaseModel], tier: Tier, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    captured: list[Any] = []

    async def intercept(
        self: object, url: str, data: Any = None, json: Any = None, **_: Any
    ) -> Any:
        captured.append(json if json is not None else data)
        raise ConnectionError("intercepted: nothing leaves the machine")

    monkeypatch.setattr(http_handler.AsyncHTTPHandler, "post", intercept)
    settings = get_settings()
    gateway = LLMGateway(
        registry_from(settings.models_config_path),  # the real registry: the real model ids
        FakeLedger(),  # type: ignore[arg-type]
        prompt_library(),
        api_key="offline-test-key",
        timeout_s=5,
        max_rate_limit_waits=0,
        max_rate_limit_wait_s=1,
        allow_strong_tier=True,
    )
    with pytest.raises(LLMCallFailed):  # the intercepted post fails the call, as intended
        asyncio.run(
            gateway.structured(schema, tier=tier, prompt=judge(), ctx=ctx(), route_reason="offline")
        )
    assert captured, "no request body was built"
    body = captured[0]
    return json.loads(body) if isinstance(body, (str, bytes)) else body


@pytest.mark.parametrize("tier", [Tier.CHEAP, Tier.STANDARD, Tier.STRONG])
@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_every_structured_schema_is_sent_in_the_provider_safe_form(
    name: str, tier: Tier, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _sent_body(SCHEMAS[name], tier, monkeypatch)
    sent = body["generationConfig"]["response_json_schema"]
    used = _keywords(sent)
    assert used <= ALLOWED_KEYWORDS, f"{name} sends {sorted(used - ALLOWED_KEYWORDS)}"
    assert not {"$defs", "$ref", "additionalProperties", "anyOf", "default"} & used
    assert sent == provider_schema(SCHEMAS[name])  # LiteLLM passed it through unchanged


# ── provider_schema itself ───────────────────────────────────────────────────────────────────


class Inner(BaseModel):
    code: str | None
    weight: float = 1.0


class Outer(BaseModel):
    items: list[Inner]
    main: Inner
    kind: str = "x"


def test_refs_are_inlined_and_nullables_become_type_lists() -> None:
    schema = provider_schema(Outer)
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)
    inner = schema["properties"]["main"]
    assert inner["properties"]["code"]["type"] == ["string", "null"]
    assert "default" not in inner["properties"]["weight"]
    assert schema["properties"]["items"]["items"] == inner


def test_a_construct_the_provider_form_cannot_express_is_refused() -> None:
    class Either(BaseModel):
        value: int | str

    with pytest.raises(SchemaNotSupported, match="nullable single-type anyOf"):
        provider_schema(Either)
