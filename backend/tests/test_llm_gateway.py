"""The gateway's call path with a scripted fake model and an in-memory ledger (no real model)."""

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel, Field

from app.errors import BudgetExceeded, LLMCallFailed, LLMRateLimited, StructuredOutputInvalid
from app.llm.gateway import LLMGateway, response_format, retry_delay_s
from app.llm.prompts import PromptRef
from app.llm.registry import Tier
from tests.llm_fakes import (
    FakeCompletion,
    FakeLedger,
    ctx,
    judge,
    make_registry,
    prompt_library,
    rate_limit,
    reply,
)

pytestmark = pytest.mark.anyio


class Verdict(BaseModel):
    supported: bool
    confidence: float = Field(ge=0, le=1)


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def gateway(
    completion: FakeCompletion,
    ledger: FakeLedger | None = None,
    *,
    max_waits: int = 3,
    max_wait_s: float = 90,
) -> tuple[LLMGateway, FakeLedger, Sleeps]:
    ledger = ledger or FakeLedger()
    sleeps = Sleeps()
    gw = LLMGateway(
        make_registry(unique=False),
        ledger,  # type: ignore[arg-type]
        prompt_library(),
        api_key="test-key",
        timeout_s=30,
        max_rate_limit_waits=max_waits,
        max_rate_limit_wait_s=max_wait_s,
        completion=completion,
        sleep=sleeps,
    )
    return gw, ledger, sleeps


async def ask(gw: LLMGateway, tier: Tier = Tier.CHEAP) -> Verdict:
    return await gw.structured(
        Verdict,
        tier=tier,
        prompt=judge(),
        ctx=ctx(),
        route_reason="test: default tier",
        max_output_tokens=256,
    )


async def test_valid_output_is_returned_as_the_schema_and_logged() -> None:
    completion = FakeCompletion(reply('{"supported": true, "confidence": 0.9}', 120, 40))
    gw, ledger, _ = gateway(completion)
    verdict = await ask(gw)
    assert verdict == Verdict(supported=True, confidence=0.9)
    (row,) = ledger.rows.values()
    assert row["outcome"] == "ok"
    assert (row["input_tokens"], row["output_tokens"]) == (120, 40)
    assert row["cost_usd"] == pytest.approx((120 * 0.25 + 40 * 1.50) / 1e6)
    assert row["rate_limit_wait_ms"] == 0
    assert row["latency_ms"] >= 0
    assert row["route_reason"] == "test: default tier"
    assert row["prompt"] == PromptRef("test_judge", "v1")


async def test_the_rendered_template_is_what_the_model_receives() -> None:
    completion = FakeCompletion(reply('{"supported": true, "confidence": 0.9}'))
    gw, _, _ = gateway(completion)
    await ask(gw)
    assert completion.calls[0]["messages"] == [
        {"role": "system", "content": "Judge the claim."},
        {"role": "user", "content": "Is water wet?"},
    ]


async def test_every_call_disables_hidden_retries_and_names_the_schema() -> None:
    completion = FakeCompletion(reply('{"supported": false, "confidence": 0.1}'))
    gw, _, _ = gateway(completion)
    await ask(gw)
    (kwargs,) = completion.calls
    assert kwargs["num_retries"] == 0 and kwargs["max_retries"] == 0
    # The provider-safe schema, by name — never the raw Pydantic class (app/llm/schema.py).
    assert kwargs["response_format"] == response_format(Verdict)
    assert kwargs["response_format"]["json_schema"]["name"] == "Verdict"
    assert kwargs["max_tokens"] == 256
    assert kwargs["api_key"] == "test-key"
    assert kwargs["model"].startswith("gemini/test-cheap-")


@pytest.mark.parametrize("bad", ["The claim is supported.", '{"supported": "maybe"}', "", None])
async def test_invalid_output_is_repaired_once(bad: str | None) -> None:
    completion = FakeCompletion(reply(bad), reply('{"supported": true, "confidence": 0.6}'))
    gw, ledger, _ = gateway(completion)
    assert await ask(gw) == Verdict(supported=True, confidence=0.6)
    first, second = ledger.rows.values()
    assert first["outcome"] == "invalid_output"
    assert second["outcome"] == "ok"
    # The repair is its own versioned template, recorded as such; the reason names what it repairs.
    assert first["prompt"] == PromptRef("test_judge", "v1")
    assert second["prompt"] == PromptRef("structured_repair", "v1")
    assert second["route_reason"].startswith(
        "structured_repair@v1 after invalid output of test_judge@v1: "
    )
    repair_messages = completion.calls[1]["messages"]
    assert repair_messages[-2] == {"role": "assistant", "content": bad or ""}
    assert "single JSON object" in repair_messages[-1]["content"]
    assert repair_messages[-1] != completion.calls[0]["messages"][-1]  # never the same prompt


async def test_invalid_twice_fails_loudly_after_exactly_two_calls() -> None:
    completion = FakeCompletion(reply("Sure! It is wet."), reply("Yes, supported."))
    gw, ledger, _ = gateway(completion)
    with pytest.raises(StructuredOutputInvalid, match="Verdict: invalid after one repair"):
        await ask(gw)
    assert len(completion.calls) == 2
    assert [r["outcome"] for r in ledger.rows.values()] == ["invalid_output", "invalid_output"]
    # Tokens spent on invalid output are still recorded.
    assert all(r["cost_usd"] > 0 for r in ledger.rows.values())


async def test_a_429_is_waited_out_and_the_same_request_resent() -> None:
    ok = reply('{"supported": true, "confidence": 1}')
    completion = FakeCompletion(rate_limit("7s"), rate_limit("3s"), ok)
    gw, ledger, sleeps = gateway(completion)
    await ask(gw)
    assert sleeps.calls == [7.0, 3.0]  # Google's retryDelay honoured
    assert len(completion.calls) == 3
    assert completion.calls[0]["messages"] == completion.calls[2]["messages"]  # same request
    (row,) = ledger.rows.values()  # one call, one row: waits are not failures (Rules.md C10)
    assert row["outcome"] == "ok"
    assert row["rate_limit_wait_ms"] == 10_000


async def test_rate_limit_waits_are_capped_in_number() -> None:
    completion = FakeCompletion(*[rate_limit("1s")] * 5)
    gw, ledger, sleeps = gateway(completion, max_waits=2)
    with pytest.raises(LLMRateLimited, match="after 2 waits"):
        await ask(gw)
    assert len(completion.calls) == 3  # first try + 2 waits, never more
    assert len(sleeps.calls) == 2
    (row,) = ledger.rows.values()
    assert row["outcome"] == "rate_limited"
    assert row["rate_limit_wait_ms"] == 2000


async def test_rate_limit_waits_are_capped_in_total_time() -> None:
    completion = FakeCompletion(rate_limit("40s"), rate_limit("40s"), rate_limit("40s"))
    gw, ledger, sleeps = gateway(completion, max_waits=10, max_wait_s=60)
    with pytest.raises(LLMRateLimited):
        await ask(gw)
    assert sleeps.calls == [40.0]  # a second 40 s wait would pass the 60 s cap
    assert list(ledger.rows.values())[0]["outcome"] == "rate_limited"


async def test_a_429_without_retry_delay_backs_off_exponentially() -> None:
    completion = FakeCompletion(
        rate_limit(None), rate_limit(None), reply('{"supported": true, "confidence": 1}')
    )
    gw, _, sleeps = gateway(completion)
    await ask(gw)
    assert 1 <= sleeps.calls[0] < 2 and 2 <= sleeps.calls[1] < 3


async def test_other_provider_errors_are_recorded_and_not_retried() -> None:
    completion = FakeCompletion(RuntimeError("upstream exploded"), reply("{}"))
    gw, ledger, _ = gateway(completion)
    with pytest.raises(LLMCallFailed, match="upstream exploded"):
        await ask(gw)
    assert len(completion.calls) == 1
    assert list(ledger.rows.values())[0]["outcome"] == "error"


async def test_cancellation_completes_the_row_and_propagates() -> None:
    completion = FakeCompletion(asyncio.CancelledError())
    gw, ledger, _ = gateway(completion)
    with pytest.raises(asyncio.CancelledError):
        await ask(gw)
    assert list(ledger.rows.values())[0]["outcome"] == "error"


async def test_rpm_pacing_waits_and_records_the_wait() -> None:
    completion = FakeCompletion(reply('{"supported": true, "confidence": 1}'))
    gw, ledger, sleeps = gateway(completion, ledger=FakeLedger(waits=[4.0, 2.5]))
    await ask(gw)
    assert sleeps.calls == [4.0, 2.5]
    assert list(ledger.rows.values())[0]["rate_limit_wait_ms"] == 6500


async def test_rpm_pacing_beyond_the_cap_is_refused_and_recorded() -> None:
    completion = FakeCompletion()
    gw, ledger, _ = gateway(completion, ledger=FakeLedger(waits=[50.0, 50.0]), max_wait_s=60)
    with pytest.raises(LLMRateLimited, match="pacing"):
        await ask(gw)
    assert completion.calls == []
    assert ledger.refused and "pacing" in ledger.refused[0]


async def test_a_refused_call_never_reaches_the_model() -> None:
    completion = FakeCompletion()
    ledger = FakeLedger(refusal=BudgetExceeded("refused: run has made 20 of 20 LLM calls"))
    gw, _, _ = gateway(completion, ledger=ledger)
    with pytest.raises(BudgetExceeded):
        await ask(gw)
    assert completion.calls == []


@pytest.mark.parametrize(
    ("message", "expected"),
    [('"retryDelay": "29s"', 29.0), ("retryDelay: 1.5s", 1.5), ("no hint", None)],
)
def test_retry_delay_parsing(message: str, expected: Any) -> None:
    assert retry_delay_s(Exception(message)) == expected


async def test_the_strong_tier_is_refused_unless_explicitly_allowed() -> None:
    from app.errors import StrongTierDisabled

    completion = FakeCompletion()
    gw, ledger, _ = gateway(completion)
    with pytest.raises(StrongTierDisabled, match="strong tier"):
        await ask(gw, tier=Tier.STRONG)
    assert completion.calls == []  # never reaches the provider
    assert ledger.rows == {}  # never admitted, so never counted
    assert ledger.refused and "disabled" in ledger.refused[0]  # but recorded
    assert ledger.refused_prompts == [PromptRef("test_judge", "v1")]  # with its prompt


async def test_the_strong_tier_works_when_allowed() -> None:
    completion = FakeCompletion(reply('{"supported": true, "confidence": 1}'))
    sleeps = Sleeps()
    gw = LLMGateway(
        make_registry(unique=False),
        FakeLedger(),  # type: ignore[arg-type]
        prompt_library(),
        api_key="k",
        timeout_s=5,
        max_rate_limit_waits=0,
        max_rate_limit_wait_s=1,
        allow_strong_tier=True,
        completion=completion,
        sleep=sleeps,
    )
    await ask(gw, tier=Tier.STRONG)
    assert completion.calls[0]["model"].startswith("gemini/test-strong-")
