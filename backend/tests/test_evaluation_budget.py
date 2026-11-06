"""The evaluation's own budget scope (decision-log 2.36). The class of defect it closes: the only
budget a real-provider gateway could be built with was the DEVELOPMENT ceiling, which refuses any
database not listed in it, so the evaluation's ~660 calls would have been refused on day one. The
property tested is exactly "nothing in the development ceiling can refuse an evaluation call"."""

from dataclasses import replace
from datetime import date

import pytest

from app.llm.dev_budget import DevBudget, load_dev_budget
from app.llm.evaluation_budget import (
    EvaluationBudget,
    load_evaluation_budget,
    scope_refusal,
)

DEV = load_dev_budget()
EVAL = load_evaluation_budget()
EVAL_DB = sorted(EVAL.databases)[0]
# The plan's cost: 30 requests x 3 arms, the agents of each run and the revision rounds an approver
# may ask for, about 660 Standard calls (docs/phase7-first-decision.md). The re-run rule allows an
# invalidated run to be redone, so the budget must exceed the base by a real allowance.
PLAN_BASE_CALLS = 660


def test_the_budget_covers_the_plans_base_cost_with_a_real_rerun_allowance() -> None:
    assert EVAL.base_calls == PLAN_BASE_CALLS
    assert EVAL.ceiling_calls == EVAL.base_calls + EVAL.rerun_allowance_calls
    assert EVAL.rerun_allowance_calls >= PLAN_BASE_CALLS // 3  # one re-run in three, at least
    assert 0 < EVAL.report_to_owner_at_calls < EVAL.ceiling_calls
    assert EVAL.report_to_owner_at_calls > EVAL.base_calls  # the report is not due on a clean run


def test_the_two_scopes_name_disjoint_databases() -> None:
    assert not EVAL.databases & set(DEV.made_at_ruling)
    assert EVAL.databases  # an evaluation scope with no database would refuse everything


def test_an_evaluation_call_is_not_refused_by_any_state_of_the_development_ceiling() -> None:
    for dev in (
        DEV,
        replace(DEV, ceiling_calls=1, total_at_ruling=10_000),  # spent twice over
        replace(DEV, made_at_ruling={}),  # no database listed at all
        replace(DEV, billing_unlinked_confirmed_on=None),  # ITS billing condition unset
    ):
        assert scope_refusal(EVAL_DB, 0, dev, replace_billing(EVAL)) is None
        assert scope_refusal(EVAL_DB, PLAN_BASE_CALLS + 200, dev, replace_billing(EVAL)) is None
    # and with no development budget on the ledger at all
    assert scope_refusal(EVAL_DB, 0, None, replace_billing(EVAL)) is None


def replace_billing(ev: EvaluationBudget) -> EvaluationBudget:
    """The shared billing fact, confirmed (it is read from the development file in production)."""
    return replace(ev, billing_unlinked_confirmed_on=date(2026, 11, 4))


def test_the_evaluation_ceiling_refuses_at_its_own_limit_and_only_there() -> None:
    ev = replace_billing(EVAL)
    assert scope_refusal(EVAL_DB, ev.ceiling_calls - 1, DEV, ev) is None
    kind, _reason, is_evaluation = scope_refusal(EVAL_DB, ev.ceiling_calls, DEV, ev) or ("", "", 0)
    assert (kind, is_evaluation) == ("ceiling_reached", True)


def test_a_database_in_neither_scope_is_refused_and_a_dev_database_is_judged_by_dev() -> None:
    ev = replace_billing(EVAL)
    kind, _r, is_evaluation = scope_refusal("some_other_db", 0, DEV, ev) or ("", "", 0)
    assert (kind, is_evaluation) == ("database_unregistered", False)
    dev_db = sorted(DEV.made_at_ruling)[0]
    assert scope_refusal(dev_db, DEV.made_at_ruling[dev_db], DEV, ev) is None
    spent = replace(DEV, ceiling_calls=DEV.total_at_ruling)  # development exhausted
    refused = scope_refusal(dev_db, DEV.made_at_ruling[dev_db], spent, ev)
    assert refused is not None and refused[0] == "ceiling_reached" and refused[2] is False


def test_the_billing_condition_still_applies_to_the_evaluation() -> None:
    unset = replace(EVAL, billing_unlinked_confirmed_on=None)
    refused = scope_refusal(EVAL_DB, 0, DEV, unset)
    assert refused is not None and refused[0] == "billing_not_confirmed"


def test_a_database_in_both_scopes_fails_to_load(tmp_path) -> None:  # type: ignore[no-untyped-def]
    both = tmp_path / "e.yaml"
    both.write_text(
        "ceiling_calls: 10\nbase_calls: 6\nrerun_allowance_calls: 4\n"
        f"report_to_owner_at_calls: 8\ndatabases:\n  - {sorted(DEV.made_at_ruling)[0]}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="both the development and the evaluation scope"):
        load_evaluation_budget(both)


def test_the_development_budget_type_is_unchanged() -> None:
    assert isinstance(DEV, DevBudget) and DEV.ceiling_calls == 700
