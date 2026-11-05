"""Which run endings are infrastructure failures — defined in advance and narrowly
(docs/evaluation-plan.md §2, the attempt rule fixed 2026-10-17, amended 2026-11-05).

Exactly five kinds qualify, and nothing else (the fifth, `provider_unavailable`, is the amendment
of 2026-11-05, made before any evaluation data existed):

- `rate_limited` — `LLMRateLimited`: rate-limit waits exceeded their cap (Rules.md C10).
- `quota_exhausted` — `QuotaExhausted`: the model's daily quota is used up (Rules.md C9).
- `network_error` — a transport failure anywhere in the cause chain: LiteLLM/OpenAI
  `APIConnectionError` (its `Timeout` included), `httpx.TransportError`,
  `psycopg.OperationalError`, `ConnectionError`, `TimeoutError`.
- `provider_unavailable` — the provider answered a server-side 5xx (503 "high demand", 500, 502,
  504 ...) on every attempt the gateway was allowed (`ProviderUnavailable`), or three such answers
  in a row opened the run's breaker (`ProviderCircuitOpen`). A provider outage is not a property of
  the arm being measured. (A 429 is `rate_limited`; a transport failure is `network_error`.)
- `process_crash` — the attempt was cancelled under the worker (`asyncio.CancelledError`:
  shutdown, job timeout). A process that dies outright never reaches this code: such an attempt
  has no terminal status, and the Phase 10 harness classifies it `process_crash` when it finds the
  run unterminated with no live job. A job that reaches its own deadline is now ended `failed` by
  the worker, with `runs.failure` = `{kind: job_timeout, infrastructure_failure: process_crash,
  ...}` (migration 0046, 2026-11-04), so it no longer waits for that harness rule to find it.

Everything else — a budget stop (`partial`), a gate block, invalid model output, a provider's
refusal of a malformed request, a bug — is not an infrastructure failure: the attempt is a result.
Classification reads the exception's type and cause chain only; nobody decides it afterwards.
"""

import asyncio
from collections.abc import Iterable, Mapping
from typing import Any, Literal

import httpx
import psycopg

from app.errors import LLMRateLimited, ProviderCircuitOpen, ProviderUnavailable, QuotaExhausted
from app.llm._litellm import APIConnectionError
from app.llm.gateway import is_provider_unavailable

InfrastructureFailure = Literal[
    "rate_limited", "quota_exhausted", "network_error", "process_crash", "provider_unavailable"
]
INFRASTRUCTURE_FAILURES: tuple[InfrastructureFailure, ...] = (
    "rate_limited",
    "quota_exhausted",
    "network_error",
    "process_crash",
    "provider_unavailable",
)

_NETWORK: tuple[type[BaseException], ...] = (
    APIConnectionError,
    httpx.TransportError,
    psycopg.OperationalError,
    ConnectionError,
    TimeoutError,
)


# A run that would END `completed` while carrying a declared gap of one of these kinds is
# invalidated, not a result (owner ruling, 2026-11-05; docs/evaluation-plan.md Amendment 1): the
# gap is the provider's outage, not the arm's behaviour, and leaving it in would depress whichever
# arm the outage touched. Only `provider_unavailable` is ruled so far. Whether the same holds for
# a rate-limit or network gap is NOT ruled and is not assumed here.
INVALIDATING_GAP_KINDS: frozenset[str] = frozenset({"provider_unavailable"})


def invalidating_gaps(errors: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The agent errors whose KIND invalidates the run, decided from the kind alone: the
    classifier reads `infrastructure`, which `classify_infrastructure_failure` set from the
    exception when the agent failed. Nobody reads the copy and then decides."""
    return [e for e in errors if e.get("infrastructure") in INVALIDATING_GAP_KINDS]


def _chain(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def classify_infrastructure_failure(exc: BaseException) -> InfrastructureFailure | None:
    """The infrastructure-failure kind that ended an attempt, or None if the ending is a result."""
    if isinstance(exc, asyncio.CancelledError):
        return "process_crash"
    chain = _chain(exc)
    if any(isinstance(e, LLMRateLimited) for e in chain):
        return "rate_limited"
    if any(isinstance(e, QuotaExhausted) for e in chain):
        return "quota_exhausted"
    if any(isinstance(e, ProviderUnavailable | ProviderCircuitOpen) for e in chain) or any(
        is_provider_unavailable(e) for e in chain
    ):
        return "provider_unavailable"
    if any(isinstance(e, _NETWORK) for e in chain):
        return "network_error"
    return None
