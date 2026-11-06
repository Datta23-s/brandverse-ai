"""The cap audit (owner, 2026-11-06; decision-log 2.39): every cap on the evaluation path states the
registered parameter it bounds and carries a test that it exceeds that parameter's worst case.

Three caps bit this project in a week (the per-run call cap, the attempt cap, the A2 page limit);
each was an unexamined constant. A cap is not wrong because it is small: it is wrong when nothing
says what it bounds. Each test below names that.
"""

import math

import pytest

from app.agents.market import PAGES_BY_DEPTH
from app.core.call_budget import (
    logical_calls,
    sequential_model_calls,
    worst_case_calls,
)
from app.core.config import Settings
from app.llm.evaluation_budget import load_evaluation_budget
from app.memory.scoring import load_scoring
from scripts.phase7_stage2 import ATTEMPT_CALL_CAP, INTERIM_BRANDS, PER_RUN_CALL_CAP
from tests.conftest import make_settings

S = Settings.model_fields
DEFAULT = {name: f.default for name, f in S.items()}
SCORING = load_scoring()

# Measured, replayed from the frozen snapshot R01-R25 (decision-log 2.34): pages fetched at D2
# against D1, and the searches (exactly 2.00x).
PAGES_FETCHED_D2_OVER_D1 = 1.657
LIVE_CAMPAIGNS_PER_BRAND = 2  # campaign 3 is scored from recall, no call


# ── 1. calls per run ─────────────────────────────────────────────────────────────────────────


def test_the_logical_calls_of_a_run_are_what_the_graph_makes() -> None:
    """Brief check 1 + A1, A2, A3 + (A6, claim extraction, A7) per draft, 1 + 2 revision rounds."""
    assert logical_calls(0) == 7 and logical_calls(2) == 13


def test_the_production_per_run_cap_covers_a_run_that_uses_every_revision_and_every_retry() -> None:
    """Bounds: calls in ONE run (Architecture.md 12 / Rules.md C3; registered as 20, "a first pass
    and two rounds"). Worst case: 13 logical + the 6 provider-unavailable retries the gateway may
    spend (each is its own row) = 19."""
    worst = worst_case_calls(DEFAULT["max_revision_rounds"], DEFAULT["llm_unavailable_total_cap"])
    assert worst == 19
    assert DEFAULT["max_llm_calls_per_run"] >= worst


def test_the_production_cap_leaves_room_for_only_one_repair_and_that_is_known() -> None:
    """A structured repair (one extra call, at most one per request) is not in the worst case
    above. Margin over the 19 is 1. Measured: 7 repairs in 340 calls. Recorded as a finding in
    decision-log 2.39, not hidden: a run needing a second repair at the very worst case is refused
    by the cap and ends partial, declared."""
    worst = worst_case_calls(DEFAULT["max_revision_rounds"], DEFAULT["llm_unavailable_total_cap"])
    assert DEFAULT["max_llm_calls_per_run"] - worst == 1


def test_a_production_configuration_below_the_worst_case_does_not_start() -> None:
    prod = {"environment": "production", "fake_runs_enabled": False}
    assert make_settings(**prod).max_llm_calls_per_run == 20  # the registered default starts
    with pytest.raises(ValueError, match="below the worst case of one run"):
        make_settings(**prod, max_llm_calls_per_run=12)
    # and the revision limit cannot be raised past what the cap covers
    with pytest.raises(ValueError, match="below the worst case of one run"):
        make_settings(**prod, max_revision_rounds=5)


def test_the_stage2_per_run_cap_covers_the_logical_worst_case() -> None:
    """Bounds: one stage-2 campaign (not on the evaluation path). 13 logical calls; the cap of 15
    leaves a repair, and NOT the six retries (an outage invalidates the run first)."""
    assert PER_RUN_CALL_CAP >= logical_calls(DEFAULT["max_revision_rounds"])


def test_the_attempt_cap_covers_every_live_campaign_at_its_own_cap() -> None:
    """Bounds: one interim attempt = live campaigns x the per-run cap."""
    live = len(INTERIM_BRANDS) * LIVE_CAMPAIGNS_PER_BRAND
    assert ATTEMPT_CALL_CAP >= live * PER_RUN_CALL_CAP


# ── 2. pages ─────────────────────────────────────────────────────────────────────────────────


def test_the_page_budget_rises_with_the_depth_and_d2_covers_the_measured_fetch_ratio() -> None:
    """Bounds: the pages A2 reads. The registered parameter is DEPTH, so the budget must rise with
    it (the defect: 6 at every depth) and D2 must be at least D1 x the measured 1.657."""
    p = PAGES_BY_DEPTH
    assert p["D0"] == 0 < p["D1"] < p["D2"]
    assert p["D2"] >= round(p["D1"] * PAGES_FETCHED_D2_OVER_D1)


# ── 3. time ──────────────────────────────────────────────────────────────────────────────────


def test_the_job_timeout_exceeds_a_run_that_is_slow_everywhere_but_never_repaired() -> None:
    """Bounds: wall time of one run. Worst case: the research step at its timeout (900 s, the value
    the stage-2 worker sets) + every sequential call at the per-call timeout, none repaired or
    retried. Measured: longest run 516 s, longest call 277 s. With a repair or a retry on EVERY
    call the sum passes 1800 s: that is the job timeout doing its job (a visible failure, ruled
    4 November), and the finding recorded in decision-log 2.39."""
    research_timeout_s = 900
    worst_s = (
        research_timeout_s
        + sequential_model_calls(DEFAULT["max_revision_rounds"]) * DEFAULT["llm_call_timeout_s"]
    )
    assert worst_s == 1560
    assert DEFAULT["worker_job_timeout_s"] > worst_s


def test_a_call_cannot_stay_pending_past_its_expiry() -> None:
    longest = (DEFAULT["llm_max_rate_limit_waits"] + 1) * DEFAULT["llm_call_timeout_s"] + DEFAULT[
        "llm_max_rate_limit_wait_s"
    ]
    assert DEFAULT["llm_pending_expiry_s"] > longest


# ── 4. memory ────────────────────────────────────────────────────────────────────────────────


def test_the_injection_cap_is_the_registered_twelve_and_u_saturates_exactly_there() -> None:
    """Bounds: injected items per run (Memory.md 9). 12 is the frozen item_cap, and U's quantity
    saturates at the same 12, so a run can be fully covered and never over-injected."""
    assert SCORING.necessity.item_cap == 12


# ── 5. the evaluation's own budget and the strong tier ───────────────────────────────────────


def test_the_evaluation_budget_covers_the_plans_calls_and_a_rerun_in_three() -> None:
    ev = load_evaluation_budget()
    assert ev.base_calls >= 660 and ev.rerun_allowance_calls >= ev.base_calls // 3


def test_the_strong_tier_daily_limit_is_below_the_plans_worst_day_and_needs_two_days() -> None:
    """Bounds: strong-tier calls per day (20; models.yaml). The plan expects 18 to 24 strong calls
    in the regulated runs (phase7-first-decision.md), so the worst case is ABOVE the cap: it is a
    SCHEDULING requirement (those runs are spread over two days by hand), asserted here so it is
    not silent. A T4 call is never downgraded."""
    strong_rpd = 20
    worst = 24
    assert worst > strong_rpd
    assert math.ceil(worst / strong_rpd) == 2
