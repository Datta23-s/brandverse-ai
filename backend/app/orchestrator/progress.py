"""Which pipeline step each run in this process last started.

Read when a job runs out of time (`Pipeline.fail_timed_out`), to say which step it died on. Held in
memory on purpose: it is only ever read by the process that is executing the run, at the moment it
gives up on it; a dead process has no one to ask, and the run's checkpoint is the durable record of
the rest. Never used for a decision about the run, only for its failure message.
"""

from uuid import UUID

_last: dict[UUID, tuple[int, str]] = {}


def started(run_id: UUID, step: int, name: str) -> None:
    _last[run_id] = (step, name)


def last_started(run_id: UUID) -> tuple[int, str] | None:
    return _last.get(run_id)


def forget(run_id: UUID) -> None:
    _last.pop(run_id, None)
