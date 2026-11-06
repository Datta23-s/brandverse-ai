"""The evaluation's own call budget (owner, 2026-11-06): a scope separate from the development
ceiling, enforced at admission like it. `config/evaluation_budget.yaml` holds the ceiling, its
sizing (660 base plus a re-run allowance) and the databases whose calls are evaluation calls.

The two scopes are DISJOINT: the ledger sends a call made from an evaluation database to this
budget and every other database to the development one, so the development ceiling's state cannot
refuse an evaluation call. A database in neither is refused by whichever scope receives it.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from app.core.config import REPO_ROOT
from app.llm.dev_budget import DevBudget

EVALUATION_BUDGET_PATH = REPO_ROOT / "config" / "evaluation_budget.yaml"


@dataclass(frozen=True, slots=True)
class EvaluationBudget:
    ceiling_calls: int
    base_calls: int
    rerun_allowance_calls: int
    report_to_owner_at_calls: int
    databases: frozenset[str]
    # The shared billing fact (config/dev_budget.yaml); None until the owner has confirmed it.
    billing_unlinked_confirmed_on: date | None

    def covers(self, database: str) -> bool:
        return database in self.databases

    def report_due(self, made_here_now: int) -> str | None:
        if made_here_now >= self.report_to_owner_at_calls:
            return (
                f"{made_here_now} of the {self.ceiling_calls} evaluation calls are spent, at or "
                f"past the {self.report_to_owner_at_calls} the owner asked to be told at: report, "
                "do not raise the ceiling"
            )
        return None

    def refusal(self, database: str, made_here_now: int) -> tuple[str, str] | None:
        """(kind, reason) when the next evaluation call may not be made, else None."""
        if self.billing_unlinked_confirmed_on is None:
            return (
                "billing_not_confirmed",
                "refused: no call of any tier until it is confirmed that no billing account is "
                "linked to the Cloud project (config/dev_budget.yaml "
                "billing_unlinked_confirmed_on is unset; only the owner can set it)",
            )
        if not self.covers(database):
            return (
                "database_unregistered",
                f"refused: database {database!r} is not an evaluation database in "
                "config/evaluation_budget.yaml",
            )
        if made_here_now >= self.ceiling_calls:
            return (
                "ceiling_reached",
                f"refused: {made_here_now} of the {self.ceiling_calls} evaluation calls are spent",
            )
        return None


def scope_refusal(
    database: str,
    made_here_now: int,
    dev_budget: DevBudget | None,
    evaluation_budget: EvaluationBudget | None,
) -> tuple[str, str, bool] | None:
    """(kind, reason, is_evaluation) when the next call from `database` may not be made, else None.

    The scope is chosen by the DATABASE before either budget is consulted: an evaluation database
    is judged by the evaluation budget alone, so no state of the development ceiling can refuse an
    evaluation call; every other database is judged by the development budget alone."""
    if evaluation_budget is not None and evaluation_budget.covers(database):
        decided = evaluation_budget.refusal(database, made_here_now)
        return None if decided is None else (decided[0], decided[1], True)
    if dev_budget is None:
        return (
            "database_unregistered",
            f"refused: database {database!r} is not an evaluation database and this ledger has "
            "no development budget",
            False,
        )
    decided = dev_budget.refusal(database, made_here_now)
    return None if decided is None else (decided[0], decided[1], False)


def load_evaluation_budget(
    path: Path = EVALUATION_BUDGET_PATH, dev_budget_path: Path | None = None
) -> EvaluationBudget:
    from app.llm.dev_budget import DEV_BUDGET_PATH, load_dev_budget

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    dev = load_dev_budget(dev_budget_path or DEV_BUDGET_PATH)
    ceiling = int(raw["ceiling_calls"])
    base, allowance = int(raw["base_calls"]), int(raw["rerun_allowance_calls"])
    if ceiling != base + allowance:
        raise ValueError("ceiling_calls must equal base_calls + rerun_allowance_calls")
    if not 0 < int(raw["report_to_owner_at_calls"]) < ceiling:
        raise ValueError("report_to_owner_at_calls must be above 0 and below ceiling_calls")
    databases = frozenset(str(d) for d in raw["databases"])
    if databases & set(dev.made_at_ruling):
        raise ValueError(
            "a database is in both the development and the evaluation scope: "
            f"{sorted(databases & set(dev.made_at_ruling))}"
        )
    return EvaluationBudget(
        ceiling_calls=ceiling,
        base_calls=base,
        rerun_allowance_calls=allowance,
        report_to_owner_at_calls=int(raw["report_to_owner_at_calls"]),
        databases=databases,
        billing_unlinked_confirmed_on=dev.billing_unlinked_confirmed_on,
    )
