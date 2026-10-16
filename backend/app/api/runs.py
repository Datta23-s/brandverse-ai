"""Runs: the fake-run trigger and the SSE run-progress stream (Architecture.md §10).

`GET /api/v1/runs/{id}/stream` is the endpoint the real pipeline will stream through from Phase 6;
only `POST /api/v1/runs/fake` is specific to Phase 1.

The stream endpoint has no authentication or organisation check yet (chunk B). Until it does, the
stream guard below refuses every run that is not a fake run, so no real run is ever streamable
unauthenticated. Remove the guard only in the change that adds authentication and the org check
(Architecture.md §11, "Unauthenticated stream guard").
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.api.deps import ResourcesDep
from app.core.logging import bind_run_id
from app.core.run_events import STREAM_START, at_or_before, parse_entry_id
from app.errors import JobNotEnqueued
from app.models.runs import FakeRunJob, FakeRunRequest, RunAccepted, RunFailed, RunQueued
from app.workers.jobs import RUN_FAKE_PIPELINE, fake_run_job_id

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/runs", tags=["runs"])
# Mounted only when `fake_runs_enabled` (never in production: Settings refuses it).
fake_router = APIRouter(prefix="/api/v1/runs", tags=["runs"])

# Browser reconnect delay after a dropped connection (milliseconds), sent with the first event.
_RETRY_MS = 2000


def stream_path(run_id: UUID) -> str:
    return f"/api/v1/runs/{run_id}/stream"


@fake_router.post("/fake", status_code=status.HTTP_202_ACCEPTED)
async def start_fake_run(
    resources: ResourcesDep, request: FakeRunRequest | None = None
) -> RunAccepted:
    """Start a fake 15-step run: no LLM call, no pipeline. Returns at once; progress is streamed."""
    run_id = uuid4()
    job = FakeRunJob(run_id=run_id, request=request or FakeRunRequest())
    with bind_run_id(str(run_id)):
        # Written before enqueueing, so the stream exists (and can be subscribed to) immediately.
        await resources.events.append(RunQueued(run_id=run_id, mode="fake"))
        try:
            job_id = fake_run_job_id(run_id)
            queued = await resources.redis.enqueue_job(
                RUN_FAKE_PIPELINE, job.model_dump(mode="json"), _job_id=job_id
            )
            if queued is None:
                raise JobNotEnqueued(f"job {job_id} already exists")
        except Exception as e:
            log.exception("fake run could not be enqueued")
            await resources.events.append(
                RunFailed(run_id=run_id, error=f"not enqueued: {type(e).__name__}")
            )
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "run queue unavailable") from e
        log.info("fake run queued", extra={"request": job.request.model_dump(mode="json")})
    return RunAccepted(run_id=run_id, stream_url=stream_path(run_id))


UNAUTHENTICATED_STREAM_REFUSAL = (
    "streaming is limited to fake runs until authentication and the organisation check exist"
    " (Architecture.md §11, unauthenticated stream guard)"
)


async def refuse_unless_fake_run(run_id: UUID, resources: ResourcesDep) -> None:
    """UNAUTHENTICATED STREAM GUARD — remove only together with authentication + org check.

    Fails closed: a run streams only if its first event is `run.queued` with mode `fake`. A real
    run, a stream whose first event is anything else, or one whose first event was trimmed away,
    is refused.
    """
    first = await resources.events.first(run_id)
    if first is not None and isinstance(first.event, RunQueued) and first.event.mode == "fake":
        return
    log.warning(
        "refused to stream a non-fake run without authentication", extra={"run": str(run_id)}
    )
    raise HTTPException(status.HTTP_403_FORBIDDEN, UNAUTHENTICATED_STREAM_REFUSAL)


@dataclass(frozen=True, slots=True)
class StreamCursor:
    run_id: UUID
    after: str


async def stream_cursor(
    run_id: UUID,
    resources: ResourcesDep,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamCursor:
    """Resolve where a (re)connecting client resumes; reject what cannot stream.

    Runs before the response starts, so 404 and 204 are real status codes, not stream content.
    """
    last = await resources.events.last(run_id)
    if last is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such run, or its events have expired")
    await refuse_unless_fake_run(run_id, resources)

    # A malformed Last-Event-ID is not trusted as a position: replay from the start.
    after = STREAM_START
    if last_event_id is not None and parse_entry_id(last_event_id) is not None:
        after = last_event_id
    if last.is_terminal and at_or_before(last.entry_id, after):
        # The client already has the final event. 204 tells EventSource to stop reconnecting.
        raise HTTPException(status.HTTP_204_NO_CONTENT)
    return StreamCursor(run_id=run_id, after=after)


@router.get(
    "/{run_id}/stream",
    response_class=EventSourceResponse,
    responses={
        status.HTTP_204_NO_CONTENT: {"description": "Run already finished at Last-Event-ID"},
        status.HTTP_403_FORBIDDEN: {"description": "Not a fake run: no streaming without auth"},
        status.HTTP_404_NOT_FOUND: {"description": "Unknown run, or its events expired"},
    },
)
async def stream_run(
    resources: ResourcesDep, cursor: Annotated[StreamCursor, Depends(stream_cursor)]
) -> AsyncIterator[ServerSentEvent]:
    """Server-sent events, one per run event, ending after `run.completed` or `run.failed`.

    Each SSE message has `event:` = the event type (`run.queued`, `run.started`, `step`,
    `run.completed`, `run.failed`), `id:` = its stream entry ID (send it back as `Last-Event-ID`
    to resume), and `data:` = the event as JSON. Step events carry `step`, `name` and `status`.
    """
    first = True
    async for stored in resources.events.follow(cursor.run_id, after=cursor.after):
        yield ServerSentEvent(
            event=stored.event.type,
            id=stored.entry_id,
            data=stored.event,
            retry=_RETRY_MS if first else None,
        )
        first = False
