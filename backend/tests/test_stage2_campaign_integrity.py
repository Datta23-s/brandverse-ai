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


def test_the_budget_floor_hands_the_decision_back_to_the_owner() -> None:
    """Owner, 5 November 2026: the ceiling holds at 250; under 30 calls left, no attempt starts."""
    from scripts.phase7_stage2 import BUDGET_FLOOR, budget_floor_refusal

    assert BUDGET_FLOOR == 30
    assert budget_floor_refusal(91) is None and budget_floor_refusal(30) is None
    refusal = budget_floor_refusal(29)
    assert refusal is not None and "come back to the owner" in refusal
    assert "not raised quietly" in refusal


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
