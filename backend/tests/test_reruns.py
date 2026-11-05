"""The re-run cap and the invalidation record (owner ruling, 5 November 2026; Amendment 1).

At most 2 re-runs per campaign; a third failure comes to the owner and is not re-run. Every
invalidation is recorded with the outage window it overlapped and the run that replaced it.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.infrastructure import INVALIDATING_GAP_KINDS, invalidating_gaps
from app.evaluation.outages import OutageWindow
from app.evaluation.reruns import MAX_RERUNS, Attempt, invalidations, next_action, primary

T0 = datetime(2026, 11, 5, 12, 54, tzinfo=UTC)
FAILED = "provider_unavailable"


def test_the_cap_is_two_reruns() -> None:
    assert MAX_RERUNS == 2


def test_a_clean_first_attempt_is_accepted() -> None:
    assert next_action([Attempt(1, None)]) == "accept"


def test_a_failed_first_attempt_is_rerun() -> None:
    assert next_action([Attempt(1, FAILED)]) == "rerun"


def test_a_second_failure_is_still_rerun() -> None:
    assert next_action([Attempt(1, FAILED), Attempt(2, FAILED)]) == "rerun"


def test_a_third_failure_comes_to_the_owner_and_is_not_rerun() -> None:
    attempts = [Attempt(1, FAILED), Attempt(2, FAILED), Attempt(3, FAILED)]
    assert next_action(attempts) == "escalate"


def test_a_rerun_that_succeeds_is_accepted_and_the_primary_is_the_first_clean_one() -> None:
    attempts = [Attempt(1, FAILED), Attempt(2, None), Attempt(3, None)]
    assert next_action(attempts) == "accept"
    chosen = primary(attempts)
    assert chosen is not None and chosen.number == 2  # the first attempt WITHOUT the failure


def test_an_unfinished_campaign_has_no_primary() -> None:
    assert primary([Attempt(1, FAILED), Attempt(2, "network_error")]) is None


def test_no_attempt_is_an_error_not_a_default() -> None:
    with pytest.raises(ValueError):
        next_action([])


def test_only_provider_unavailable_is_an_invalidating_gap_kind_so_far() -> None:
    assert frozenset({"provider_unavailable"}) == INVALIDATING_GAP_KINDS
    errors = [
        {"agent": "A1", "step": 7, "infrastructure": "provider_unavailable"},
        {"agent": "A2", "step": 7, "infrastructure": "rate_limited"},  # not ruled
        {"agent": "A3", "step": 7, "infrastructure": None},
    ]
    assert [e["agent"] for e in invalidating_gaps(errors)] == ["A1"]


def test_every_invalidation_is_recorded_with_its_window_and_its_rerun() -> None:
    window = OutageWindow(T0, T0 + timedelta(minutes=40), 12)
    invalid, replacement, clean = uuid4(), uuid4(), uuid4()
    failure = {
        "kind": "infrastructure_gap",
        "invalidated": True,
        "infrastructure_failure": FAILED,
        "gaps": [{"step": 7, "agent": "A1", "error": "ProviderUnavailable"}],
    }
    found = invalidations(
        [
            (invalid, T0 + timedelta(minutes=5), T0 + timedelta(minutes=9), failure, replacement),
            (clean, T0 + timedelta(minutes=60), T0 + timedelta(minutes=70), None, None),
        ],
        [window],
    )
    assert len(found) == 1  # only the invalidated run is recorded
    record = found[0]
    assert record.run_id == invalid and record.rerun_run_id == replacement
    assert record.windows == (window,) and record.window_found
    assert record.infrastructure_failure == FAILED
    assert record.gaps[0]["agent"] == "A1"


def test_an_invalidation_with_no_recorded_window_is_flagged_not_passed() -> None:
    failure = {"kind": "infrastructure_gap", "invalidated": True,
               "infrastructure_failure": FAILED, "gaps": []}  # fmt: skip
    (record,) = invalidations([(uuid4(), T0, T0 + timedelta(minutes=1), failure, None)], [])
    assert not record.window_found  # the outage record is incomplete: look at it
    assert record.rerun_run_id is None  # and it has not been re-run yet
