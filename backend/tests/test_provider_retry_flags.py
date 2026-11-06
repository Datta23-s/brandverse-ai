"""Campaigns that contained a provider retry are flagged, and every figure can be read with and
without them (owner, 2026-11-06). The pure sensitivity arithmetic here; the query in
tests/db/test_provider_retry_flags.py."""

from uuid import uuid4

import pytest

from app.evaluation.provider_retries import sensitivity


def test_a_figure_is_read_with_and_without_the_flagged_campaigns() -> None:
    a, b, c, d = (uuid4() for _ in range(4))
    values = {a: 0.5, b: 0.7, c: 0.9, d: 0.1}
    s = sensitivity(values, [d])
    assert (s.n_all, s.n_without, s.n_flagged) == (4, 3, 1)
    assert s.mean_all == pytest.approx(0.55) and s.mean_without == pytest.approx(0.7)
    assert s.moved_by == pytest.approx(0.15)


def test_no_flagged_campaign_means_the_two_readings_agree() -> None:
    a, b = uuid4(), uuid4()
    s = sensitivity({a: 1.0, b: 3.0}, [])
    assert (s.mean_all, s.mean_without, s.moved_by, s.n_flagged) == (2.0, 2.0, 0.0, 0)


def test_a_flagged_run_with_no_value_is_ignored_and_empty_sets_are_none() -> None:
    a, ghost = uuid4(), uuid4()
    assert sensitivity({a: 2.0}, [ghost]).n_flagged == 0
    empty = sensitivity({}, [])
    assert (empty.mean_all, empty.mean_without, empty.moved_by) == (None, None, None)
    everything = sensitivity({a: 2.0}, [a])
    assert everything.mean_without is None and everything.moved_by is None
