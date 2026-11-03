"""The development call ceiling (owner, 2026-11-01): the decision and the values the owner ruled."""

from datetime import date

import pytest

from app.llm.dev_budget import DevBudget, load_dev_budget


def budget(confirmed: date | None = date(2026, 11, 2)) -> DevBudget:
    return DevBudget(
        ceiling_calls=250,
        total_at_ruling=101,
        made_at_ruling={"brandverse_chunk_f": 7},
        billing_unlinked_confirmed_on=confirmed,
    )


def test_the_total_is_the_owners_figure_plus_this_databases_growth() -> None:
    b = budget()
    assert b.total("brandverse_chunk_f", 7) == 101
    assert b.total("brandverse_chunk_f", 20) == 114


def test_without_the_billing_confirmation_every_call_is_refused_whatever_else_holds() -> None:
    kind, reason = budget(None).refusal("brandverse_chunk_f", 7) or ("", "")
    assert kind == "billing_not_confirmed"
    assert "billing account" in reason
    # even a database that is not listed, and a spent ceiling, report the billing condition first
    assert (budget(None).refusal("elsewhere", 10_000) or ("", ""))[0] == "billing_not_confirmed"


def test_an_unlisted_database_is_refused() -> None:
    assert (budget().refusal("brandverse_new", 0) or ("", ""))[0] == "database_unregistered"


def test_the_250th_call_is_the_last_and_the_251st_is_refused() -> None:
    b = budget()
    assert b.refusal("brandverse_chunk_f", 7 + 148) is None  # total 249: this call is the 250th
    kind, reason = b.refusal("brandverse_chunk_f", 7 + 149) or ("", "")  # total 250 are spent
    assert kind == "ceiling_reached"
    assert "250 of the 250" in reason


def test_the_ruled_values_are_in_the_file_and_the_condition_is_still_unset() -> None:
    """Changing a figure here is a decision, not an edit: it needs a decision-log entry and this
    test changed with it. `billing_unlinked_confirmed_on` is the owner's to set; the day they do,
    this assertion is replaced by their date."""
    b = load_dev_budget()
    assert b.ceiling_calls == 250
    assert b.total_at_ruling == 101
    assert b.made_at_ruling == {"brandverse_chunk_f": 7}
    assert b.billing_unlinked_confirmed_on is None


def test_a_gateway_that_reaches_the_provider_cannot_be_built_without_a_ceiling() -> None:
    from app.llm.gateway import LLMGateway
    from tests.llm_fakes import FakeLedger

    with pytest.raises(ValueError, match="dev_budget"):
        LLMGateway(
            registry=None,  # type: ignore[arg-type]
            ledger=FakeLedger(),  # type: ignore[arg-type]
            prompts=None,  # type: ignore[arg-type]
            api_key="x",
            timeout_s=1.0,
            max_rate_limit_waits=0,
            max_rate_limit_wait_s=1.0,
        )
