"""Runs: the fake-run trigger and the SSE run-progress stream (Architecture.md §10).

`GET /api/v1/runs/{id}/stream` is the endpoint the real pipeline will stream through from Phase 6;
only `POST /api/v1/runs/fake` is specific to Phase 1.

Both require a signed-in caller (a verified Supabase access token). A run belongs to the
organisation recorded in its first event (`run.queued.org_id`, written by this API). Streaming it
needs membership of that organisation, decided by migration 0003's app.member_org_ids() under the
caller's identity; starting one needs owner or editor (app.has_role), as `runs_insert` requires.
A caller who is not a member gets the same 404 as for a run that does not exist: a run's
existence is not disclosed across organisations.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.api.deps import IdentityDep, ResourcesDep, require_db
from app.core.db import as_user
from app.core.logging import bind_run_id
from app.core.run_events import STREAM_START, at_or_before, parse_entry_id
from app.core.tenancy import has_role, is_member
from app.errors import JobNotEnqueued
from app.models.runs import FakeRunJob, FakeRunRequest, RunAccepted, RunFailed, RunQueued
from app.workers.jobs import RUN_FAKE_PIPELINE, fake_run_job_id

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/runs", tags=["runs"])
# Mounted only when `fake_runs_enabled` (never in production: Settings refuses it).
fake_router = APIRouter(prefix="/api/v1/runs", tags=["runs"])

# Browser reconnect delay after a dropped connection (milliseconds), sent with the first event.
_RETRY_MS = 2000

RUN_NOT_FOUND = "no such run, or its events have expired"
ORG_NOT_FOUND = "no such organisation"


def stream_path(run_id: UUID) -> str:
    return f"/api/v1/runs/{run_id}/stream"


@fake_router.post("/fake", status_code=status.HTTP_202_ACCEPTED)
async def start_fake_run(
    resources: ResourcesDep, identity: IdentityDep, request: FakeRunRequest
) -> RunAccepted:
    """Start a fake 15-step run: no LLM call, no pipeline. Returns at once; progress is streamed."""
    async with as_user(require_db(resources), identity) as conn:
        if not await is_member(conn, request.org_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, ORG_NOT_FOUND)
        if not await has_role(conn, request.org_id, ["owner", "editor"]):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "starting a run needs owner or editor")

    run_id = uuid4()
    job = FakeRunJob(run_id=run_id, request=request)
    with bind_run_id(str(run_id)):
        # Written before enqueueing, so the stream exists (and can be subscribed to) immediately.
        await resources.events.append(RunQueued(run_id=run_id, mode="fake", org_id=request.org_id))
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
        log.info(
            "fake run queued",
            extra={"request": job.request.model_dump(mode="json"), "user": str(identity.user_id)},
        )
    return RunAccepted(run_id=run_id, stream_url=stream_path(run_id))


@dataclass(frozen=True, slots=True)
class StreamCursor:
    run_id: UUID
    after: str


async def stream_cursor(
    run_id: UUID,
    resources: ResourcesDep,
    identity: IdentityDep,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamCursor:
    """Authorise the caller for this run, then resolve where a (re)connecting client resumes.

    Runs before the response starts, so 401, 404 and 204 are real status codes, not stream
    content. The org check comes before the resume logic: a 204 would confirm that a run exists
    and has finished.
    """
    first = await resources.events.first(run_id)
    last = await resources.events.last(run_id)
    if first is None or last is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, RUN_NOT_FOUND)
    if not isinstance(first.event, RunQueued):
        # The owning org is unknowable (first event missing or trimmed): fail closed.
        log.warning("run stream has no run.queued first event", extra={"run": str(run_id)})
        raise HTTPException(status.HTTP_404_NOT_FOUND, RUN_NOT_FOUND)
    async with as_user(require_db(resources), identity) as conn:
        allowed = await is_member(conn, first.event.org_id)
    if not allowed:
        log.warning(
            "run stream refused: caller is not a member of the run's organisation",
            extra={"run": str(run_id), "user": str(identity.user_id)},
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, RUN_NOT_FOUND)

    # A malformed Last-Event-ID is not trusted as a position: replay from the start.
    after = STREAM_START
    if last_event_id is not None and parse_entry_id(last_event_id) is not None:
        after = last_event_id
    if last.is_terminal and at_or_before(last.entry_id, after):
        # The client already has the final event. 204 tells the client to stop reconnecting.
        raise HTTPException(status.HTTP_204_NO_CONTENT)
    return StreamCursor(run_id=run_id, after=after)


@router.get(
    "/{run_id}/stream",
    response_class=EventSourceResponse,
    responses={
        status.HTTP_204_NO_CONTENT: {"description": "Run already finished at Last-Event-ID"},
        status.HTTP_401_UNAUTHORIZED: {"description": "No valid access token"},
        status.HTTP_404_NOT_FOUND: {
            "description": "Unknown run, expired events, or not the caller's organisation"
        },
    },
)
async def stream_run(
    resources: ResourcesDep, cursor: Annotated[StreamCursor, Depends(stream_cursor)]
) -> AsyncIterator[ServerSentEvent]:
    """Server-sent events, one per run event, ending after `run.completed` or `run.failed`.

    Each SSE message has `event:` = the event type (`run.queued`, `run.started`, `step`,
    `run.completed`, `run.failed`), `id:` = its stream entry ID (send it back as `Last-Event-ID`
    to resume), and `data:` = the event as JSON. Step events carry `step`, `name` and `status`.
    Authorisation is checked when the stream opens; an open stream runs to the run's end.
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
