"""The development call ceiling (owner, 2026-11-01): the decision and the values the owner ruled."""

from datetime import date

import pytest

from app.llm.dev_budget import DevBudget, load_dev_budget


def budget(confirmed: date | None = date(2026, 11, 2)) -> DevBudget:
    return DevBudget(
        ceiling_calls=500,
        total_at_ruling=101,
        made_at_ruling={"brandverse_chunk_f": 7},
        billing_unlinked_confirmed_on=confirmed,
        report_to_owner_at_calls=400,
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


def test_the_500th_call_is_the_last_and_the_501st_is_refused() -> None:
    b = budget()
    assert b.refusal("brandverse_chunk_f", 7 + 398) is None  # total 499: this call is the 500th
    kind, reason = b.refusal("brandverse_chunk_f", 7 + 399) or ("", "")  # total 500 are spent
    assert kind == "ceiling_reached"
    assert "500 of the 500" in reason


def test_the_owner_is_told_at_400_which_does_not_refuse_the_call() -> None:
    """Owner, 2026-11-05: report at 400 rather than raising the ceiling again quietly. A tripwire,
    not a refusal: the ceiling is what refuses."""
    b = budget()
    assert b.report_due("brandverse_chunk_f", 7 + 298) is None  # total 399
    due = b.report_due("brandverse_chunk_f", 7 + 299)  # total 400
    assert due is not None and "400" in due and "do not raise the ceiling" in due
    assert b.refusal("brandverse_chunk_f", 7 + 299) is None  # ... and the call is still allowed


def test_a_report_threshold_at_or_above_the_ceiling_is_refused(tmp_path: object) -> None:
    """It would never fire before the refusal does, so it is a configuration error, not a choice."""
    from pathlib import Path

    import yaml

    source = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "config" / "dev_budget.yaml").read_text(
            encoding="utf-8"
        )
    )
    source["report_to_owner_at_calls"] = source["ceiling_calls"]
    path = Path(str(tmp_path)) / "dev_budget.yaml"
    path.write_text(yaml.safe_dump(source), encoding="utf-8")
    with pytest.raises(ValueError, match="report_to_owner_at_calls"):
        load_dev_budget(path)


def test_the_ruled_values_are_in_the_file_and_the_condition_is_confirmed() -> None:
    """Changing a figure here is a decision, not an edit: it needs a decision-log entry and this
    test changed with it. `billing_unlinked_confirmed_on` was set on 2026-11-04 (decision-log
    2.24; Architecture.md 4.4): `gcloud billing projects describe gen-lang-client-0622579315`
    printed billingEnabled false. A change to that date is the owner's. The ceiling was RAISED to
    500 on 2026-11-05 (decision-log 2.31): the 250 bounded spend while billing was unconfirmed, the
    standard tier is free-tier at 500 requests a day, and a provider outage consumed the 250."""
    b = load_dev_budget()
    assert b.ceiling_calls == 500
    assert b.report_to_owner_at_calls == 400
    assert b.total_at_ruling == 101
    assert b.made_at_ruling == {"brandverse_chunk_f": 7, "brandverse_stage2": 0}
    assert b.billing_unlinked_confirmed_on == date(2026, 11, 4)


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
