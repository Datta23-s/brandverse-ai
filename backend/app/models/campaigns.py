"""The campaign view, the approver's decisions, and the jobs that act on a parked run.

The approver sees the COPY and the GATE FINDINGS beside it. A7's advisory (`validation["advisory"]`)
is stored with the campaign and is never part of this view: `CampaignView` has no field that could
carry it, and `view_from_row` copies named fields only, so a new key in `validation` reaches nobody
until it is added here on purpose (ruled 2026-10-29: the approver does not see the advisory, in
calibration and evaluation alike).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.models.base import Contract
from app.models.runs import RunRequest

CampaignStatus = Literal["draft", "awaiting_approval", "approved", "rejected"]
DecisionOutcome = Literal["approve", "revise", "reject"]
GateOutcome = Literal["pass", "flag", "block"]


class FindingView(Contract):
    gate: str
    kind: str
    outcome: Literal["flag", "block"]
    message: str
    # The exact words the finding is about, when it names some (a quote from the copy).
    span: str | None
    statement: str | None


class StatementView(Contract):
    """A factual statement the classifier extracted, and whether it rests on a claim."""

    text: str
    cited: bool


class DecisionView(Contract):
    outcome: Literal["approve", "revise", "reject", "undecided"]
    reason: str | None
    failed_gate: str | None
    revision_round: int
    decided_at: datetime


class CampaignView(Contract):
    campaign_id: UUID
    run_id: UUID
    status: CampaignStatus
    revision_round: int
    # Regeneration rounds the approver may still ask for (the bound is 2, Phases.md chunk B).
    rounds_remaining: int
    # The gates' verdict on the copy as it now stands.
    gate_outcome: GateOutcome | None
    copy_parts: dict[str, str]
    findings: list[FindingView]
    statements: list[StatementView]
    # Statements the reviewer has removed, in order (each re-ran the gates).
    edited_out: list[str]
    decisions: list[DecisionView]


class DecisionRequest(Contract):
    reason: str | None = Field(default=None, max_length=2000)
    # The rubric gate id the decision rests on (docs/approval-rubric.md section 5), e.g. "T1.3".
    failed_gate: str | None = Field(default=None, max_length=40)
    # Time the approver spent on this campaign, measured by the client (rubric section 5).
    seconds_spent: int = Field(ge=0, le=86_400)


class DecisionAccepted(Contract):
    run_id: UUID
    outcome: DecisionOutcome
    revision_round: int
    # False when this exact decision was already recorded for the round: nothing new was written.
    recorded: bool


class EditOutRequest(Contract):
    statement: str = Field(
        min_length=1,
        max_length=1500,
        description="Exact words in the copy to remove. The approver does not rewrite text.",
    )


class EditOutAccepted(Contract):
    run_id: UUID


class DecisionJob(Contract):
    """The ARQ payload that continues a parked run after the approver's decision."""

    run_id: UUID
    outcome: DecisionOutcome
    by: UUID
    reason: str | None


class EditOutJob(Contract):
    run_id: UUID
    statement: str
    by: UUID


class PipelineRunJob(Contract):
    """The ARQ payload that starts a real run. The run row and its `run.queued` event exist."""

    run_id: UUID
    request: RunRequest
    by: UUID
