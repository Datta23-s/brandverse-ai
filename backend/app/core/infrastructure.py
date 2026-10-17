"""Which run endings are infrastructure failures — defined in advance and narrowly
(docs/evaluation-plan.md §2, the attempt rule fixed 2026-10-17).

Exactly four kinds qualify, and nothing else:

- `rate_limited` — `LLMRateLimited`: rate-limit waits exceeded their cap (Rules.md C10).
- `quota_exhausted` — `QuotaExhausted`: the model's daily quota is used up (Rules.md C9).
- `network_error` — a transport failure anywhere in the cause chain: LiteLLM/OpenAI
  `APIConnectionError` (its `Timeout` included), `httpx.TransportError`,
  `psycopg.OperationalError`, `ConnectionError`, `TimeoutError`.
- `process_crash` — the attempt was cancelled under the worker (`asyncio.CancelledError`:
  shutdown, job timeout). A process that dies outright never reaches this code: such an attempt
  has no terminal status, and the Phase 10 harness classifies it `process_crash` when it finds the
  run unterminated with no live job.

Everything else — a budget stop (`partial`), a gate block, invalid model output, a provider's
refusal of a malformed request, a bug — is not an infrastructure failure: the attempt is a result.
Classification reads the exception's type and cause chain only; nobody decides it afterwards.
"""

import asyncio
from typing import Literal

import httpx
import psycopg

from app.errors import LLMRateLimited, QuotaExhausted
from app.llm._litellm import APIConnectionError

InfrastructureFailure = Literal["rate_limited", "quota_exhausted", "network_error", "process_crash"]
INFRASTRUCTURE_FAILURES: tuple[InfrastructureFailure, ...] = (
    "rate_limited",
    "quota_exhausted",
    "network_error",
    "process_crash",
)

_NETWORK: tuple[type[BaseException], ...] = (
    APIConnectionError,
    httpx.TransportError,
    psycopg.OperationalError,
    ConnectionError,
    TimeoutError,
)


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
    if any(isinstance(e, _NETWORK) for e in chain):
        return "network_error"
    return None
