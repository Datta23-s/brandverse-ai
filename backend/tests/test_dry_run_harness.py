"""The dry-run harness reads the schemas a migration touches from the migration (offline half).

`scripts/live_migration_dry_run.py` snapshotted `public` and `app` only, so 0034, whose whole
job was to create tables in a private schema, printed an empty diff and proved nothing. The scope
is now found in the migration's own text; the database half
(tests/db/test_dry_run_harness_db.py) replays 0034 and asserts the private schema shows.
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

from live_migration_dry_run import BASE_SCHEMAS, comparable, discover_schemas  # noqa: E402

MIGRATIONS = BACKEND.parent / "supabase" / "migrations"
EXISTING = {"public", "app", "auth", "extensions", "storage", "pg_catalog", "information_schema"}


def test_a_created_schema_is_in_scope_even_though_it_does_not_exist_yet() -> None:
    text = (MIGRATIONS / "0034_graph_checkpoints.sql").read_text(encoding="utf-8")
    assert "graph_checkpoints" in discover_schemas(text, EXISTING)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("alter table auth.users add column x int;", {"auth"}),
        ('select "extensions"."vector_in"(1);', {"extensions"}),
        ("grant usage on schema storage to anon;", {"storage"}),
        ("alter function f() set search_path = extensions, public;", {"extensions"}),
        ("create schema if not exists private_stuff;", {"private_stuff"}),
        ("create table t (a int); select p.x from t p;", set()),  # `p.` is an alias, not a schema
        ("select pg_catalog.now(), information_schema.x;", set()),  # system schemas are not scope
    ],
)
def test_the_scope_is_what_the_text_names(text: str, expected: set[str]) -> None:
    assert discover_schemas(text, EXISTING) == expected | set(BASE_SCHEMAS)


def test_a_nonexistent_name_that_is_not_created_is_not_scope() -> None:
    assert discover_schemas("select nonesuch.f();", EXISTING) == set(BASE_SCHEMAS)


def test_only_the_repeatable_part_of_a_run_is_compared() -> None:
    a = "attempt 1: backend 11, started 0.3s ago\n-- scope: app, public\n  x + 1\n"
    b = "attempt 4: backend 99, started 0.1s ago\n-- scope: app, public\n  x + 1\n"
    assert comparable(a) == comparable(b) == ["-- scope: app, public", "  x + 1"]
