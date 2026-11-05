"""Provider unavailability: a fifth infrastructure kind, bounded retries, a run-level breaker.

Owner rulings of 5 November 2026, after the provider answered 503 "high demand" to most agent calls
for over an hour and the runs recorded the outage as their own agent failures. A provider outage is
not a property of the arm being measured. Here a scripted fake provider answers 5xx and the gateway
and the classifier are driven for real; the real ledger's accounting is in
tests/db/test_provider_unavailable_accounting.py.
"""

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from litellm.exceptions import BadRequestError
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.infrastructure import INFRASTRUCTURE_FAILURES, classify_infrastructure_failure
from app.errors import (
    ABORTS,
    BudgetExceeded,
    LLMCallFailed,
    LLMRateLimited,
    ProviderCircuitOpen,
    ProviderUnavailable,
)
from app.evaluation.llm_attempts import CallRow, totals
from app.llm import _litellm
from app.llm.gateway import LLMGateway, is_provider_unavailable
from app.llm.ledger import CallContext
from app.llm.registry import Tier
from tests.llm_fakes import (
    FakeCompletion,
    FakeLedger,
    judge,
    make_registry,
    prompt_library,
    rate_limit,
    reply,
)

pytestmark = pytest.mark.anyio

OK = '{"supported": true, "confidence": 0.9}'
APP = Path(__file__).resolve().parents[1] / "app"


class Verdict(BaseModel):
    supported: bool
    confidence: float = Field(ge=0, le=1)


def unavailable(status: int = 503) -> Exception:
    """What LiteLLM raises for a 5xx, by status."""
    kinds: dict[int, Any] = {
        500: _litellm.InternalServerError,
        502: _litellm.BadGatewayError,
        503: _litellm.ServiceUnavailableError,
    }
    return kinds[status](
        message="This model is currently experiencing high demand.",
        llm_provider="gemini",
        model="gemini/test",
    )


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def gateway(
    completion: FakeCompletion, *, retries: int = 2, streak: int = 3
) -> tuple[LLMGateway, FakeLedger, Sleeps]:
    ledger, sleeps = FakeLedger(), Sleeps()
    gw = LLMGateway(
        make_registry(unique=False),
        ledger,  # type: ignore[arg-type]
        prompt_library(),
        api_key="test-key",
        timeout_s=30,
        max_rate_limit_waits=3,
        max_rate_limit_wait_s=90,
        unavailable_retries=retries,
        unavailable_backoff_s=2.0,
        circuit_breaker_streak=streak,
        completion=completion,
        sleep=sleeps,
    )
    return gw, ledger, sleeps


async def ask(gw: LLMGateway, call_ctx: CallContext) -> Verdict:
    return await gw.structured(
        Verdict,
        tier=Tier.CHEAP,
        prompt=judge(),
        ctx=call_ctx,
        route_reason="test",
        max_output_tokens=256,
    )


def new_ctx() -> CallContext:
    return CallContext(org_id=uuid4(), run_id=uuid4(), agent_id="test")


def outcomes(ledger: FakeLedger) -> list[str]:
    return [row["outcome"] for row in ledger.rows.values()]


# ── Retries: bounded, each on its own row ─────────────────────────────────────────────────────


async def test_one_503_is_retried_on_a_new_row_and_the_retry_is_the_result() -> None:
    gw, ledger, sleeps = gateway(FakeCompletion(unavailable(), reply(OK)))
    verdict = await ask(gw, new_ctx())
    assert verdict.supported is True
    assert outcomes(ledger) == ["provider_unavailable", "ok"]  # two rows: spend 2, work 1
    assert len(sleeps.calls) == 1 and 2.0 <= sleeps.calls[0] <= 2.5  # backoff 2 s plus jitter


async def test_two_503s_then_success_backs_off_exponentially() -> None:
    gw, ledger, sleeps = gateway(FakeCompletion(unavailable(), unavailable(), reply(OK)))
    await ask(gw, new_ctx())
    assert outcomes(ledger) == ["provider_unavailable", "provider_unavailable", "ok"]
    assert 2.0 <= sleeps.calls[0] <= 2.5 and 4.0 <= sleeps.calls[1] <= 5.0  # 2 s then 4 s


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_every_5xx_class_is_retried(status: int) -> None:
    gw, ledger, _ = gateway(FakeCompletion(unavailable(status), reply(OK)))
    await ask(gw, new_ctx())
    assert outcomes(ledger) == ["provider_unavailable", "ok"]


async def test_a_400_is_not_retried_it_is_an_error() -> None:
    bad = BadRequestError(message="bad", llm_provider="gemini", model="m")
    gw, ledger, sleeps = gateway(FakeCompletion(bad))  # type: ignore[arg-type]
    with pytest.raises(LLMCallFailed) as raised:
        await ask(gw, new_ctx())
    assert not isinstance(raised.value, ProviderUnavailable)
    assert outcomes(ledger) == ["error"] and sleeps.calls == []


async def test_retries_are_bounded_by_the_setting() -> None:
    gw, ledger, _ = gateway(FakeCompletion(unavailable(), unavailable()), retries=1, streak=9)
    with pytest.raises(ProviderUnavailable):
        await ask(gw, new_ctx())
    assert outcomes(ledger) == ["provider_unavailable", "provider_unavailable"]  # 1 try + 1 retry


async def test_with_no_retries_one_503_is_provider_unavailable_at_once() -> None:
    gw, ledger, sleeps = gateway(FakeCompletion(unavailable()), retries=0, streak=9)
    with pytest.raises(ProviderUnavailable):
        await ask(gw, new_ctx())
    assert outcomes(ledger) == ["provider_unavailable"] and sleeps.calls == []


# ── The run-level breaker ─────────────────────────────────────────────────────────────────────


async def test_three_consecutive_503s_open_the_breaker_and_abort() -> None:
    """Tonight's 35-errored-calls window: the third consecutive answer ends it."""
    completion = FakeCompletion(unavailable(), unavailable(), unavailable())  # a 4th would assert
    gw, ledger, _ = gateway(completion)
    with pytest.raises(ProviderCircuitOpen, match="3 consecutive provider-unavailable"):
        await ask(gw, new_ctx())
    assert outcomes(ledger) == ["provider_unavailable"] * 3
    assert len(completion.calls) == 3  # no fourth request reached the provider


async def test_an_open_breaker_refuses_every_later_call_in_that_run_without_a_row() -> None:
    gw, ledger, _ = gateway(FakeCompletion(unavailable(), unavailable(), unavailable()))
    run = new_ctx()
    with pytest.raises(ProviderCircuitOpen):
        await ask(gw, run)
    rows_before = len(ledger.rows)
    with pytest.raises(ProviderCircuitOpen):
        await ask(gw, run)  # the same run: refused before admission
    assert len(ledger.rows) == rows_before


async def test_the_breaker_is_per_run() -> None:
    gw, _, _ = gateway(
        FakeCompletion(unavailable(), unavailable(), unavailable(), reply(OK)), streak=3
    )
    with pytest.raises(ProviderCircuitOpen):
        await ask(gw, new_ctx())
    assert (await ask(gw, new_ctx())).supported is True  # another run is unaffected


async def test_a_success_resets_the_streak() -> None:
    script = [unavailable(), reply(OK), unavailable(), unavailable(), reply(OK)]
    gw, ledger, _ = gateway(FakeCompletion(*script))
    run = new_ctx()
    await ask(gw, run)  # 503 then ok: streak back to 0
    await ask(gw, run)  # 503, 503, ok: only 2 in a row, below 3
    assert outcomes(ledger) == [
        "provider_unavailable", "ok", "provider_unavailable", "provider_unavailable", "ok",
    ]  # fmt: skip


async def test_a_connection_error_is_a_network_error_not_an_outage() -> None:
    """LiteLLM gives APIConnectionError status_code 500 by default; a transport failure is
    `network_error`, never the fifth kind, and never opens the breaker (found 5 November: the
    offline request-body test, which cannot reach the provider, tripped it)."""
    connection = _litellm.APIConnectionError(message="no route", llm_provider="gemini", model="m")
    assert connection.status_code == 500
    assert not is_provider_unavailable(connection)
    assert classify_infrastructure_failure(connection) == "network_error"
    gw, ledger, sleeps = gateway(FakeCompletion(connection))
    with pytest.raises(LLMCallFailed) as raised:
        await ask(gw, new_ctx())
    assert not isinstance(raised.value, ProviderUnavailable | ProviderCircuitOpen)
    assert outcomes(ledger) == ["error"] and sleeps.calls == []  # one row, no retry


async def test_a_429_is_still_the_rate_limit_path_not_the_breaker() -> None:
    gw, ledger, _ = gateway(FakeCompletion(rate_limit(), reply(OK)))
    await ask(gw, new_ctx())
    assert outcomes(ledger) == ["ok"]  # waited out on the same row, as before


# ── Classification: the fifth kind ────────────────────────────────────────────────────────────


def test_there_are_five_infrastructure_kinds_and_the_fifth_is_provider_unavailable() -> None:
    assert INFRASTRUCTURE_FAILURES == (
        "rate_limited",
        "quota_exhausted",
        "network_error",
        "process_crash",
        "provider_unavailable",
    )


def test_provider_unavailable_and_the_breaker_classify_as_the_fifth_kind() -> None:
    assert classify_infrastructure_failure(ProviderUnavailable("x")) == "provider_unavailable"
    assert classify_infrastructure_failure(ProviderCircuitOpen("x")) == "provider_unavailable"
    wrapped = LLMCallFailed("model: ServiceUnavailableError: high demand")
    wrapped.__cause__ = unavailable()  # how the gateway used to record it
    assert classify_infrastructure_failure(wrapped) == "provider_unavailable"


def test_the_other_kinds_and_results_are_unchanged() -> None:
    assert classify_infrastructure_failure(LLMRateLimited("x")) == "rate_limited"
    assert classify_infrastructure_failure(ValueError("a bug")) is None
    assert classify_infrastructure_failure(BudgetExceeded("cap")) is None  # a result: partial
    bad = BadRequestError(message="bad", llm_provider="gemini", model="m")
    assert classify_infrastructure_failure(LLMCallFailed("x")) is None
    assert not is_provider_unavailable(bad)  # type: ignore[arg-type]


def test_a_bool_status_is_not_a_5xx() -> None:
    class Odd(Exception):
        status_code = True

    assert not is_provider_unavailable(Odd())


# ── The abort reaches the run ─────────────────────────────────────────────────────────────────


def test_every_catch_site_in_the_orchestrator_uses_the_shared_aborts_tuple() -> None:
    """A node that catches a failed agent must let an abort through. They share one name, so a
    new abort cannot be swallowed at a site that forgot it."""
    assert BudgetExceeded in ABORTS and ProviderCircuitOpen in ABORTS
    for relative in ("orchestrator/nodes.py", "core/claim_classifier.py"):
        source = (APP / relative).read_text(encoding="utf-8")
        assert "except BudgetExceeded" not in source, relative
        assert re.search(r"except ABORTS:\n\s+raise", source), relative


# ── Accounting and settings ───────────────────────────────────────────────────────────────────


def _row(outcome: str, last: bool = True, cost: float | None = 0.001) -> CallRow:
    return CallRow(
        id=1, run_id=uuid4(), step=7, revision_round=0, agent_id="A1", execution_id=uuid4(),
        attempt=1, last_attempt=last, outcome=outcome, prompt_id="p", input_tokens=10,
        output_tokens=5, cost_usd=cost, latency_ms=10,
    )  # fmt: skip


def test_a_retry_is_spend_and_never_work() -> None:
    rows = [_row("provider_unavailable"), _row("provider_unavailable"), _row("ok")]
    t = totals(rows)
    assert (t.spend_calls, t.work_calls, t.unavailable_calls) == (3, 1, 2)
    assert t.spend_calls == (
        t.work_calls + t.resume_duplicate_calls + t.orphaned_calls + t.unavailable_calls
    )


def test_the_settings_default_to_the_ruling() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (s.llm_unavailable_retries, s.llm_unavailable_backoff_s) == (2, 2.0)
    assert s.llm_circuit_breaker_streak == 3
