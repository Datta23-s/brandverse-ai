"""The worker's execution gate: a recovery never runs beside another run.

THE HAZARD (stage-2 interim, 2026-11-04). At start-up the worker queues a `recover_pipeline` job for
every run a dead worker left. The new run's job is queued a moment later, and with `worker_max_jobs`
above one the two executed TOGETHER. The recovered run's research (eight extraction threads of
pure-Python trafilatura) and the new run's pipeline fought over the GIL and the CPU: the new run's
step 3 took 277 s instead of 8 s and ARQ killed its job at its 600 s limit. In the evaluation the
same collision would not be a failed run but something worse: a measured run whose latency silently
includes someone else's work.

THE RULE. Run jobs (`run_pipeline`, `resume_pipeline`) SHARE the gate; a recovery holds it ALONE.
A recovery therefore starts only when no run is executing, and no run starts while one recovers.
Writer-preferring: once a recovery is waiting, new run jobs queue behind it, so a steady stream of
runs cannot starve it. Waiting happens inside the job, under the job's own deadline
(`app/workers/jobs.py`), and any wait over a second is logged with its length so it can be
subtracted from a latency reading.

Scope: one worker process (app/workers/recovery.py states the same assumption). Two workers would
need a lease on the run, as that module says; the single-worker lease (`lease.py`) prevents them.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

log = logging.getLogger(__name__)

_LOG_WAITS_OVER_S = 1.0


class ExecutionGate:
    def __init__(self) -> None:
        self._cond = asyncio.Condition()
        self._shared = 0
        self._exclusive = False
        self._waiting_exclusive = 0

    @property
    def running(self) -> int:
        """Run jobs holding the gate now (for tests and logs)."""
        return self._shared

    @asynccontextmanager
    async def shared(self) -> AsyncIterator[None]:
        began = time.monotonic()
        async with self._cond:
            await self._cond.wait_for(lambda: not self._exclusive and self._waiting_exclusive == 0)
            self._shared += 1
        self._note_wait("run", began)
        try:
            yield
        finally:
            async with self._cond:
                self._shared -= 1
                self._cond.notify_all()

    @asynccontextmanager
    async def exclusive(self) -> AsyncIterator[None]:
        began = time.monotonic()
        async with self._cond:
            self._waiting_exclusive += 1
            try:
                await self._cond.wait_for(lambda: not self._exclusive and self._shared == 0)
            finally:
                self._waiting_exclusive -= 1
                self._cond.notify_all()  # a cancelled wait must not leave run jobs blocked
            self._exclusive = True
        self._note_wait("recovery", began)
        try:
            yield
        finally:
            async with self._cond:
                self._exclusive = False
                self._cond.notify_all()

    @staticmethod
    def _note_wait(kind: str, began: float) -> None:
        waited = time.monotonic() - began
        if waited >= _LOG_WAITS_OVER_S:
            log.warning(
                "%s job waited %.1f s for the execution gate (a recovery never runs beside a run)",
                kind,
                waited,
                extra={"gate_wait_s": round(waited, 1), "job_kind": kind},
            )
