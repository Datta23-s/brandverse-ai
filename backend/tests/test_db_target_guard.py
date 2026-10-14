"""The destructive database tooling refuses a managed Supabase target (tests/db/target_guard.py).

Runs without a database: the guard must fire before any connection is opened.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.db.target_guard import (
    ManagedSupabaseTarget,
    check_not_managed_supabase,
    managed_supabase_hosts,
)

BACKEND = Path(__file__).resolve().parents[1]
# A syntactically real but non-existent project ref: if the guard ever failed open, the
# connection attempt would fail on DNS with a different error, not succeed.
DIRECT = "postgresql://postgres:pw@db.guardtestref000000.supabase.co:5432/postgres"
POOLER = (
    "postgresql://postgres.guardtestref000000:pw@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"
)


@pytest.mark.parametrize(
    "conninfo",
    [
        DIRECT,
        POOLER,
        "host=db.guardtestref000000.supabase.co dbname=postgres user=postgres",
        "postgresql://postgres@DB.GuardTestRef000000.SUPABASE.CO/postgres",
        "postgresql://postgres@db.guardtestref000000.supabase.co./postgres",
        "host=localhost,db.guardtestref000000.supabase.co dbname=postgres",
    ],
)
def test_managed_supabase_hosts_are_refused(conninfo: str) -> None:
    with pytest.raises(ManagedSupabaseTarget, match="Refusing to run"):
        check_not_managed_supabase(conninfo)


@pytest.mark.parametrize(
    "conninfo",
    [
        "postgresql://postgres:postgres@localhost:5432/postgres",  # CI service container
        "postgresql://postgres:@127.0.0.1:54824/postgres",  # embedded / local Docker
        "host=localhost dbname=postgres",
        "postgresql://postgres@notsupabase.com/postgres",  # lookalike, not a subdomain
    ],
)
def test_throwaway_hosts_are_allowed(conninfo: str) -> None:
    assert managed_supabase_hosts(conninfo) == []
    check_not_managed_supabase(conninfo)


def _env_with(url: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "REQUIRE_DB_TESTS"}
    env["TEST_DATABASE_URL"] = url
    return env


def test_db_test_session_exits_before_connecting() -> None:
    # The real harness, run end to end: the db_url fixture must stop the session.
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-m",
            "db",
            "-p",
            "no:cacheprovider",
            "tests/db/test_rls.py::test_anon_sees_nothing",
        ],
        cwd=BACKEND,
        env=_env_with(POOLER),
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, output
    assert "Refusing to run" in output
    assert "OperationalError" not in output  # no connection was attempted


def test_measurement_script_exits_before_connecting() -> None:
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "scripts/measure_memory_storage.py", "--rows", "1"],
        cwd=BACKEND,
        env=_env_with(DIRECT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Refusing to run" in output
    assert "OperationalError" not in output
