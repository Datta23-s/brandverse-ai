"""Run contracts: validated at the boundary, round-trip through JSON unchanged."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.runs import (
    FakeRunRequest,
    RunCompleted,
    RunQueued,
    StepEvent,
    StepStatus,
    run_event_adapter,
)


def test_step_name_must_match_step_number() -> None:
    with pytest.raises(ValidationError, match="step 4 is 'retrieve_memory'"):
        StepEvent(run_id=uuid4(), step=4, name="write_memory", status=StepStatus.STARTED)


@pytest.mark.parametrize("step", [0, 16])
def test_step_number_in_range(step: int) -> None:
    with pytest.raises(ValidationError):
        StepEvent(run_id=uuid4(), step=step, name="x", status=StepStatus.STARTED)


@pytest.mark.parametrize(
    "event",
    [
        RunQueued(run_id=uuid4(), mode="fake", org_id=uuid4()),
        StepEvent(run_id=uuid4(), step=6, name="research", status=StepStatus.SKIPPED, detail="d"),
        RunCompleted(run_id=uuid4()),
    ],
)
def test_events_round_trip_through_json(event: RunQueued | StepEvent | RunCompleted) -> None:
    assert run_event_adapter.validate_json(run_event_adapter.dump_json(event)) == event


def test_unknown_event_type_rejected() -> None:
    with pytest.raises(ValidationError):
        run_event_adapter.validate_python({"type": "run.exploded", "run_id": str(uuid4())})


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(ValidationError):
        RunCompleted(run_id=uuid4(), ts="2026-10-16T10:00:00")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "body",
    [
        {"step_delay_ms": 5001},
        {"step_delay_ms": -1},
        {"fail_at_step": 16},
        {"depth": "D3"},
        {"x": 1},
    ],
)
def test_fake_run_request_bounds(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FakeRunRequest.model_validate(body)
