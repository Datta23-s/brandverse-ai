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

**The sequence rule (amendment of 2026-11-06, before any evaluation data existed).** A request is a
sequence of attempts (the call, its provider-unavailable retries, at most one repair). If ANY
attempt in a sequence that ends in failure was an infrastructure failure, the whole sequence is
classified as that failure. *Reasoning:* a retry issued right after a provider failure may itself
be degraded; over-invalidating costs re-runs, under-invalidating biases a result, so the safe
direction is discarding data rather than attributing a provider's bad minute to an arm. It governs
sequences that END in failure; a sequence that ends in a valid output is a result as before.

Everything else — a budget stop (`partial`), a gate block, invalid model output, a provider's
refusal of a malformed request, a bug — is not an infrastructure failure: the attempt is a result.
Classification reads the exception's type and cause chain only; nobody decides it afterwards.
"""

import asyncio
from collections.abc import Iterable, Mapping
from typing import Any, Literal

import httpx
import psycopg

from app.errors import (
    LLMRateLimited,
    ProviderCircuitOpen,
    ProviderUnavailable,
    QuotaExhausted,
    StructuredOutputInvalid,
)
from app.llm._litellm import APIConnectionError
from app.llm.gateway import SEQUENCE_ATTRIBUTE, is_provider_unavailable

# The four kinds fixed in advance on 2026-10-17, plus `provider_unavailable` (Amendment 1,
# 5 November) and `schema_exhausted` (AMENDMENT 4, 11 November). Each keeps its own name in every
# report, so "which kind" is never lost by being grouped here.
#
# AMENDMENT 4 (owner, 2026-11-11), registered before the remaining runs and independent of any
# outcome: **a structured-output failure whose one repair is also exhausted invalidates the run**
# and it is re-run under the existing cap. The owner's justification: schema compliance is a
# MODEL-RELIABILITY property, not a behavioural difference between the arms. For A3 that is
# provable — A3 receives no arm-specific input since injection into it was dropped on 6 November, so
# its prompt is identical across arms and any difference in its outcomes is noise.
# `StructuredOutputInvalid` is raised only after the one repair has failed too
# (`LLMGateway.structured`), so the exception IS the repair-exhausted condition.
# **A6 is the case this must not hide:** A6 does receive injected memory, so a longer prompt could
# genuinely cause more truncation in the Proposed arm, which would be a real effect and not noise.
# Every occurrence is therefore recorded BY AGENT AND BY ARM and reported
# (`app/evaluation/metrics.py`, `schema_failure_lines`): if they cluster in one arm or one agent,
# that is a finding.
InfrastructureFailure = Literal[
    "rate_limited",
    "quota_exhausted",
    "network_error",
    "process_crash",
    "provider_unavailable",
    "schema_exhausted",
]
INFRASTRUCTURE_FAILURES: tuple[InfrastructureFailure, ...] = (
    "rate_limited",
    "quota_exhausted",
    "network_error",
    "process_crash",
    "provider_unavailable",
    "schema_exhausted",
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
# gap is infrastructure, not the arm's behaviour, and leaving it in would depress whichever arm it
# touched.
#
# AMENDMENT 3 (owner, 2026-11-07, before any evaluation data exists): EVERY registered
# infrastructure kind invalidates, not `provider_unavailable` alone. The owner's reasoning, recorded
# with the ruling: the infrastructure list exists precisely to separate what is not the arm's
# behaviour from what is, and a local DNS fault is no more a property of an arm than a provider
# outage. The re-run cap is unchanged (2 re-runs, then the owner: `app/evaluation/reruns.py`).
# `rate_limited` invalidates AND is reported as a SCHEDULING DEFECT every time it occurs
# (`app/evaluation/metrics.py`): Baseline B makes more calls than the Proposed arm, so a binding
# rate limit binds unevenly across arms. The scheduler must pace so it never happens.
INVALIDATING_GAP_KINDS: frozenset[str] = frozenset(INFRASTRUCTURE_FAILURES)


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
    # The SEQUENCE rule (owner, 2026-11-06; evaluation-plan.md Amendment 2): a failure that ends a
    # request whose earlier attempt was an infrastructure failure (an invalid output after a 503
    # retry, say) is classified by that earlier failure. See LLMGateway.structured.
    for e in chain:
        marked = getattr(e, SEQUENCE_ATTRIBUTE, None)
        for kind in INFRASTRUCTURE_FAILURES:
            if marked == kind:
                return kind
    # AMENDMENT 4, LAST: a repair-exhausted structured-output failure. Last so that a sequence which
    # was ALSO an infrastructure failure keeps that earlier, more specific kind (Amendment 2's
    # sequence rule above), and an outage is never relabelled as a schema problem.
    if any(isinstance(e, StructuredOutputInvalid) for e in chain):
        return "schema_exhausted"
    return None
