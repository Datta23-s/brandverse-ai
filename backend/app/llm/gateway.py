"""The LLM gateway: the only path to a model (Rules.md P6). Every call goes through LiteLLM
(app/llm/_litellm.py), is admitted by the ledger before it is made, and is logged to llm_calls.

`structured()` takes a RenderedPrompt — a versioned template with its variables filled in
(app/llm/prompts.py), never an inline string — and returns an instance of the requested Pydantic
schema, never a raw string (Rules.md §3.1). Each llm_calls row records the template's id and
version. An output that does not validate (prose, broken JSON, a schema mismatch) is repaired ONCE
with a different prompt, the `structured_repair` template, which quotes the validation errors; if
the repair fails too, StructuredOutputInvalid is raised. That is at most two calls per request,
within Rules.md C8.

The strong tier (20 requests/day, shared with the evaluation) is refused unless the gateway is
built with `allow_strong_tier=True` (Settings.llm_allow_strong_tier, false by default): no
development, test or smoke call can spend it, whichever database the ledger writes to.

It can instead be scoped (owner, 2026-10-28, to measure the strong calls a run takes on the tuning
set): built with `strong_tier_org_id`, the gateway admits a strong call only from that organisation,
and only while the ledger reports the organisation flagged `is_practice` (checked on every call, so
an unflagged organisation, a missing one, or any other organisation is refused before admission and
before any provider call). A scope, once set, overrides `allow_strong_tier`: it can only narrow.

Rate limits (Rules.md C10): the ledger paces calls to the model's RPM from llm_calls; a 429 that
still occurs is waited out (Google's retryDelay if given, else exponential backoff with jitter) and
the SAME request re-sent. Waits are capped in number and total seconds — no loop runs unbounded
against a model — and are recorded in rate_limit_wait_ms, apart from latency_ms. Any other provider
error is recorded and raised, not retried. LiteLLM's own retries are switched off on every call,
so the only retries are the ones written here.
"""

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.errors import (
    LLMCallFailed,
    LLMRateLimited,
    ProviderCircuitOpen,
    ProviderUnavailable,
    StrongTierDisabled,
    StructuredOutputInvalid,
)
from app.llm import _litellm
from app.llm.ledger import CallContext, CallLedger
from app.llm.prompts import PromptLibrary, PromptRef, RenderedPrompt
from app.llm.registry import ModelRegistry, Tier
from app.llm.schema import provider_schema

log = logging.getLogger(__name__)

Completion = Callable[..., Awaitable[Any]]

# The repair template, pinned by version like any other prompt (config/prompts/).
REPAIR_PROMPT = ("structured_repair", "v1")
_RETRY_DELAY = re.compile(r'"?retryDelay"?\s*[:=]\s*"?(\d+(?:\.\d+)?)s')


def response_format(schema: type[BaseModel]) -> dict[str, Any]:
    """What LiteLLM is given for structured output: the provider-safe schema, by name."""
    return {
        "type": "json_schema",
        "json_schema": {"name": schema.__name__, "schema": provider_schema(schema)},
    }


@dataclass(frozen=True, slots=True)
class _Attempt:
    call_id: int
    content: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    wait_ms: int


def estimate_tokens(messages: list[dict[str, str]]) -> int:
    """A deliberately generous estimate (about 3 characters per token) for the pre-flight check."""
    return sum(len(m["content"]) for m in messages) // 3 + 16


def is_provider_unavailable(error: BaseException) -> bool:
    """The provider is unavailable: a server-side 5xx (503 "high demand", 500, 502, 504, ...).
    NOT a 429 (that is the rate-limit path above, kind `rate_limited`) and NOT a transport failure
    (kind `network_error`). The fifth infrastructure kind, `provider_unavailable`, fixed by the
    amendment of 2026-11-05 (docs/evaluation-plan.md): an outage is not a property of the arm."""
    if isinstance(error, _litellm.APIConnectionError):
        # LiteLLM gives a transport failure (and its Timeout) status_code 500 by default. It is a
        # network error (`network_error`), not the provider answering that it is unavailable.
        return False
    if isinstance(
        error,
        _litellm.ServiceUnavailableError | _litellm.InternalServerError | _litellm.BadGatewayError,
    ):
        return True
    status = getattr(error, "status_code", None)
    return isinstance(status, int) and not isinstance(status, bool) and status >= 500


def retry_delay_s(error: Exception) -> float | None:
    """Google's suggested wait from a 429 body (`"retryDelay": "29s"`), if present."""
    match = _RETRY_DELAY.search(str(error))
    return float(match.group(1)) if match else None


def _parse[M: BaseModel](schema: type[M], content: str) -> tuple[M | None, str]:
    try:
        return schema.model_validate_json(content), ""
    except ValidationError as e:
        details = "; ".join(
            f"{'.'.join(map(str, x['loc'])) or '(root)'}: {x['msg']}" for x in e.errors()
        )
        return None, details[:2000]


class LLMGateway:
    def __init__(
        self,
        registry: ModelRegistry,
        ledger: CallLedger,
        prompts: PromptLibrary,
        *,
        api_key: str | None,
        timeout_s: float,
        max_rate_limit_waits: int,
        max_rate_limit_wait_s: float,
        allow_strong_tier: bool = False,
        strong_tier_org_id: UUID | None = None,
        unavailable_retries: int = 2,
        unavailable_backoff_s: float = 2.0,
        circuit_breaker_streak: int = 3,
        unavailable_total_cap: int = 6,
        completion: Completion | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        # Resolved here, not as a default argument, so the provider call is looked up when a
        # gateway is built and a test fixture can replace it (tests/conftest.py).
        if completion is None:
            completion = _litellm.acompletion
        if completion is _litellm.acompletion and getattr(ledger, "dev_budget", None) is None:
            # A real provider call with no development ceiling is a hole (owner, 2026-11-01): the
            # ceiling and the billing condition are enforced in the ledger, so a gateway that
            # reaches the provider through a ledger without them cannot be built. There is no
            # production exemption either: when evaluation runs begin, the owner replaces the
            # file's figures and registers the live database in it.
            raise ValueError(
                "a gateway that calls the provider needs a ledger with a dev_budget "
                "(config/dev_budget.yaml)"
            )
        self._registry = registry
        self._ledger = ledger
        self._repair = prompts.get(*REPAIR_PROMPT)  # fails at start-up, not at the first repair
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._max_waits = max_rate_limit_waits
        self._max_wait_s = max_rate_limit_wait_s
        self._allow_strong = allow_strong_tier
        self._strong_org = strong_tier_org_id
        if strong_tier_org_id is not None and (
            ledger.strong_cap is None or ledger.strong_cap[0] != strong_tier_org_id
        ):
            # A scope with no cap is a hole: refuse to build a gateway that has one.
            raise ValueError(
                "strong_tier_org_id needs the ledger's strong_cap for the same organisation "
                "(llm_strong_tier_max_calls)"
            )
        self._completion = completion
        self._sleep = sleep
        self._clock = clock
        self._today = today
        if unavailable_retries < 0 or unavailable_backoff_s < 0 or circuit_breaker_streak < 1:
            raise ValueError("retries and backoff cannot be negative; the breaker streak is >= 1")
        if unavailable_total_cap < circuit_breaker_streak:
            # A total below the streak would make the streak unreachable and so meaningless.
            raise ValueError("unavailable_total_cap must be at least circuit_breaker_streak")
        self._unavailable_retries = unavailable_retries
        self._unavailable_backoff_s = unavailable_backoff_s
        self._breaker_streak = circuit_breaker_streak
        self._unavailable_total_cap = unavailable_total_cap
        # TWO bounds per run, both needed (owner, 2026-11-05). The STREAK bounds a dead provider;
        # it does not bound a FLAKY one, because a success resets it, which is why the 5 November
        # attempts cost 5, 9, 10 and 14 calls instead of the 5 expected. The TOTAL is never reset by
        # a success, so the worst case per run is quotable rather than estimated. Both are in
        # memory on purpose: they protect a run that is executing now, and a restarted worker
        # starts clean (its runs are re-driven from their checkpoints).
        self._streak: dict[UUID, int] = {}
        self._unavailable_total: dict[UUID, int] = {}

    def _breaker_reason(self, ctx: CallContext) -> str | None:
        """Why this run may make no further call, or None. The streak OR the total."""
        streak = self._streak.get(ctx.run_id, 0)
        total = self._unavailable_total.get(ctx.run_id, 0)
        if streak >= self._breaker_streak:
            return f"{streak} consecutive provider-unavailable responses"
        if total >= self._unavailable_total_cap:
            return (
                f"{total} provider-unavailable responses in this run (the total cap is "
                f"{self._unavailable_total_cap}; a success resets the streak, not the total)"
            )
        return None

    def _raise_if_open(self, ctx: CallContext) -> None:
        if (reason := self._breaker_reason(ctx)) is not None:
            raise ProviderCircuitOpen(
                f"run {ctx.run_id}: {reason}; no further calls are made in this run"
            )

    def _note_unavailable(self, ctx: CallContext) -> tuple[int, int]:
        """Count this answer against both bounds; returns (streak, total)."""
        if len(self._streak) > 4096:  # bounded: forget the oldest runs
            for old in list(self._streak)[:1024]:
                self._streak.pop(old, None)
                self._unavailable_total.pop(old, None)
        self._streak[ctx.run_id] = self._streak.get(ctx.run_id, 0) + 1
        self._unavailable_total[ctx.run_id] = self._unavailable_total.get(ctx.run_id, 0) + 1
        return self._streak[ctx.run_id], self._unavailable_total[ctx.run_id]

    def _note_ok(self, ctx: CallContext) -> None:
        """A success resets the STREAK. It deliberately does NOT reset the total."""
        self._streak.pop(ctx.run_id, None)

    async def structured[M: BaseModel](
        self,
        schema: type[M],
        *,
        tier: Tier,
        prompt: RenderedPrompt,
        ctx: CallContext,
        route_reason: str,
        max_output_tokens: int = 1024,
    ) -> M:
        messages = [{"role": "system", "content": prompt.system}] if prompt.system else []
        messages.append({"role": "user", "content": prompt.user})
        first = await self._attempt(
            schema,
            messages,
            tier,
            prompt.ref,
            ctx,
            route_reason,
            max_output_tokens,
            prompt.temperature,
        )
        parsed, errors = _parse(schema, first.content)
        await self._finish(first, "ok" if parsed is not None else "invalid_output")
        if parsed is not None:
            return parsed

        log.warning(
            "structured output invalid; one repair",
            extra={"schema": schema.__name__, "tier": tier.value, "errors": errors},
        )
        repair_prompt = self._repair.render(errors=errors)
        repair = messages + [
            {"role": "assistant", "content": first.content},
            {"role": "user", "content": repair_prompt.user},
        ]
        second = await self._attempt(
            schema,
            repair,
            tier,
            repair_prompt.ref,
            ctx,
            f"{repair_prompt.ref} after invalid output of {prompt.ref}: {route_reason}",
            max_output_tokens,
            prompt.temperature,  # the repair of a temperature-0 call is a temperature-0 call
        )
        parsed, errors = _parse(schema, second.content)
        await self._finish(second, "ok" if parsed is not None else "invalid_output")
        if parsed is not None:
            return parsed
        log.error(
            "structured output invalid after repair",
            extra={"schema": schema.__name__, "tier": tier.value, "errors": errors},
        )
        raise StructuredOutputInvalid(f"{schema.__name__}: invalid after one repair: {errors}")

    async def _finish(self, attempt: _Attempt, outcome: str) -> None:
        await self._ledger.finish(
            attempt.call_id,
            outcome="ok" if outcome == "ok" else "invalid_output",
            input_tokens=attempt.input_tokens,
            output_tokens=attempt.output_tokens,
            cost_usd=attempt.cost_usd,
            latency_ms=attempt.latency_ms,
            rate_limit_wait_ms=attempt.wait_ms,
        )

    async def _strong_tier_refusal(self, model_id: str, rpd: int, ctx: CallContext) -> str | None:
        """Why a strong call may not be made, or None when it may."""
        if self._strong_org is None:
            if self._allow_strong:
                return None
            return (
                f"refused: strong tier ({model_id}, {rpd}/day) is disabled; "
                "set llm_allow_strong_tier only for evaluation runs"
            )
        if ctx.org_id != self._strong_org:
            return (
                f"refused: strong tier ({model_id}, {rpd}/day) is enabled for one organisation "
                "only, and this call is from another"
            )
        if not await self._ledger.organisation_is_practice(ctx.org_id):
            return (
                f"refused: strong tier ({model_id}, {rpd}/day) is enabled for organisation "
                f"{self._strong_org} only while it is flagged is_practice, and it is not"
            )
        return None

    async def _attempt(
        self,
        schema: type[BaseModel],
        messages: list[dict[str, str]],
        tier: Tier,
        prompt: PromptRef,
        ctx: CallContext,
        route_reason: str,
        max_output_tokens: int,
        temperature: float | None = None,
    ) -> _Attempt:
        model = self._registry.model(tier)
        if tier is Tier.STRONG:
            refusal = await self._strong_tier_refusal(model.id, model.rate_limits.rpd, ctx)
            if refusal is not None:
                # Refused before admission and before any provider call, on every database.
                await self._ledger.refuse(ctx, tier, prompt, refusal)
                raise StrongTierDisabled(refusal)
        estimate = model.cost_usd(estimate_tokens(messages), max_output_tokens, self._today())
        waited_s = 0.0
        # An open breaker refuses the call before it is admitted: no row, no provider request.
        self._raise_if_open(ctx)

        async def admit() -> int:
            """Admission: may refuse (raises), or ask to wait for the model's RPM window."""
            nonlocal waited_s
            admission = await self._ledger.admit(ctx, tier, prompt, route_reason, estimate)
            while admission.call_id is None:
                if waited_s + admission.wait_s > self._max_wait_s:
                    reason = (
                        f"refused: pacing {model.id} to {model.rate_limits.rpm}/min would wait "
                        f"over {self._max_wait_s:.0f} s (Rules.md C10)"
                    )
                    await self._ledger.refuse(ctx, tier, prompt, reason)
                    raise LLMRateLimited(reason)
                await self._sleep(admission.wait_s)
                waited_s += admission.wait_s
                admission = await self._ledger.admit(ctx, tier, prompt, route_reason, estimate)
            return admission.call_id

        async def one_request(call_id: int) -> tuple[Any, int]:
            """The call for ONE admitted row. A 429 is waited out and the same request re-sent,
            within the caps."""
            nonlocal waited_s
            rate_limit_waits = 0
            while True:
                started = self._clock()
                try:
                    response = await self._completion(
                        model=model.litellm_model,
                        messages=messages,
                        # Never the raw Pydantic schema: the plain, allow-listed form
                        # (app/llm/schema.py). The reply is still validated against `schema`.
                        response_format=response_format(schema),
                        max_tokens=max_output_tokens,
                        timeout=self._timeout_s,
                        api_key=self._api_key,
                        # No hidden retries: LiteLLM's and the HTTP client's are both off.
                        num_retries=0,
                        max_retries=0,
                        # Only when the locked prompt sets one; otherwise the provider's default.
                        **({} if temperature is None else {"temperature": temperature}),
                    )
                    return response, int((self._clock() - started) * 1000)
                except _litellm.RateLimitError as e:
                    delay = retry_delay_s(e)
                    if delay is None:
                        delay = min(2.0**rate_limit_waits, 30.0) + random.uniform(0, 1)  # noqa: S311
                    if rate_limit_waits >= self._max_waits or waited_s + delay > self._max_wait_s:
                        await self._ledger.finish(
                            call_id,
                            outcome="rate_limited",
                            input_tokens=None,
                            output_tokens=None,
                            cost_usd=None,
                            latency_ms=None,
                            rate_limit_wait_ms=int(waited_s * 1000),
                        )
                        raise LLMRateLimited(
                            f"{model.id}: still rate-limited after {rate_limit_waits} waits "
                            f"({waited_s:.1f} s)"
                        ) from e
                    log.info(
                        "rate limited; waiting",
                        extra={"model": model.id, "wait_s": round(delay, 2), "call_id": call_id},
                    )
                    rate_limit_waits += 1
                    await self._sleep(delay)
                    waited_s += delay

        # 1. Admission, then 2. the call. A provider-unavailable answer (any 5xx) closes ITS row as
        # `provider_unavailable` and is retried on a NEW admitted row after an exponential backoff,
        # at most `unavailable_retries` times: every retry is a row of its own, so it counts as
        # SPEND (quota, caps, cost) and never as WORK (llm_attempts.py). Three consecutive such
        # answers in one run open the breaker and abort the run (2026-11-05 ruling).
        call_id = await admit()
        unavailable_attempts = 0
        while True:
            try:
                response, latency_ms = await one_request(call_id)
                break
            except LLMRateLimited:
                raise
            except BaseException as e:
                if isinstance(e, Exception) and is_provider_unavailable(e):
                    await asyncio.shield(
                        self._ledger.finish(
                            call_id,
                            outcome="provider_unavailable",
                            input_tokens=None,
                            output_tokens=None,
                            cost_usd=None,
                            latency_ms=None,
                            rate_limit_wait_ms=int(waited_s * 1000),
                        )
                    )
                    unavailable_attempts += 1
                    streak, total = self._note_unavailable(ctx)
                    log.warning(
                        "provider unavailable",
                        extra={
                            "model": model.id,
                            "call_id": call_id,
                            "attempt": unavailable_attempts,
                            "run_streak": streak,
                            "run_total": total,
                            "error": type(e).__name__,
                        },
                    )
                    if (reason := self._breaker_reason(ctx)) is not None:
                        raise ProviderCircuitOpen(
                            f"run {ctx.run_id}: {reason} (last: {type(e).__name__}); the run is "
                            "aborted as an infrastructure failure"
                        ) from e
                    if unavailable_attempts > self._unavailable_retries:
                        raise ProviderUnavailable(
                            f"{model.id}: provider unavailable on {unavailable_attempts} "
                            f"attempts: {type(e).__name__}: {e}"
                        ) from e
                    delay = self._unavailable_backoff_s * 2 ** (unavailable_attempts - 1)
                    await self._sleep(delay + random.uniform(0, delay / 4))  # noqa: S311
                    self._raise_if_open(ctx)  # another call may have opened it while we waited
                    call_id = await admit()
                    continue
                # Any other failure, cancellation included: complete the row, then propagate.
                await asyncio.shield(
                    self._ledger.finish(
                        call_id,
                        outcome="error",
                        input_tokens=None,
                        output_tokens=None,
                        cost_usd=None,
                        latency_ms=None,
                        rate_limit_wait_ms=int(waited_s * 1000),
                    )
                )
                if isinstance(e, Exception):
                    log.exception("LLM call failed", extra={"model": model.id, "call_id": call_id})
                    raise LLMCallFailed(f"{model.id}: {type(e).__name__}: {e}") from e
                raise
        self._note_ok(ctx)

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        content = _content_of(response)
        return _Attempt(
            call_id=call_id,
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=model.cost_usd(input_tokens, output_tokens, self._today()),
            latency_ms=latency_ms,
            wait_ms=int(waited_s * 1000),
        )


def _content_of(response: Any) -> str:
    """The completion's text, or "" — which then fails validation like any non-JSON reply."""
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError):
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content) if content is not None else ""
