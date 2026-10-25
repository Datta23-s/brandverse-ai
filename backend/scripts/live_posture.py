"""Read-only posture check of a LIVE project: the repo's own posture tests, run against it.

    uv run python scripts/live_posture.py --url "$DATABASE_URL"

The delete/truncate, function, minimal-role and `llm_calls` column checks in
`tests/db/test_table_posture.py` state what the project must be. CI runs them against a database
built from the migrations, which shows what the migrations produce; this runs the same checks
against the project itself, which shows drift nobody made through a migration (a dashboard change,
a platform change, a grant that moved). Run it after every live migration and periodically.

It is a separate tool from the mutating DB suite and does not touch that suite's refusal of managed
hosts (`tests/db/target_guard.py`): that guard stays exactly as it is. This tool earns its access
to a live project by enforcing, itself, that it can only read:

1. The URL is an explicit argument; no environment variable is consulted.
2. The session is opened `SET TRANSACTION READ ONLY`, and the tool then ASSERTS it: it reads
   `transaction_read_only` back and tries a write (a temporary table), which must be refused. If
   either check fails nothing further runs.
3. Every statement passes `check_read_only` before it reaches the connection: one plain SELECT,
   with no locking clause, no INTO, and no function that writes or waits. The tests see only
   `execute`; anything else they call is an AttributeError, not a way around the filter.
4. The transaction is always rolled back.

Exit status 0 only if every check passed and at least one ran.
"""

import argparse
import itertools
import re
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_FORBIDDEN = re.compile(
    r"\b(?:into|for\s+(?:update|share|no\s+key\s+update|key\s+share)|set_config|nextval|setval"
    r"|pg_sleep\w*|pg_advisory\w*|pg_notify|pg_terminate_backend|pg_cancel_backend"
    r"|pg_reload_conf|pg_rotate_logfile|lo_\w+|dblink\w*|copy)\b",
    re.IGNORECASE,
)


class NotARead(Exception):
    """A statement this tool will not send."""


def check_read_only(statement: str) -> None:
    """Raise `NotARead` unless `statement` is one plain SELECT. The database enforces read-only
    too (the session is read only); this is the tool's own refusal, before the server is asked."""
    body = statement.strip().rstrip(";").strip()
    if not re.match(r"select\b", body, re.IGNORECASE):
        raise NotARead(f"not a SELECT: {body[:60]!r}")
    if "--" in body or "/*" in body:
        raise NotARead(f"comments are not allowed: {body[:60]!r}")
    if ";" in body:
        raise NotARead(f"more than one statement: {body[:60]!r}")
    if found := _FORBIDDEN.search(body):
        raise NotARead(f"{found.group(0)!r} is not allowed in a read: {body[:60]!r}")


class ReadOnlyTx:
    """What the posture tests receive as `tx`: `execute`, and nothing else."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def execute(
        self, query: str, params: tuple[object, ...] | dict[str, object] = ()
    ) -> list[tuple[object, ...]]:
        check_read_only(query)
        cur = self._conn.execute(query, params)  # type: ignore[call-overload]
        return cur.fetchall() if cur.description else []


def open_read_only(url: str) -> psycopg.Connection:
    """Connect and prove the session cannot write, or raise."""
    conn = psycopg.connect(url, autocommit=False)
    try:
        conn.execute("set transaction read only")
        (state,) = conn.execute("show transaction_read_only").fetchone() or (None,)
        if state != "on":
            raise RuntimeError(f"transaction_read_only is {state!r}, not 'on': not proceeding")
        conn.execute("savepoint write_probe")
        try:
            conn.execute("create temporary table posture_write_probe (x int)")
        except psycopg.errors.ReadOnlySqlTransaction:
            conn.execute("rollback to savepoint write_probe")
        else:
            raise RuntimeError("a write succeeded in the read-only session: not proceeding")
    except BaseException:
        conn.rollback()
        conn.close()
        raise
    return conn


def _cases(fn: Callable[..., Any]) -> Iterator[dict[str, object]]:
    """The keyword arguments pytest would pass, from the test's `parametrize` marks."""
    marks = [m for m in getattr(fn, "pytestmark", []) if m.name == "parametrize"]
    names = [m.args[0] for m in marks]
    for values in itertools.product(*(m.args[1] for m in marks)):
        yield dict(zip(names, values, strict=True))


def run_posture(tx: ReadOnlyTx) -> tuple[int, list[str]]:
    from tests.db import test_table_posture as suite

    ran, failures = 0, []
    for name in sorted(vars(suite)):
        fn = getattr(suite, name)
        if not (name.startswith("test_") and callable(fn)):
            continue
        for kwargs in _cases(fn):
            ran += 1
            label = name + (f"[{', '.join(map(str, kwargs.values()))}]" if kwargs else "")
            try:
                fn(tx, **kwargs)
            except Exception as e:  # noqa: BLE001 — every failure is reported, none stops the run
                failures.append(f"{label}: {type(e).__name__}: {e}")
    return ran, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", required=True, help="the live project's connection string")
    args = parser.parse_args()
    host = urlparse(args.url).hostname
    conn = open_read_only(args.url)
    try:
        print(f"read-only session confirmed on {host}")
        ran, failures = run_posture(ReadOnlyTx(conn))
    finally:
        conn.rollback()
        conn.close()
    for f in failures:
        print("MISMATCH", f)
    print(f"{ran - len(failures)} of {ran} posture checks match")
    return 0 if ran and not failures else 1


if __name__ == "__main__":
    sys.exit(main())
