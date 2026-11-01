"""The single-worker lease (owner, 1 November 2026).

THE ASSUMPTION, written down: **there is ONE worker process.** Recovery (`app/workers/recovery.py`)
takes a worker that has just started to mean the previous one died; a second live worker would have
its runs "recovered" out from under it and its in-flight calls marked orphaned. The project runs one
worker on one laptop (Architecture.md section 11). This module makes the assumption a refusal.

The lease is a TCP port bound on 127.0.0.1 and held for the life of the process. The operating
system releases it the moment the process dies, however it dies (a kill included), so there is no
stale-lock file to clean up and no clock to trust. A second worker, started by hand or by a second
supervisor, cannot bind the port and exits with `LEASE_HELD_EXIT`, which the supervisor reads as
"stop restarting: someone else is the worker".
"""

import socket

LEASE_HELD_EXIT = 75  # EX_TEMPFAIL: not an error of this process, try again later


class LeaseHeld(RuntimeError):
    """Another process holds the worker lease."""


def acquire_worker_lease(port: int) -> socket.socket:
    """Bind the lease port; keep the returned socket open for as long as the lease is wanted."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: no second bind, even with reuse set
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
    except OSError as e:
        sock.close()
        raise LeaseHeld(f"another worker holds the lease on 127.0.0.1:{port}") from e
    return sock
