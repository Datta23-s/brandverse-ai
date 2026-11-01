"""The event loop psycopg needs on Windows, enforced instead of remembered (owner, 2026-11-01).

psycopg's async mode refuses Windows' default `ProactorEventLoop` (Architecture.md section 11). The
rule was written down and a script still started on the Proactor loop: the error surfaced only as
a pool timeout ten seconds later ("couldn't get a connection"), which looks like a database problem.
Two enforcements now stand behind it:

1. `require_selector_loop()` is called wherever a database connection is made (`create_db_pool`,
   `open_checkpointer`): on the wrong loop it raises at once, naming the cause and the fix.
2. `tests/test_event_loop.py` fails if any `asyncio.run(` in `app/` or `scripts/` does not pass
   `loop_factory=asyncio.SelectorEventLoop`, so a new entry point cannot be added with the default.
"""

import asyncio


class WrongEventLoop(RuntimeError):
    """The running loop cannot drive psycopg's async mode."""


def require_selector_loop() -> None:
    """Raise `WrongEventLoop` if the RUNNING loop is a Windows ProactorEventLoop. A no-op when no
    loop is running (a synchronous caller: nothing to check yet) and on any other platform."""
    proactor = getattr(asyncio, "ProactorEventLoop", None)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if proactor is not None and isinstance(loop, proactor):
        raise WrongEventLoop(
            "this code is running on a ProactorEventLoop, which psycopg's async mode refuses "
            "(Architecture.md section 11). Start the program with "
            "asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)."
        )
