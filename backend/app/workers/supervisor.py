"""Keep the worker running: restart it when it dies (owner, 1 November 2026).

Recovery fires when a worker STARTS (`app/workers/recovery.py`), and nothing started a dead one, so
a batch of 100 campaigns depended on someone noticing. This is the thing that notices: it runs the
worker as a child process, restarts it when it exits (with a growing pause, reset once a worker has
stayed up long enough to be called healthy), and stops when told to or when the worker reports that
another worker holds the lease (`app/workers/lease.py`).

THE ASSUMPTION: one worker. The lease is held by the worker, not by the supervisor, so a worker
started by hand while the supervisor's worker is alive is refused, and the supervisor does not start
a second one while its child lives.

Run it for a batch:   uv run python -m app.workers.supervisor
Stop it:              Ctrl+C (the child is terminated first)
What it does NOT do: restart the laptop, Docker, Redis or the database; a worker that cannot
start for those reasons is restarted on the same pause schedule and fails the same way, visibly,
in the log.
"""

import argparse
import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence

from app.workers.lease import LEASE_HELD_EXIT

log = logging.getLogger("brandverse.supervisor")

# The pause before restart n, in seconds; the last value repeats.
PAUSES_S: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 30.0)
HEALTHY_AFTER_S = 60.0  # a worker that stayed up this long resets the pause schedule


def supervise(
    command: Sequence[str],
    *,
    stop: threading.Event,
    pauses_s: Sequence[float] = PAUSES_S,
    healthy_after_s: float = HEALTHY_AFTER_S,
    max_starts: int | None = None,
    spawn: Callable[[Sequence[str]], subprocess.Popen[bytes]] = lambda c: subprocess.Popen(c),  # noqa: S603
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Run `command` until `stop` is set. Returns the number of times it was started."""
    starts = 0
    failures = 0
    while not stop.is_set() and (max_starts is None or starts < max_starts):
        child = spawn(command)
        starts += 1
        began = clock()
        log.warning("worker started", extra={"pid": child.pid, "start": starts})
        while child.poll() is None and not stop.is_set():
            stop.wait(0.2)
        if stop.is_set():
            _end(child)
            break
        code = child.returncode
        if code == LEASE_HELD_EXIT:
            log.error("another worker holds the lease: not restarting")
            break
        failures = 0 if clock() - began >= healthy_after_s else failures + 1
        pause = pauses_s[min(failures, len(pauses_s)) - 1] if failures else pauses_s[0]
        log.warning("worker exited", extra={"code": code, "restart_in_s": pause})
        stop.wait(pause)
    return starts


def _end(child: subprocess.Popen[bytes]) -> None:
    child.terminate()
    try:
        child.wait(timeout=30)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=30)


def main() -> int:
    argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None).parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    stop = threading.Event()
    try:
        supervise([sys.executable, "-m", "app.workers"], stop=stop)
    except KeyboardInterrupt:
        stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
