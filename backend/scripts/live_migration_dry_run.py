"""Rolled-back dry run of migrations against the live project, on a connection modelling the apply.

    uv run python scripts/live_migration_dry_run.py ../supabase/migrations/0022_x.sql [...]
        [--exercise path/to/checks.py] [--read-only]

Rules.md §3.3: a pre-apply check runs as the role that will apply it, on a connection with no state
the real apply will not have. Twice a check ran somewhere friendlier than production and passed:
the CI platform stub (0012, a superuser revokes what Supabase's `postgres` cannot) and a dry run of
0022 on a pooled backend that already had pgvector loaded, where the real push got a fresh backend
and was refused. So this script, before running anything:

1. Opens connections through DATABASE_URL — the session pooler string the CLI push uses — until
   one is a FRESH backend: started moments ago, and with pgvector's library not yet loaded. The
   pooler hands a reused backend to a new client, so non-fresh connections are held open (forcing
   the pooler to another backend) and closed afterwards. No fresh backend: no dry run.
2. Asserts it acts as the applying role: no SET ROLE, current_user = session_user = the URL's
   user, as the CLI does. Prints whether that role is a superuser (on Supabase it is not).

Then it snapshots the catalog (functions, triggers, constraints, columns, indexes, policies,
grants, recorded migrations) on a SEPARATE connection, because reading the catalog can load
pgvector (pg_get_indexdef on the HNSW index does); re-asserts that the apply connection is still
fresh; runs the files in one transaction that is always rolled back, prints
every catalog change the files made, runs the optional `exercise(cursor)` checks inside the same
transaction, and asserts that after the rollback the catalog equals the snapshot.

Residual difference, stated: the CLI sends a migration statement by statement over the extended
protocol; this sends each file as one simple-query batch. Both run it in one transaction.
"""

import argparse
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

REPO_ROOT = Path(__file__).resolve().parents[2]
FRESH_WITHIN_S = 10.0
MAX_ATTEMPTS = 10

Snapshot = dict[str, set[tuple[Any, ...]]]

CATALOG: dict[str, str] = {
    "functions": """
        select n.nspname, p.proname, pg_get_function_identity_arguments(p.oid), p.prosecdef,
               pg_get_userbyid(p.proowner), p.proconfig::text, p.provolatile, p.proacl::text,
               md5(p.prosrc)
        from pg_proc p join pg_namespace n on n.oid = p.pronamespace
        where n.nspname in ('public', 'app')
          and not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')""",
    "triggers": """
        select t.tgrelid::regclass::text, t.tgname, pg_get_triggerdef(t.oid)
        from pg_trigger t join pg_class c on c.oid = t.tgrelid
        join pg_namespace n on n.oid = c.relnamespace
        where not t.tgisinternal and n.nspname in ('public', 'app')""",
    "constraints": """
        select c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid)
        from pg_constraint c join pg_namespace n on n.oid = c.connamespace
        where n.nspname in ('public', 'app')""",
    "columns": """
        select table_schema, table_name, column_name, data_type, is_nullable, column_default
        from information_schema.columns where table_schema in ('public', 'app')""",
    "indexes": """
        select schemaname, tablename, indexname, indexdef from pg_indexes
        where schemaname in ('public', 'app')""",
    "policies": """
        select schemaname, tablename, policyname, cmd, roles::text, qual, with_check
        from pg_policies where schemaname in ('public', 'app')""",
    "table grants": """
        select table_schema, table_name, grantee, privilege_type
        from information_schema.role_table_grants
        where table_schema in ('public', 'app')
          and grantee in ('anon', 'authenticated', 'service_role', 'PUBLIC')""",
    "column grants": """
        select table_schema, table_name, column_name, grantee, privilege_type
        from information_schema.role_column_grants
        where table_schema in ('public', 'app')
          and grantee in ('anon', 'authenticated', 'service_role', 'PUBLIC')""",
    "recorded migrations": "select version from supabase_migrations.schema_migrations",
}


def database_url() -> str:
    env = dict(
        line.split("=", 1)
        for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )
    return env["DATABASE_URL"].strip()


def fresh_connection(url: str) -> psycopg.Connection:
    """A connection on a backend with no prior state, held-open misses closed on return."""
    held: list[psycopg.Connection] = []
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            conn = psycopg.connect(url)
            row = conn.execute(
                "select pg_backend_pid(), extract(epoch from clock_timestamp() - a.backend_start), "
                "current_setting('hnsw.ef_search', true) "
                "from pg_stat_activity a where a.pid = pg_backend_pid()"
            ).fetchone()
            conn.rollback()
            if row is None:
                raise SystemExit("this backend is not in pg_stat_activity: no dry run")
            pid, age, vector_loaded = row
            fresh = float(age) < FRESH_WITHIN_S and vector_loaded is None
            print(
                f"attempt {attempt}: backend {pid}, started {float(age):.1f}s ago, pgvector "
                f"{'loaded' if vector_loaded is not None else 'not loaded'}"
                f"{' -> FRESH' if fresh else ' -> reused, held open'}"
            )
            if fresh:
                return conn
            held.append(conn)
        raise SystemExit(f"no fresh backend in {MAX_ATTEMPTS} attempts: no dry run")
    finally:
        for c in held:
            c.close()


def assert_applying_role(conn: psycopg.Connection, url: str) -> None:
    url_user = str(conninfo_to_dict(url).get("user", "")).split(".", 1)[0]  # pooler: user.ref
    row = conn.execute(
        "select current_user, session_user, rolsuper from pg_roles where rolname = current_user"
    ).fetchone()
    conn.rollback()
    if row is None:
        raise SystemExit("current_user not found in pg_roles: no dry run")
    current, session, superuser = row
    if not current == session == url_user:
        raise SystemExit(
            f"not acting as the applying role: current_user {current}, session_user {session}, "
            f"URL user {url_user}: no dry run"
        )
    print(f"acting as {current} (the CLI's role, no SET ROLE), superuser: {superuser}")


def assert_still_fresh(cur: psycopg.Cursor) -> None:
    """Immediately before the files run: nothing since connecting has loaded pgvector.

    Reading the catalog can load it (pg_get_indexdef on the HNSW index does), which is why the
    snapshot taken before the files is read on a separate connection.
    """
    row = cur.execute("select current_setting('hnsw.ef_search', true)").fetchone()
    if row is None or row[0] is not None:
        raise SystemExit("pgvector was loaded on the apply connection before the files ran")
    print("still fresh immediately before the files: pgvector not loaded")


def snapshot(cur: psycopg.Cursor) -> Snapshot:
    return {name: set(cur.execute(query).fetchall()) for name, query in CATALOG.items()}


def show_changes(before: Snapshot, during: Snapshot) -> None:
    for name in CATALOG:
        added, removed = during[name] - before[name], before[name] - during[name]
        for row in sorted(removed, key=str):
            print(f"  {name} - {row}")
        for row in sorted(added, key=str):
            print(f"  {name} + {row}")


def load_exercise(path: Path) -> Callable[[psycopg.Cursor], None]:
    spec = importlib.util.spec_from_file_location("dry_run_exercise", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load exercise module {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn: Callable[[psycopg.Cursor], None] = module.exercise
    return fn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("migrations", nargs="*", type=Path)
    ap.add_argument("--exercise", type=Path)
    ap.add_argument("--read-only", action="store_true")
    args = ap.parse_args()
    url = database_url()
    # The snapshot is read on its own connection: reading the catalog can load extensions, which
    # would spoil the apply connection's freshness.
    with psycopg.connect(url) as snap, snap.cursor() as cur:
        before = snapshot(cur)
        snap.rollback()
    print(f"recorded migrations (last 3): {sorted(before['recorded migrations'])[-3:]}")
    conn = fresh_connection(url)
    with conn:
        assert_applying_role(conn, url)
        if args.read_only or not args.migrations:
            if args.exercise:
                with conn.transaction(force_rollback=True), conn.cursor() as cur:
                    load_exercise(args.exercise)(cur)
            return 0
        with conn.transaction(force_rollback=True), conn.cursor() as cur:
            assert_still_fresh(cur)
            for m in args.migrations:
                cur.execute(m.read_text(encoding="utf-8").encode())
                print(f"applied inside the dry-run transaction: {m.name}")
            print("-- catalog changes inside the transaction")
            show_changes(before, snapshot(cur))
            if args.exercise:
                print("-- exercise")
                load_exercise(args.exercise)(cur)
        with conn.cursor() as cur:
            after = snapshot(cur)
        conn.rollback()
        if after != before:
            print("-- AFTER ROLLBACK THE CATALOG DIFFERS")
            show_changes(before, after)
            return 1
        print("-- after rollback: catalog identical to before")
    return 0


if __name__ == "__main__":
    sys.exit(main())
