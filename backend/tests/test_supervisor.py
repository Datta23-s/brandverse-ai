"""The supervisor restarts a dead worker; the lease keeps the worker single (owner, 1 Nov 2026).

Recovery fires only when a worker starts; before this nothing started a dead one. The restart is
tested on real child processes (killed from outside), and the lease on real sockets.
"""

import socket
import subprocess
import sys
import threading

import pytest

from app.workers.lease import LEASE_HELD_EXIT, LeaseHeld, acquire_worker_lease
from app.workers.supervisor import supervise

SLEEP = [sys.executable, "-c", "import time; time.sleep(120)"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_a_second_holder_of_the_lease_is_refused_and_a_dead_one_releases_it() -> None:
    port = free_port()
    first = acquire_worker_lease(port)
    with pytest.raises(LeaseHeld, match="another worker"):
        acquire_worker_lease(port)
    first.close()  # the process dying does exactly this, however it dies
    acquire_worker_lease(port).close()


def test_a_killed_worker_is_started_again() -> None:
    stop = threading.Event()
    children: list[subprocess.Popen[bytes]] = []

    def spawn(command: object) -> subprocess.Popen[bytes]:
        child = subprocess.Popen(SLEEP)  # noqa: S603 - a fixed command
        children.append(child)
        return child

    done: list[int] = []
    t = threading.Thread(
        target=lambda: done.append(
            supervise(SLEEP, stop=stop, pauses_s=(0.05,), healthy_after_s=0.0, spawn=spawn)  # type: ignore[arg-type]
        )
    )
    t.start()
    try:
        _wait_for(lambda: len(children) == 1)
        children[0].kill()  # the worker is killed outright
        _wait_for(lambda: len(children) == 2)  # and the supervisor starts another
        assert children[1].poll() is None and children[1].pid != children[0].pid
    finally:
        stop.set()
        t.join(timeout=30)
        for c in children:
            if c.poll() is None:
                c.kill()
    assert done == [2]


def test_the_supervisor_stops_restarting_when_another_worker_holds_the_lease() -> None:
    stop = threading.Event()
    refused = [sys.executable, "-c", f"import sys; sys.exit({LEASE_HELD_EXIT})"]
    starts = supervise(refused, stop=stop, pauses_s=(0.01,), max_starts=10)
    assert starts == 1  # one try, then it stands down instead of restart-looping


def test_a_crash_loop_is_paced_and_a_long_lived_worker_resets_the_pace() -> None:
    stop = threading.Event()
    pauses: list[float] = []
    ticks = iter(range(0, 10_000, 1))

    class Dead:
        pid = 1
        returncode = 1

        def poll(self) -> int:
            return 1

    original_wait = stop.wait

    def recording_wait(timeout: float | None = None) -> bool:
        if timeout is not None and timeout >= 1:
            pauses.append(timeout)
        return original_wait(0)

    stop.wait = recording_wait  # type: ignore[method-assign]
    supervise(
        ["x"],
        stop=stop,
        pauses_s=(1.0, 2.0, 5.0),
        healthy_after_s=60.0,
        max_starts=5,
        spawn=lambda c: Dead(),  # type: ignore[arg-type,return-value]
        clock=lambda: float(next(ticks)),
    )
    assert pauses == [1.0, 2.0, 5.0, 5.0, 5.0]  # growing, then capped


def _wait_for(condition: object, seconds: float = 20.0) -> None:
    import time

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():  # type: ignore[operator]
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached in time")
