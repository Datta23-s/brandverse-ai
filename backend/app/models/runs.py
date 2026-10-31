"""Run-progress contracts: requests, responses, job payloads and the events a run emits.

Pydantic v2 at every boundary (Rules.md §3.1): the API validates what it accepts and returns, the
worker validates the job payload it receives, and every event is validated when written to and
when read back from the Redis stream.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, TypeAdapter, model_validator

from app.core.admission import BlockCode
from app.core.pipeline import PIPELINE_STEPS, TOTAL_STEPS
from app.models.base import Contract

ResearchDepth = Literal["D0", "D1", "D2"]
# "fake": the Phase 1 fake run. "pipeline": the real LangGraph pipeline (Phase 6).
RunMode = Literal["fake", "pipeline"]


def utc_now() -> datetime:
    return datetime.now(UTC)


# ── API in / out ────────────────────────────────────────────────────────────────────────────


class FakeRunRequest(Contract):
    """Parameters of a fake run. No LLM call and no pipeline work: timing and outcome only."""

    org_id: UUID = Field(
        description="The organisation the run belongs to; the caller must be its owner or editor."
    )
    product_id: UUID = Field(
        description=(
            "The product the run is for. It must have an active verified claim, and its brand a "
            "sector the registry covers (app/core/admission.py)."
        )
    )
    brief: str | None = Field(
        default=None,
        max_length=2000,
        description="What the campaign should say. A brief asking for a restricted claim blocks.",
    )
    depth: ResearchDepth = Field(
        default="D1", description="Research depth to simulate; D0 skips step 6."
    )
    step_delay_ms: int = Field(
        default=400, ge=0, le=5000, description="Simulated duration of each step."
    )
    fail_at_step: int | None = Field(
        default=None,
        ge=1,
        le=TOTAL_STEPS,
        description="Fail the run at this step, to exercise the failure path in the UI.",
    )


class RunRequest(Contract):
    """A real run (`POST /api/v1/runs`). Unlike the fake run it carries the audience: research is
    planned from the request's stated audience, not from A3's personas, which come after it."""

    org_id: UUID = Field(
        description="The organisation the run belongs to; the caller must be its owner or editor."
    )
    product_id: UUID = Field(description="The product the run is for (admitted as for a fake run).")
    brief: str | None = Field(
        default=None,
        max_length=2000,
        description="What the campaign should say. A brief asking for a restricted claim blocks.",
    )
    audience: str | None = Field(
        default=None,
        max_length=500,
        description="Who the campaign is for, in the requester's words.",
    )
    depth: ResearchDepth = Field(default="D1", description="Research depth requested.")


class RunAccepted(Contract):
    run_id: UUID
    stream_url: str


class BlockReasonOut(Contract):
    code: BlockCode
    message: str
    refs: list[str]


class RunBlocked(Contract):
    """The 409 body when admission refuses a run (blocked and logged; Rules.md T2)."""

    message: str
    reasons: list[BlockReasonOut]


class FakeRunJob(Contract):
    """The ARQ job payload for a fake run (JSON-serialised; never pickled)."""

    run_id: UUID
    request: FakeRunRequest


# ── Events ──────────────────────────────────────────────────────────────────────────────────


class StepStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class _Event(Contract):
    run_id: UUID
    ts: AwareDatetime = Field(default_factory=utc_now)


class RunQueued(_Event):
    """Written by the API before the job is enqueued, so the stream exists from the start."""

    type: Literal["run.queued"] = "run.queued"
    mode: RunMode
    # Whose run it is. Only members of this organisation may stream it (app/api/runs.py).
    org_id: UUID
    total_steps: int = TOTAL_STEPS


class RunStarted(_Event):
    type: Literal["run.started"] = "run.started"
    total_steps: int = TOTAL_STEPS


class StepEvent(_Event):
    type: Literal["step"] = "step"
    step: int = Field(ge=1, le=TOTAL_STEPS)
    name: str
    status: StepStatus
    detail: str | None = None

    @model_validator(mode="after")
    def _name_matches_step(self) -> "StepEvent":
        expected = PIPELINE_STEPS[self.step - 1].name
        if self.name != expected:
            raise ValueError(f"step {self.step} is {expected!r}, not {self.name!r}")
        return self


class RunCompleted(_Event):
    type: Literal["run.completed"] = "run.completed"
    # True when a budget refusal stopped the run (`runs.status = 'partial'`): the steps after the
    # one that was refused did not run. A rejected campaign is a COMPLETE run, not a partial one.
    partial: bool = False


class RunFailed(_Event):
    type: Literal["run.failed"] = "run.failed"
    step: int | None = Field(default=None, ge=1, le=TOTAL_STEPS)
    error: str


RunEvent = Annotated[
    RunQueued | RunStarted | StepEvent | RunCompleted | RunFailed,
    Field(discriminator="type"),
]
TERMINAL_EVENT_TYPES = frozenset({"run.completed", "run.failed"})

run_event_adapter: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)
