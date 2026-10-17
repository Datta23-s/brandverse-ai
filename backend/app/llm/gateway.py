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

from pydantic import BaseModel, ValidationError

from app.errors import (
    LLMCallFailed,
    LLMRateLimited,
    StrongTierDisabled,
    StructuredOutputInvalid,
)
from app.llm import _litellm
from app.llm.ledger import CallContext, CallLedger
from app.llm.prompts import PromptLibrary, PromptRef, RenderedPrompt
from app.llm.registry import ModelRegistry, Tier

log = logging.getLogger(__name__)

Completion = Callable[..., Awaitable[Any]]

# The repair template, pinned by version like any other prompt (config/prompts/).
REPAIR_PROMPT = ("structured_repair", "v1")
_RETRY_DELAY = re.compile(r'"?retryDelay"?\s*[:=]\s*"?(\d+(?:\.\d+)?)s')


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
        completion: Completion = _litellm.acompletion,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        self._registry = registry
        self._ledger = ledger
        self._repair = prompts.get(*REPAIR_PROMPT)  # fails at start-up, not at the first repair
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._max_waits = max_rate_limit_waits
        self._max_wait_s = max_rate_limit_wait_s
        self._allow_strong = allow_strong_tier
        self._completion = completion
        self._sleep = sleep
        self._clock = clock
        self._today = today

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
            schema, messages, tier, prompt.ref, ctx, route_reason, max_output_tokens
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

    async def _attempt(
        self,
        schema: type[BaseModel],
        messages: list[dict[str, str]],
        tier: Tier,
        prompt: PromptRef,
        ctx: CallContext,
        route_reason: str,
        max_output_tokens: int,
    ) -> _Attempt:
        model = self._registry.model(tier)
        if tier is Tier.STRONG and not self._allow_strong:
            # Refused before admission and before any provider call, on every database.
            reason = (
                f"refused: strong tier ({model.id}, {model.rate_limits.rpd}/day) is disabled; "
                "set llm_allow_strong_tier only for evaluation runs"
            )
            await self._ledger.refuse(ctx, tier, prompt, reason)
            raise StrongTierDisabled(reason)
        estimate = model.cost_usd(estimate_tokens(messages), max_output_tokens, self._today())
        waited_s = 0.0

        # 1. Admission: may refuse (raises), or ask to wait for the model's RPM window.
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
        call_id = admission.call_id

        # 2. The call. A 429 is waited out and the same request re-sent, within the caps.
        rate_limit_waits = 0
        try:
            while True:
                started = self._clock()
                try:
                    response = await self._completion(
                        model=model.litellm_model,
                        messages=messages,
                        response_format=schema,
                        max_tokens=max_output_tokens,
                        timeout=self._timeout_s,
                        api_key=self._api_key,
                        # No hidden retries: LiteLLM's and the HTTP client's are both off.
                        num_retries=0,
                        max_retries=0,
                    )
                    latency_ms = int((self._clock() - started) * 1000)
                    break
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
        except LLMRateLimited:
            raise
        except BaseException as e:
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
