"""A live campaign that did not produce what the measurement needs stops the interim.

Found 5 November 2026: the provider answered 503 ("high demand") to every agent call. The runs still
ended `completed`, with declared gaps, no copy and no personas; no memory was written; every later
campaign recalled nothing; and the interim reported `failed` on empty inputs after 30 minutes and 53
calls, a verdict about the outage and not about R. The guard stops after the first bad campaign.
"""

import pytest

from scripts.phase7_stage2 import InvalidCampaign, campaign_problem

ALL_AGENTS_FAILED = [
    {"step": 7, "agent": "A1", "error": "LLMCallFailed"},
    {"step": 7, "agent": "A2", "error": "LLMCallFailed"},
    {"step": 7, "agent": "A3", "error": "LLMCallFailed"},
    {"step": 10, "agent": "A6", "error": "LLMCallFailed"},
]


def test_tonights_outage_is_caught_on_its_first_campaign() -> None:
    problem = campaign_problem("completed", ALL_AGENTS_FAILED, 0, 0)
    assert problem is not None
    assert "agents failed (A1, A2, A3, A6; LLMCallFailed)" in problem


def test_one_failed_agent_is_enough() -> None:
    gaps = [{"step": 7, "agent": "A3", "error": "LLMCallFailed"}]
    problem = campaign_problem("completed", gaps, 4, 9)
    assert problem is not None and "A3" in problem


def test_a_campaign_that_added_no_memory_is_not_an_observation() -> None:
    problem = campaign_problem("completed", [], 9, 9)
    assert problem is not None and "added no memory (9 items before, 9 after)" in problem


@pytest.mark.parametrize("status", ["failed", "partial", "blocked", "queued", None])
def test_a_run_that_did_not_complete_cannot_be_scored(status: str | None) -> None:
    problem = campaign_problem(status, [], 0, 9)
    assert problem is not None and "not 'completed'" in problem


def test_a_clean_campaign_that_wrote_memory_passes() -> None:
    assert campaign_problem("completed", [], 0, 9) is None
    assert campaign_problem("completed", [], 9, 12) is None


def test_an_attempt_cannot_exceed_its_stated_call_cap() -> None:
    """Owner, 5 November 2026 (evening): "no attempt can exceed a stated number", because the
    gateway's bounds limit a run, not how many campaigns an attempt tries. A campaign starts only
    if its own worst case still fits, so the cap is a bound and not an estimate."""
    from scripts.phase7_stage2 import (
        ATTEMPT_CALL_CAP,
        PER_RUN_CALL_CAP,
        attempt_cap_refusal,
    )

    assert (ATTEMPT_CALL_CAP, PER_RUN_CALL_CAP) == (125, 15)
    # The per-run cap must allow a campaign's registered worst case: 7 base + 3 per revision round
    # with max_revision_rounds = 2 is 13 (measured 2026-11-06), plus a repair call.
    assert PER_RUN_CALL_CAP >= 13
    # A typical interim is 70 to 80 calls, so it never reaches the threshold.
    assert attempt_cap_refusal(80) is None
    assert attempt_cap_refusal(110) is None  # the last campaign that may start
    refusal = attempt_cap_refusal(111)  # 111 + 15 would exceed 125
    assert refusal is not None and "attempt cap is 125" in refusal
    # The bound holds however the calls were spent, and the worst case is the cap itself.
    assert all(
        attempt_cap_refusal(spent) is not None
        for spent in range(ATTEMPT_CALL_CAP - PER_RUN_CALL_CAP + 1, ATTEMPT_CALL_CAP + 1)
    )


def test_reaching_the_attempt_cap_writes_no_report_and_is_retryable() -> None:
    from scripts.phase7_stage2 import AttemptCapReached, InvalidCampaign

    assert issubclass(AttemptCapReached, InvalidCampaign)  # exit 3: no report, retry


def test_the_owner_is_told_at_400_instead_of_the_ceiling_being_raised_again() -> None:
    """Owner, 5 November 2026 (evening): ceiling 500, report at 400. Supersedes the floor of 30."""
    from app.llm.dev_budget import load_dev_budget

    budget = load_dev_budget()
    assert (budget.ceiling_calls, budget.report_to_owner_at_calls) == (500, 400)
    # `budget_report_refusal` reads the live database, so the pure decision is asserted here.
    assert budget.report_due("brandverse_stage2", 298) is None  # total 399
    assert budget.report_due("brandverse_stage2", 299) is not None  # total 400


def test_the_interim_stops_on_it_and_writes_no_report() -> None:
    """The guard is wired in: the error is raised from the campaign loop, caught in run_live, and
    returns a distinct exit status before any report is written."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "scripts" / "phase7_stage2.py").read_text(
        encoding="utf-8"
    )
    assert "raise InvalidCampaign(" in src
    assert src.index("except InvalidCampaign as e:") < src.index("INTERIM_REPORT if stage")
    assert "INVALID RUN, NO REPORT WRITTEN" in src
    assert issubclass(InvalidCampaign, RuntimeError)
