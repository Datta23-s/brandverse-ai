"""The dry-run harness shows what 0034 does to a private schema, and records and compares its runs.

0034 is replayed on a scratch database that holds every migration before it. With the harness's
scope fixed at `public` and `app` (its first version) the diff of 0034 would show nothing about
`graph_checkpoints`; the second test pins that the net under the discovered scope still would.
"""

import argparse
import sys
from pathlib import Path
from types import ModuleType

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import live_migration_dry_run

from tests.db.conftest import MIGRATIONS_DIR, PLATFORM_STUB

SCRATCH = "brandverse_dryrun_harness"
TARGET = "0034_graph_checkpoints.sql"

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def harness() -> ModuleType:
    return live_migration_dry_run


@pytest.fixture(scope="module")
def scratch_url(db_url: str) -> str:
    """A database holding every migration BEFORE the one under test, and the CLI's history table."""
    with psycopg.connect(db_url, autocommit=True) as admin:
        name = sql.Identifier(SCRATCH)
        admin.execute(sql.SQL("drop database if exists {} with (force)").format(name))
        admin.execute(sql.SQL("create database {}").format(name))
    url = make_conninfo(db_url, dbname=SCRATCH)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(PLATFORM_STUB.read_text(encoding="utf-8").encode())
        for m in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if m.name >= TARGET:
                break
            conn.execute(m.read_text(encoding="utf-8").encode())
        conn.execute("create schema if not exists supabase_migrations")
        conn.execute(
            "create table supabase_migrations.schema_migrations (version text primary key)"
        )
    return url


def args_for(migration: Path, **extra: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "migrations": [migration],
        "exercise": None,
        "read_only": False,
        "no_record": False,
        "accept_difference": False,
    }
    return argparse.Namespace(**{**base, **extra})


def test_the_private_schema_0034_creates_is_in_the_diff(
    harness: ModuleType, scratch_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert harness.run(args_for(MIGRATIONS_DIR / TARGET), scratch_url) == 0
    out = capsys.readouterr().out
    assert "-- scope: app, graph_checkpoints, public" in out
    for table in ("checkpoint_blobs", "checkpoint_migrations", "checkpoint_writes", "checkpoints"):
        assert f"relations (all schemas) + ('graph_checkpoints', '{table}', 'r')" in out
        assert f"row security + ('graph_checkpoints', '{table}', True, False)" in out
    # the API roles hold no usage on the schema, and the diff says so rather than saying nothing
    assert "schema usage + ('graph_checkpoints', 'anon', False)" in out
    assert "-- after rollback: catalog identical to before" in out


def test_the_global_net_shows_it_even_if_the_scope_were_the_old_fixed_one(
    harness: ModuleType,
    scratch_url: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        harness, "discover_schemas", lambda text, existing: set(harness.BASE_SCHEMAS)
    )
    assert harness.run(args_for(MIGRATIONS_DIR / TARGET), scratch_url) == 0
    out = capsys.readouterr().out
    assert "row security + ('graph_checkpoints'" not in out  # the scoped reads are blind, as before
    assert "relations (all schemas) + ('graph_checkpoints', 'checkpoints', 'r')" in out
    assert "schemas (all) + ('graph_checkpoints'" in out


def test_a_run_is_recorded_then_compared_and_a_difference_is_refused(
    harness: ModuleType,
    scratch_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(harness, "RECORD_DIR", tmp_path)
    monkeypatch.setattr(harness, "REPO_ROOT", tmp_path)
    args = args_for(MIGRATIONS_DIR / TARGET)

    def one_run() -> tuple[int, str]:
        assert harness.run(args, scratch_url) == 0
        output = capsys.readouterr().out
        return harness.record(args, scratch_url, output), capsys.readouterr().out

    rc, out = one_run()
    assert rc == 0 and "-- recorded:" in out
    path = tmp_path / "0034_graph_checkpoints.txt"
    assert path.read_text(encoding="utf-8").startswith("# dry run, rolled back,")

    rc, out = one_run()  # the same migration against the same state: identical, record untouched
    assert rc == 0 and "-- matches the recorded run" in out

    path.write_text(path.read_text(encoding="utf-8").replace("checkpoints'", "checkpointz'", 1))
    rc, out = one_run()  # a record that no longer matches is a difference, and is not overwritten
    assert rc == 3 and "-- DIFFERS FROM THE RECORDED RUN" in out
    assert "checkpointz'" in path.read_text(encoding="utf-8")
    args.accept_difference = True
    rc, out = one_run()
    assert rc == 0 and "checkpointz'" not in path.read_text(encoding="utf-8")
