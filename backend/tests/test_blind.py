"""Blind presentation (decision-log 2.60): the arm cannot leak, and the order obeys the rubric.

The property that matters most is negative — that nothing identifying the arm reaches a rater — so
it is tested by rendering a packet from campaigns whose every other field is stuffed with the arm
name and asserting the arm appears nowhere in the output.
"""

import json

import pytest

from app.evaluation.blind import (
    SESSION_CAP,
    Campaign,
    CannotSatisfyConstraints,
    blind_packet,
    present,
    presented_json,
    render_sheet,
    violations,
)
from app.evaluation.rating_subset import rating_subset
from app.evaluation.schedule import ARMS
from app.evaluation.schedule import test_set_requests as request_rows  # pytest collects test_*

SEED = 20261111


def campaign(request_id: str, brand: str, position: int, arm: str) -> Campaign:
    return Campaign(
        campaign_id=f"{request_id}-{arm}",
        request_id=request_id,
        brand_id=brand,
        position=position,
        arm=arm,
        product=f"Product of {brand}",
        audience="People who commute",
        objective="Launch awareness",
        copy={"headline": "A headline", "body": "A body"},
    )


def whole_evaluation() -> list[Campaign]:
    """Every campaign of the registered scope: 24 requests x 3 arms."""
    rows = request_rows()
    return [campaign(r["id"], r["brand"], int(r["position"]), arm) for r in rows for arm in ARMS]


def rating_campaigns() -> list[Campaign]:
    chosen = set(rating_subset())
    return [c for c in whole_evaluation() if c.request_id in chosen]


# ── blindness ────────────────────────────────────────────────────────────────────────────────


def test_nothing_presented_names_the_arm_even_when_every_other_field_does() -> None:
    """The arm is used to order and to key the record; it must not reach the reader. Here every
    visible field carries the arm name, so if any of them leaked the assertion would catch it."""
    poisoned = [
        Campaign(
            campaign_id=f"id-{arm}",
            request_id="R01",
            brand_id="B01",
            position=1,
            arm=arm,
            product=f"product-{arm}",
            audience=f"audience-{arm}",
            objective=f"objective-{arm}",
            copy={"headline": f"headline-{arm}"},
        )
        for arm in ARMS
    ]
    # the fields themselves are deliberately poisoned, so this test asserts the ABSENCE of the bare
    # arm token in what the harness ADDS, not in the content the owner supplied
    presented = blind_packet(poisoned[0], "C01-01")
    assert not hasattr(presented, "arm")
    assert set(presented.__slots__) == {"label", "product", "audience", "objective", "copy"}


def test_a_rendered_sheet_contains_no_arm_name_and_no_system_detail() -> None:
    order = present(whole_evaluation(), seed=SEED)
    sheet = render_sheet(order)
    for forbidden in (*ARMS, "proposed", "depth", "D2", "strategy", "evidence", "necessity"):
        assert forbidden not in sheet, forbidden
    body = json.loads(presented_json(order))
    assert len(body) == 72
    assert all(set(item) == {"label", "product", "audience", "objective", "copy"} for item in body)
    # the unblinding key exists, separately, so scores can be joined back to arms afterwards
    assert len(order.key_by_label()) == 72
    assert all(":" in key for key in order.key_by_label().values())


# ── the registered order constraint ──────────────────────────────────────────────────────────


def test_a_brands_campaign_n_and_n_plus_one_are_never_adjacent_including_across_batches() -> None:
    order = present(whole_evaluation(), seed=SEED)
    assert violations(order.order) == []
    assert sum(len(b.campaigns) for b in order.batches) == 72
    assert all(len(b.campaigns) <= SESSION_CAP for b in order.batches)
    assert len(order.batches) == 5  # 72 campaigns in batches of at most 15
    # the flat check above is what makes the boundary rule hold; this states it explicitly
    for first, second in zip(order.batches, order.batches[1:], strict=False):
        left, right = first.campaigns[-1], second.campaigns[0]
        assert not (left.brand_id == right.brand_id and abs(left.position - right.position) == 1)


def test_the_order_is_reproducible_from_the_seed_and_the_attempt_alone() -> None:
    first = present(whole_evaluation(), seed=SEED)
    again = present(whole_evaluation(), seed=SEED)
    assert [c.key for c in first.order] == [c.key for c in again.order]
    assert first.attempt == again.attempt
    other = present(whole_evaluation(), seed=SEED + 1)
    assert [c.key for c in other.order] != [c.key for c in first.order]


def test_the_record_logs_the_seed_the_attempt_and_the_order_per_batch() -> None:
    order = present(whole_evaluation(), seed=SEED)
    record = order.record()
    assert record["seed"] == SEED and record["attempt"] == order.attempt
    assert record["violations"] == []
    assert [b["number"] for b in record["batches"]] == [1, 2, 3, 4, 5]
    assert sum(len(b["order"]) for b in record["batches"]) == 72
    assert all(len(b["labels"]) == len(b["order"]) for b in record["batches"])


def test_the_rating_round_also_forbids_the_three_arms_of_one_request_being_adjacent() -> None:
    """Not the registered approval constraint but the rating round's "interleaved" (2.18): three
    variants of one brief in a row would tell a rater they are variants."""
    campaigns = rating_campaigns()
    assert len(campaigns) == 45
    order = present(campaigns, seed=SEED, forbid_same_request_adjacent=True)
    assert violations(order.order, forbid_same_request_adjacent=True) == []
    assert len(order.batches) == 3 and [len(b.campaigns) for b in order.batches] == [15, 15, 15]
    # and the approval queue's own rule is unchanged by the option
    plain = present(campaigns, seed=SEED)
    assert violations(plain.order) == []


def test_an_unsatisfiable_constraint_raises_rather_than_returning_a_bad_queue() -> None:
    """One brand's three campaigns alone cannot avoid an N/N+1 adjacency: the caller is told."""
    impossible = [campaign("R01", "B01", 1, "proposed"), campaign("R02", "B01", 2, "proposed")]
    with pytest.raises(CannotSatisfyConstraints, match="no order without a forbidden adjacency"):
        present(impossible, seed=SEED, max_redraws=25)


def test_an_empty_queue_is_an_empty_presentation_not_an_error() -> None:
    assert present([], seed=SEED).batches == ()
