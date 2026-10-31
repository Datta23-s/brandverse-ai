"""The campaign record and the approver's decisions (Phases.md Phase 6 chunk E).

A campaign is ONE row per run (`unique (run_id)`, 0041). The pipeline writes it when the run parks
at the approval step (`PoolCampaignStore.park`) and again each time a revision round or an edit-out
changes it. The API reads it, shows the approver the copy and the gate findings, and records the
decision here.

THE DECISION IS KEYED, NOT CHECKED: `approvals` has a unique index on (campaign, revision round)
for every outcome but `undecided` (0041), and the insert below relies on it. Approving twice writes
one row: the second call finds the first decision, and if it is the same outcome it is a replay
(nothing written, the continuation job re-offered under its fixed id), if it differs it is refused.

A7's advisory (`validation["advisory"]`) is stored and NEVER returned: `view_from_row` copies
named fields only (`app/models/campaigns.py`).
"""

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.errors import BrandverseError
from app.models.campaigns import (
    CampaignView,
    DecisionOutcome,
    DecisionView,
    FindingView,
    StatementView,
)

log = logging.getLogger(__name__)

Kind = Literal["evaluation", "calibration", "tuning"]


class CampaignNotFound(BrandverseError):
    """No campaign for the run (not parked yet, or not the caller's)."""


class NotAwaitingDecision(BrandverseError):
    """The campaign is not waiting for a decision (already approved, rejected or ended)."""


class DecisionConflict(BrandverseError):
    """A different decision is already recorded for this round."""


class NoRoundsLeft(BrandverseError):
    """A revision was asked for after the last regeneration round."""


@dataclass(frozen=True, slots=True)
class Decision:
    outcome: DecisionOutcome
    revision_round: int
    recorded: bool  # False: an identical decision was already on record for this round


class PoolCampaignStore:
    def __init__(self, pool: AsyncConnectionPool[AsyncConnection]) -> None:
        self._pool = pool

    async def park(
        self,
        *,
        run_id: UUID,
        org_id: UUID,
        strategy: Mapping[str, Any] | None,
        creative: Mapping[str, Any] | None,
        validation: Mapping[str, Any] | None,
        revision_round: int,
    ) -> None:
        """Write (or rewrite) the campaign as it stands at the approval step. Idempotent: a node
        re-run from a checkpoint writes the same row. A campaign that is no longer waiting
        (approved or rejected) is left exactly as it is."""
        async with self._pool.connection() as conn:
            await conn.execute(
                "insert into public.campaigns (org_id, run_id, strategy, creative, validation, "
                "status, revision_round) values (%s, %s, %s, %s, %s, 'awaiting_approval', %s) "
                "on conflict (run_id) do update set strategy = excluded.strategy, "
                "creative = excluded.creative, validation = excluded.validation, "
                "revision_round = excluded.revision_round, status = 'awaiting_approval' "
                "where public.campaigns.status in ('draft', 'awaiting_approval')",
                (
                    org_id,
                    run_id,
                    Jsonb(strategy) if strategy is not None else None,
                    Jsonb(creative) if creative is not None else None,
                    Jsonb(validation) if validation is not None else None,
                    revision_round,
                ),
            )

    async def revise_copy(
        self, run_id: UUID, creative: Mapping[str, Any], validation: Mapping[str, Any]
    ) -> None:
        """An edit-out changed the copy and the gates were re-run: the campaign follows."""
        async with self._pool.connection() as conn:
            await conn.execute(
                "update public.campaigns set creative = %s, validation = %s "
                "where run_id = %s and status = 'awaiting_approval'",
                (Jsonb(creative), Jsonb(validation), run_id),
            )


async def record_decision(
    pool: AsyncConnectionPool[AsyncConnection],
    *,
    run_id: UUID,
    approver_id: UUID,
    outcome: DecisionOutcome,
    reason: str | None,
    failed_gate: str | None,
    seconds_spent: int,
    kind: Kind,
    max_revisions: int,
) -> Decision:
    """Record the approver's decision for the campaign's CURRENT round, once.

    One transaction under a row lock on the campaign: the decision, and for approve and reject the
    campaign's own status (`approved_by` is checked by the database against the approver role and
    its revocation, 0007). Raises CampaignNotFound, NotAwaitingDecision, DecisionConflict or
    NoRoundsLeft."""
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "select id, org_id, status, revision_round from public.campaigns "
            "where run_id = %s for update",
            (run_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise CampaignNotFound(f"no campaign for run {run_id}")
        campaign_id, org_id, status, round_ = row
        cur = await conn.execute(
            "select outcome, approver_id from public.approvals where campaign_id = %s "
            "and revision_round = %s and outcome <> 'undecided'",
            (campaign_id, round_),
        )
        decided = await cur.fetchall()
        # The index keys a decision by approver (calibration records two scorers on one campaign).
        # In an evaluation organisation ONE decision settles the round, whoever made it; in a
        # practice organisation each approver's own decision is the one that counts.
        relevant = [d for d in decided if d[1] == approver_id or kind == "evaluation"]
        if relevant:
            if relevant[0][0] == outcome and relevant[0][1] == approver_id:
                return Decision(outcome, round_, recorded=False)
            raise DecisionConflict(f"this round already has a decision: {relevant[0][0]}")
        if status != "awaiting_approval":
            raise NotAwaitingDecision(f"the campaign is {status}")
        if outcome == "revise" and round_ >= max_revisions:
            raise NoRoundsLeft(f"no regeneration rounds left (limit {max_revisions})")
        await conn.execute(
            "insert into public.approvals (campaign_id, org_id, approver_id, outcome, failed_gate, "
            "reason, seconds_spent, revision_round, kind, is_calibration) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                campaign_id,
                org_id,
                approver_id,
                outcome,
                failed_gate if outcome != "approve" else None,
                reason,
                seconds_spent,
                round_,
                kind,
                kind == "calibration",
            ),
        )
        if outcome == "approve":
            await conn.execute(
                "update public.campaigns set status = 'approved', approved_by = %s, "
                "approved_at = now() where id = %s",
                (approver_id, campaign_id),
            )
        elif outcome == "reject":
            await conn.execute(
                "update public.campaigns set status = 'rejected' where id = %s", (campaign_id,)
            )
    return Decision(outcome, round_, recorded=True)


def view_from_row(
    row: Mapping[str, Any], decisions: Sequence[Mapping[str, Any]], max_revisions: int
) -> CampaignView:
    """The approver's view of a campaign row: named fields only (never `validation["advisory"]`)."""
    creative = row["creative"] or {}
    validation = row["validation"] or {}
    stmts = validation.get("statements") or []
    return CampaignView(
        campaign_id=row["id"],
        run_id=row["run_id"],
        status=row["status"],
        revision_round=row["revision_round"],
        rounds_remaining=max(0, max_revisions - row["revision_round"]),
        gate_outcome=validation.get("outcome"),
        copy_parts={k: str(v) for k, v in (creative.get("copy") or {}).items()},
        findings=[
            FindingView(
                gate=f["gate"],
                kind=f["kind"],
                outcome=f["outcome"],
                message=f["message"],
                span=f.get("span"),
                statement=f.get("statement"),
            )
            for f in validation.get("findings") or []
        ],
        statements=[
            StatementView(text=s["text"], cited=s.get("claim_id") is not None) for s in stmts
        ],
        edited_out=[str(e) for e in validation.get("edited_out") or []],
        decisions=[
            DecisionView(
                outcome=d["outcome"],
                reason=d["reason"],
                failed_gate=d["failed_gate"],
                revision_round=d["revision_round"],
                decided_at=d["decided_at"],
            )
            for d in decisions
        ],
    )


async def audit(
    pool: AsyncConnectionPool[AsyncConnection],
    *,
    org_id: UUID,
    actor_id: UUID,
    action: str,
    run_id: UUID,
    details: Mapping[str, Any],
) -> None:
    """One audit_log row for an act on a campaign (written by the backend's own role)."""
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.audit_log (org_id, actor_type, actor_id, action, entity_type, "
            "entity_id, details) values (%s, 'user', %s, %s, 'run', %s, %s)",
            (org_id, str(actor_id), action, str(run_id), json.dumps(details, default=str)),
        )
