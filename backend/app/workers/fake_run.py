"""The fake 15-step run (Phases.md Phase 1): real progress events, no pipeline work.

No LLM call, no search, no database write. Each step of `PIPELINE_STEPS` emits `started`, waits
`step_delay_ms`, then emits `completed` — or `skipped` (step 6 at depth D0), or `failed` when the
request asks for a simulated failure. The events are the same contract the LangGraph pipeline
will emit from Phase 6, so the SSE endpoint and the UI are built and debugged against them now.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.core.pipeline import PIPELINE_STEPS, PipelineStep
from app.models.runs import (
    FakeRunJob,
    FakeRunRequest,
    RunCompleted,
    RunEvent,
    RunFailed,
    RunStarted,
    StepEvent,
    StepStatus,
)

log = logging.getLogger(__name__)

RESEARCH_STEP = 6
_RESEARCH_NODE = {"D1": "focused_research", "D2": "deep_research"}


class EventSink(Protocol):
    async def append(self, event: RunEvent) -> str: ...


Sleep = Callable[[float], Awaitable[None]]


def _detail(step: PipelineStep, request: FakeRunRequest) -> str | None:
    """A label for steps whose real counterpart branches; makes the fake path visible."""
    match step.name:
        case "assess_research_necessity":
            return f"fake: depth {request.depth}"
        case "research":
            return f"fake: {_RESEARCH_NODE[request.depth]}"
        case "run_agents":
            return "fake: select_agents then run_agents"
        case "await_human_approval":
            return "fake: approval simulated; no human action recorded"
        case _:
            return None


async def execute_fake_run(job: FakeRunJob, sink: EventSink, sleep: Sleep = asyncio.sleep) -> bool:
    """Emit the run's events. Returns True if it completed, False on a simulated failure."""
    run_id, request = job.run_id, job.request
    delay_s = request.step_delay_ms / 1000

    await sink.append(RunStarted(run_id=run_id))
    log.info("fake run started", extra={"depth": request.depth})

    for step in PIPELINE_STEPS:
        if step.number == RESEARCH_STEP and request.depth == "D0":
            await sink.append(
                StepEvent(
                    run_id=run_id,
                    step=step.number,
                    name=step.name,
                    status=StepStatus.SKIPPED,
                    detail="fake: D0, memory sufficient, no research",
                )
            )
            continue

        await sink.append(
            StepEvent(
                run_id=run_id,
                step=step.number,
                name=step.name,
                status=StepStatus.STARTED,
                detail=_detail(step, request),
            )
        )
        await sleep(delay_s)

        if request.fail_at_step == step.number:
            error = f"simulated failure at step {step.number} ({step.name})"
            await sink.append(
                StepEvent(
                    run_id=run_id,
                    step=step.number,
                    name=step.name,
                    status=StepStatus.FAILED,
                    detail=error,
                )
            )
            await sink.append(RunFailed(run_id=run_id, step=step.number, error=error))
            log.warning("fake run failed (simulated)", extra={"step": step.number})
            return False

        await sink.append(
            StepEvent(run_id=run_id, step=step.number, name=step.name, status=StepStatus.COMPLETED)
        )

    await sink.append(RunCompleted(run_id=run_id))
    log.info("fake run completed")
    return True
