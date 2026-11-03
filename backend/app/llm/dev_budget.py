"""The development call ceiling (owner, 2026-11-01), enforced at admission like the strong-tier cap.

`config/dev_budget.yaml` holds the ceiling, the owner's tally at the ruling, the databases allowed
to make calls (each with what its own `llm_calls` held at the ruling), and the billing
condition. The ledger counts from `llm_calls` (a count, never a counter: Rules.md A1) and asks
`refusal()` whether the next call may be made. The decision is a pure function so it can be
tested without a database.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from app.core.config import REPO_ROOT

DEV_BUDGET_PATH = REPO_ROOT / "config" / "dev_budget.yaml"


@dataclass(frozen=True, slots=True)
class DevBudget:
    ceiling_calls: int
    total_at_ruling: int
    made_at_ruling: dict[str, int]  # database name -> its own non-refused calls at the ruling
    billing_unlinked_confirmed_on: date | None

    def total(self, database: str, made_here_now: int) -> int:
        """Calls in all databases: the owner's figure at the ruling plus this database's growth."""
        return self.total_at_ruling + (made_here_now - self.made_at_ruling[database])

    def refusal(self, database: str, made_here_now: int) -> tuple[str, str] | None:
        """(kind, reason) when the next call may not be made, else None. Checked in this order:
        the billing condition, the database, then the ceiling."""
        if self.billing_unlinked_confirmed_on is None:
            return (
                "billing_not_confirmed",
                "refused: no call of any tier until it is confirmed that no billing account is "
                "linked to Cloud project brandverse-ai (config/dev_budget.yaml "
                "billing_unlinked_confirmed_on is unset; only the owner can set it)",
            )
        if database not in self.made_at_ruling:
            return (
                "database_unregistered",
                f"refused: database {database!r} is not listed in config/dev_budget.yaml, so its "
                "calls cannot be added to the development tally; list it first",
            )
        total = self.total(database, made_here_now)
        if total >= self.ceiling_calls:
            return (
                "ceiling_reached",
                f"refused: {total} of the {self.ceiling_calls} development calls are spent",
            )
        return None


def load_dev_budget(path: Path = DEV_BUDGET_PATH) -> DevBudget:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    confirmed = raw["billing_unlinked_confirmed_on"]
    if confirmed is not None and not isinstance(confirmed, date):
        raise ValueError("billing_unlinked_confirmed_on must be a YYYY-MM-DD date or null")
    return DevBudget(
        ceiling_calls=int(raw["ceiling_calls"]),
        total_at_ruling=int(raw["total_at_ruling"]),
        made_at_ruling={str(k): int(v) for k, v in raw["databases"].items()},
        billing_unlinked_confirmed_on=confirmed,
    )
