"""The sequence rule (owner, 2026-11-06; evaluation-plan.md Amendment 2).

If ANY attempt in a request's sequence (the call, its provider-unavailable retries, at most one
repair) was an infrastructure failure, a sequence that ENDS in failure is classified as it.
Found by the confirmation run: A3's request was a 503, then invalid output, then an invalid repair,
and nothing said whether that was the provider's or the arm's. Tests drive the real gateway with a
scripted provider and the real classifier and re-run rule.
"""

from typing import Any

import pytest

from app.core.infrastructure import classify_infrastructure_failure, invalidating_gaps
from app.errors import LLMCallFailed, StructuredOutputInvalid
from app.evaluation.reruns import MAX_RERUNS, Attempt, next_action
from tests.llm_fakes import FakeCompletion, reply
from tests.test_provider_unavailable import (
    OK,
    ask,
    gateway,
    new_ctx,
    outcomes,
    unavailable,
)

pytestmark = pytest.mark.anyio

BAD = "this is not json"


async def failure_of(*script: Any) -> BaseException:
    gw, ledger, _ = gateway(FakeCompletion(*script))
    with pytest.raises(BaseException) as raised:  # noqa: PT011 - the type is what is asserted
        await ask(gw, new_ctx())
    return raised.value


async def test_a_503_then_invalid_output_then_an_invalid_repair_is_infrastructure() -> None:
    """The exact A3 sequence of 6 November: provider_unavailable, invalid, invalid repair."""
    e = await failure_of(unavailable(), reply(BAD), reply(BAD))
    assert isinstance(e, StructuredOutputInvalid)  # the agent-level error is unchanged
    assert classify_infrastructure_failure(e) == "provider_unavailable"


async def test_a_503_before_the_repair_counts_too() -> None:
    e = await failure_of(reply(BAD), unavailable(), reply(BAD))
    assert isinstance(e, StructuredOutputInvalid)
    assert classify_infrastructure_failure(e) == "provider_unavailable"


async def test_the_gap_an_agent_records_invalidates_the_run() -> None:
    e = await failure_of(unavailable(), reply(BAD), reply(BAD))
    gap = {
        "agent": "A3",
        "error": "StructuredOutputInvalid",
        "infrastructure": classify_infrastructure_failure(e),
    }
    assert invalidating_gaps([gap]) == [gap]


async def test_invalid_output_with_no_infrastructure_failure_is_still_a_result() -> None:
    """The rule must not swallow the arm's own failures: no earlier infrastructure attempt, no
    reclassification."""
    e = await failure_of(reply(BAD), reply(BAD))
    assert isinstance(e, StructuredOutputInvalid)
    assert classify_infrastructure_failure(e) is None
    assert invalidating_gaps([{"agent": "A3", "infrastructure": None}]) == []


async def test_a_sequence_that_ends_in_a_valid_output_is_a_result_not_a_failure() -> None:
    """The rule governs sequences that end in failure. A 503 retried into a valid answer is the
    existing 2026-11-05 behaviour (a retry, one extra row), and is not discarded here."""
    gw, ledger, _ = gateway(FakeCompletion(unavailable(), reply(OK)))
    assert (await ask(gw, new_ctx())).supported is True
    assert outcomes(ledger) == ["provider_unavailable", "ok"]


async def test_another_failure_after_a_503_is_classified_by_the_503() -> None:
    """Not only invalid output: any failure that ends a sequence which began with a 503."""
    e = await failure_of(unavailable(), RuntimeError("something else broke"))
    assert isinstance(e, LLMCallFailed)
    assert classify_infrastructure_failure(e) == "provider_unavailable"


async def test_the_first_infrastructure_kind_in_the_sequence_wins_and_a_direct_kind_is_kept() -> (
    None
):
    e = await failure_of(unavailable(), unavailable(), unavailable())  # retries exhausted
    assert classify_infrastructure_failure(e) == "provider_unavailable"  # direct, unchanged


def test_it_interacts_with_the_two_rerun_cap_as_any_infrastructure_failure_does() -> None:
    """A campaign whose sequence is classified infrastructure is re-run, at most MAX_RERUNS (2)
    times, and the third failure comes to the owner. The sequence rule changes WHAT is classified
    infrastructure, not how many re-runs there are."""
    infra = "provider_unavailable"
    assert MAX_RERUNS == 2
    assert next_action([Attempt(1, infra)]) == "rerun"
    assert next_action([Attempt(1, infra), Attempt(2, infra)]) == "rerun"
    assert next_action([Attempt(1, infra), Attempt(2, infra), Attempt(3, infra)]) == "escalate"
    # a re-run that completes cleanly is accepted, and the first attempt stays the record
    assert next_action([Attempt(1, infra), Attempt(2, None)]) == "accept"
