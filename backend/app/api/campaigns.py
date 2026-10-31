"""The campaign view and the approver's decisions (Phases.md Phase 6 chunk E; Rules.md T6).

- `GET  /api/v1/runs/{run_id}/campaign`, `GET /api/v1/campaigns/{campaign_id}`: the copy and the
  gate findings beside it, for any member of the run's organisation. A7's advisory is not in it.
- `POST /api/v1/runs/{run_id}/approve | revise | reject`: the decision of an APPROVER (the role, not
  owner or editor). The decision is recorded once per round (`record_decision`), then the run is
  continued by a worker job whose id is fixed by (run, round), so submitting twice writes one
  `approvals` row and queues one continuation.
- `POST /api/v1/runs/{run_id}/edit-out`: the reviewer removes words from the copy; the gates run
  again. The approver never rewrites text.

A caller who is not a member gets the same 404 as for a campaign that does not exist.
"""

import logging
import secrets
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.deps import IdentityDep, ResourcesDep, require_db
from app.core.campaigns import (
    CampaignNotFound,
    DecisionConflict,
    Kind,
    NoRoundsLeft,
    NotAwaitingDecision,
    record_decision,
    view_from_row,
)
from app.core.db import as_user
from app.core.logging import bind_run_id
from app.core.tenancy import has_role, is_member
from app.models.campaigns import (
    CampaignView,
    DecisionAccepted,
    DecisionJob,
    DecisionOutcome,
    DecisionRequest,
    EditOutAccepted,
    EditOutJob,
    EditOutRequest,
)
from app.workers.jobs import EDIT_OUT_PIPELINE, RESUME_PIPELINE, edit_out_job_id, resume_job_id

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["campaigns"])

NOT_FOUND = "no such campaign, or it is not in your organisation"

_SELECT = (
    "select id, org_id, run_id, status, revision_round, creative, validation "
    "from public.campaigns where "
)
_BY_RUN = _SELECT + "run_id = %s"
_BY_ID = _SELECT + "id = %s"
_DECISIONS = (
    "select outcome, reason, failed_gate, revision_round, decided_at from public.approvals "
    "where campaign_id = %s order by decided_at, id"
)


async def _load_view(
    resources: ResourcesDep, identity: IdentityDep, query: str, key: UUID
) -> CampaignView:
    pool = require_db(resources)
    async with as_user(pool, identity) as conn:
        cur = await conn.execute(query, (key,))
        row = await cur.fetchone()
        if row is None:  # row-level security shows a member their own organisation's only
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        cur = await conn.execute(_DECISIONS, (row[0],))
        decisions: list[Mapping[str, Any]] = [
            {
                "outcome": d[0],
                "reason": d[1],
                "failed_gate": d[2],
                "revision_round": d[3],
                "decided_at": d[4],
            }
            for d in await cur.fetchall()
        ]
    record: dict[str, Any] = {
        "id": row[0],
        "org_id": row[1],
        "run_id": row[2],
        "status": row[3],
        "revision_round": row[4],
        "creative": row[5],
        "validation": row[6],
    }
    return view_from_row(record, decisions, resources.settings.max_revision_rounds)


@router.get("/runs/{run_id}/campaign")
async def campaign_of_run(
    run_id: UUID, resources: ResourcesDep, identity: IdentityDep
) -> CampaignView:
    return await _load_view(resources, identity, _BY_RUN, run_id)


@router.get("/campaigns/{campaign_id}")
async def campaign_by_id(
    campaign_id: UUID, resources: ResourcesDep, identity: IdentityDep
) -> CampaignView:
    return await _load_view(resources, identity, _BY_ID, campaign_id)


async def _approver_org(
    resources: ResourcesDep, identity: IdentityDep, run_id: UUID
) -> tuple[UUID, bool]:
    """The run's organisation, if the caller is a member and holds the approver role there; and
    whether it is a practice organisation. 404 for a non-member, 403 for a member who may not decide
    (Rules.md T6: owner, editor and viewer do not confer approval; the database checks it again)."""
    pool = require_db(resources)
    async with as_user(pool, identity) as conn:
        cur = await conn.execute("select org_id from public.campaigns where run_id = %s", (run_id,))
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        org_id: UUID = row[0]
        if not await is_member(conn, org_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        if not await has_role(conn, org_id, ["approver"]):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "deciding needs the approver role")
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select is_practice from public.organisations where id = %s", (org_id,)
        )
        practice = await cur.fetchone()
    return org_id, bool(practice and practice[0])


def _kind(resources: ResourcesDep, org_id: UUID, practice: bool) -> Kind:
    """The approval's kind, derived by the server and never supplied by the caller (a trust-deciding
    column, Architecture.md section 9): a practice organisation's decisions are tuning when it is
    THE tuning organisation and calibration otherwise; every other organisation's are evaluation."""
    if not practice:
        return "evaluation"
    return "tuning" if org_id == resources.settings.llm_strong_tier_org_id else "calibration"


async def _decide(
    outcome: DecisionOutcome,
    run_id: UUID,
    body: DecisionRequest,
    resources: ResourcesDep,
    identity: IdentityDep,
) -> DecisionAccepted:
    if outcome != "approve" and not (body.reason or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "a reason is required")
    org_id, practice = await _approver_org(resources, identity, run_id)
    try:
        decision = await record_decision(
            require_db(resources),
            run_id=run_id,
            approver_id=identity.user_id,
            outcome=outcome,
            reason=body.reason,
            failed_gate=body.failed_gate,
            seconds_spent=body.seconds_spent,
            kind=_kind(resources, org_id, practice),
            max_revisions=resources.settings.max_revision_rounds,
        )
    except CampaignNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from e
    except (NotAwaitingDecision, DecisionConflict, NoRoundsLeft) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    # Offered on a replay too: the job id is fixed by (run, round), so a second offer is refused
    # by ARQ, and a first offer lost to a crash between the commit and here is made good.
    job = DecisionJob(run_id=run_id, outcome=outcome, by=identity.user_id, reason=body.reason)
    with bind_run_id(str(run_id)):
        try:
            await resources.redis.enqueue_job(
                RESUME_PIPELINE,
                job.model_dump(mode="json"),
                _job_id=resume_job_id(run_id, decision.revision_round),
            )
        except Exception as e:
            log.exception("the continuation could not be queued; the decision is recorded")
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "decision recorded; the run could not be queued, submit it again",
            ) from e
    return DecisionAccepted(
        run_id=run_id,
        outcome=outcome,
        revision_round=decision.revision_round,
        recorded=decision.recorded,
    )


_DECIDED: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {"description": "Not waiting, or a different decision"}
}


@router.post("/runs/{run_id}/approve", status_code=status.HTTP_202_ACCEPTED, responses=_DECIDED)
async def approve(
    run_id: UUID, body: DecisionRequest, resources: ResourcesDep, identity: IdentityDep
) -> DecisionAccepted:
    return await _decide("approve", run_id, body, resources, identity)


@router.post("/runs/{run_id}/revise", status_code=status.HTTP_202_ACCEPTED, responses=_DECIDED)
async def revise(
    run_id: UUID, body: DecisionRequest, resources: ResourcesDep, identity: IdentityDep
) -> DecisionAccepted:
    return await _decide("revise", run_id, body, resources, identity)


@router.post("/runs/{run_id}/reject", status_code=status.HTTP_202_ACCEPTED, responses=_DECIDED)
async def reject(
    run_id: UUID, body: DecisionRequest, resources: ResourcesDep, identity: IdentityDep
) -> DecisionAccepted:
    return await _decide("reject", run_id, body, resources, identity)


@router.post("/runs/{run_id}/edit-out", status_code=status.HTTP_202_ACCEPTED)
async def edit_out(
    run_id: UUID, body: EditOutRequest, resources: ResourcesDep, identity: IdentityDep
) -> EditOutAccepted:
    """Queue the removal of exactly these words from the copy. The gates re-run in the worker; the
    page re-reads the campaign to see the result (`edited_out` grows by one)."""
    await _approver_org(resources, identity, run_id)
    job = EditOutJob(run_id=run_id, statement=body.statement, by=identity.user_id)
    try:
        await resources.redis.enqueue_job(
            EDIT_OUT_PIPELINE,
            job.model_dump(mode="json"),
            _job_id=edit_out_job_id(run_id, secrets.token_hex(4)),
        )
    except Exception as e:
        log.exception("edit-out could not be queued")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "queue unavailable") from e
    return EditOutAccepted(run_id=run_id)
