"""Run-progress contracts: requests, responses, job payloads and the events a run emits.

Pydantic v2 at every boundary (Rules.md §3.1): the API validates what it accepts and returns, the
worker validates the job payload it receives, and every event is validated when written to and
when read back from the Redis stream.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.core.pipeline import PIPELINE_STEPS, TOTAL_STEPS

ResearchDepth = Literal["D0", "D1", "D2"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ── API in / out ────────────────────────────────────────────────────────────────────────────


class FakeRunRequest(_Contract):
    """Parameters of a fake run. No LLM call and no pipeline work: timing and outcome only."""

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


class RunAccepted(_Contract):
    run_id: UUID
    stream_url: str


class FakeRunJob(_Contract):
    """The ARQ job payload for a fake run (JSON-serialised; never pickled)."""

    run_id: UUID
    request: FakeRunRequest


# ── Events ──────────────────────────────────────────────────────────────────────────────────


class StepStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class _Event(_Contract):
    run_id: UUID
    ts: AwareDatetime = Field(default_factory=utc_now)


class RunQueued(_Event):
    """Written by the API before the job is enqueued, so the stream exists from the start."""

    type: Literal["run.queued"] = "run.queued"
    mode: Literal["fake"]
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
