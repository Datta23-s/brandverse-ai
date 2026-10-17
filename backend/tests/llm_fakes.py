"""Test doubles for the LLM layer. No test calls a real model (Rules.md §6)."""

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from app.errors import CallRefused
from app.llm import _litellm
from app.llm.ledger import Admission, CallContext
from app.llm.registry import ModelRegistry, Tier


def make_registry(
    *, rpm: int = 15, rpd: int = 500, strong_rpd: int = 20, unique: bool = True
) -> ModelRegistry:
    """A registry with model IDs unique to the test, so usage counts never mix across tests."""
    tag = uuid4().hex[:8] if unique else "fixed"

    def tier(name: str, day: int, price_in: float, price_out: float) -> dict[str, object]:
        return {
            "provider": "gemini",
            "id": f"test-{name}-{tag}",
            "rate_limits": {"rpm": rpm, "tpm": 250000, "rpd": day},
            "price_per_1m_input_usd": price_in,
            "price_per_1m_output_usd": price_out,
        }

    return ModelRegistry.model_validate(
        {
            "verified_at": "2026-10-17",
            "rpd_resets_at": "00:00 America/Los_Angeles",
            "tiers": {
                "cheap": tier("cheap", rpd, 0.25, 1.50),
                "standard": tier("standard", rpd, 0.30, 2.50),
                "strong": tier("strong", strong_rpd, 0.75, 3.75),
                "media": {"provider": None, "id": None},
            },
        }
    )


def reply(content: str | None, prompt_tokens: int = 120, completion_tokens: int = 40) -> Any:
    """A LiteLLM-shaped completion response."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def rate_limit(retry_delay: str | None = "7s") -> Exception:
    info = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}
    error: dict[str, object] = {"code": 429, "status": "RESOURCE_EXHAUSTED"}
    if retry_delay:
        error["details"] = [info]
    body = json.dumps({"error": error})  # Google's 429 body shape
    return _litellm.RateLimitError(body, llm_provider="gemini", model="gemini/test")


class FakeCompletion:
    """Scripted provider: each call takes the next item (a response, or an exception to raise)."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("the fake model was called more times than scripted")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@dataclass
class FakeLedger:
    """The ledger's interface in memory: scripted admissions, recorded rows."""

    waits: list[float] = field(default_factory=list)  # wait_s answers before admitting
    refusal: CallRefused | None = None
    rows: dict[int, dict[str, Any]] = field(default_factory=dict)
    refused: list[str] = field(default_factory=list)

    async def admit(
        self, ctx: CallContext, tier: Tier, route_reason: str, estimated_cost_usd: float
    ) -> Admission:
        if self.refusal is not None:
            self.refused.append(str(self.refusal))
            raise self.refusal
        if self.waits:
            return Admission(call_id=None, wait_s=self.waits.pop(0))
        call_id = len(self.rows) + 1
        self.rows[call_id] = {
            "tier": tier.value,
            "route_reason": route_reason,
            "outcome": "pending",
            "estimate": estimated_cost_usd,
        }
        return Admission(call_id=call_id)

    async def refuse(self, ctx: CallContext, tier: Tier, reason: str) -> None:
        self.refused.append(reason)

    async def finish(self, call_id: int, **values: Any) -> None:
        assert self.rows[call_id]["outcome"] == "pending", "finished twice"
        self.rows[call_id].update(values)


def ctx() -> CallContext:
    return CallContext(org_id=uuid4(), run_id=uuid4(), agent_id="test")
