"""Rolled-back dry run of migrations against the live project, on a connection modelling the apply.

    uv run python scripts/live_migration_dry_run.py ../supabase/migrations/0022_x.sql [...]
        [--exercise path/to/checks.py] [--read-only] [--url URL] [--no-record]
        [--accept-difference]

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

Then it snapshots the catalog on a SEPARATE connection, because reading the catalog can load
pgvector (pg_get_indexdef on the HNSW index does). The snapshot's SCOPE is read from the
migrations' own text (`discover_schemas`: every schema the files name or create, plus `public` and
`app`), never from a fixed list: 0034 created its tables in a private schema and a snapshot of
`public` and `app` alone printed an empty diff for it, proving nothing. Three more reads cover
every schema whatever the text says (schemas with their ACLs, every relation, recorded
migrations), so an object made where the text does not name it (dynamic SQL) still appears as a
new relation. Within the scope it reads functions, triggers, constraints, columns, indexes,
policies, row security, table and column grants and schema usage for the API roles. It then re-
asserts that the apply connection is still fresh; runs the files in one transaction that is always
rolled back, prints every catalog change the files made, runs the optional `exercise(cursor)`
checks inside the same transaction, and asserts that after the rollback the catalog equals the
snapshot.

The raw output of a run is kept in git beside the migrations (`supabase/dry-runs/<migration>.txt`,
with the migration's sha256, the commit and the target host). A later run of the same migration is
compared with it: identical changes print "matches the recorded run"; any difference prints the
diff, exits 3 and leaves the record alone until `--accept-difference` replaces it. So "the dry run
still matches" is a comparison, not a recollection. `--url` points the run at another database
(the harness's own test uses a scratch one); `--no-record` skips the record.

Residual difference, stated: the CLI sends a migration statement by statement over the extended
protocol; this sends each file as one simple-query batch. Both run it in one transaction.
"""

import argparse
import contextlib
import difflib
import hashlib
import importlib.util
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

REPO_ROOT = Path(__file__).resolve().parents[2]
RECORD_DIR = REPO_ROOT / "supabase" / "dry-runs"
FRESH_WITHIN_S = 10.0
MAX_ATTEMPTS = 10

Snapshot = dict[str, set[tuple[Any, ...]]]

# Queries that read the SCOPE: the schemas the migrations touch, found in the migrations' own text
# (discover_schemas), never from a fixed list. 0034 created tables in a private schema and the first
# version of this harness, which read `public` and `app` only, printed an empty diff for it.
SCOPED: dict[str, str] = {
    "functions": """
        select n.nspname, p.proname, pg_get_function_identity_arguments(p.oid), p.prosecdef,
               pg_get_userbyid(p.proowner), p.proconfig::text, p.provolatile, p.proacl::text,
               md5(p.prosrc)
        from pg_proc p join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = any(%(s)s)
          and not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')""",
    "triggers": """
        select t.tgrelid::regclass::text, t.tgname, pg_get_triggerdef(t.oid)
        from pg_trigger t join pg_class c on c.oid = t.tgrelid
        join pg_namespace n on n.oid = c.relnamespace
        where not t.tgisinternal and n.nspname = any(%(s)s)""",
    "constraints": """
        select c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid)
        from pg_constraint c join pg_namespace n on n.oid = c.connamespace
        where n.nspname = any(%(s)s)""",
    "columns": """
        select table_schema, table_name, column_name, data_type, is_nullable, column_default
        from information_schema.columns where table_schema = any(%(s)s)""",
    "indexes": """
        select schemaname, tablename, indexname, indexdef from pg_indexes
        where schemaname = any(%(s)s)""",
    "policies": """
        select schemaname, tablename, policyname, cmd, roles::text, qual, with_check
        from pg_policies where schemaname = any(%(s)s)""",
    "row security": """
        select n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where c.relkind in ('r', 'p') and n.nspname = any(%(s)s)""",
    "table grants": """
        select table_schema, table_name, grantee, privilege_type
        from information_schema.role_table_grants
        where table_schema = any(%(s)s)
          and grantee in ('anon', 'authenticated', 'service_role', 'PUBLIC')""",
    "column grants": """
        select table_schema, table_name, column_name, grantee, privilege_type
        from information_schema.role_column_grants
        where table_schema = any(%(s)s)
          and grantee in ('anon', 'authenticated', 'service_role', 'PUBLIC')""",
    "schema usage": """
        select n.nspname, r.rolname, has_schema_privilege(r.rolname, n.oid, 'usage')
        from pg_namespace n
        cross join (values ('anon'), ('authenticated'), ('service_role')) r(rolname)
        where n.nspname = any(%(s)s)""",
}

# Queries over EVERY schema, whatever the migrations' text says: the net under discover_schemas, so
# an object a migration makes where its text does not name (dynamic SQL in a DO block, a schema
# named through a variable) still shows in the diff, as a new schema or a new relation.
GLOBAL: dict[str, str] = {
    "schemas (all)": """
        select nspname, pg_get_userbyid(nspowner), nspacl::text from pg_namespace
        where nspname !~ '^pg_' and nspname <> 'information_schema'""",
    "relations (all schemas)": """
        select n.nspname, c.relname, c.relkind
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname !~ '^pg_' and n.nspname <> 'information_schema'""",
    "recorded migrations": "select version from supabase_migrations.schema_migrations",
}
CATALOG = {**SCOPED, **GLOBAL}
BASE_SCHEMAS = ("public", "app")


def discover_schemas(sql_text: str, existing: set[str]) -> set[str]:
    """The schemas a migration names: `x.` qualifiers, `schema x`, `search_path`, and creations.

    Over-inclusion is harmless (a bigger scope only shows more); a miss is the blind spot this
    exists to close, so it reads the whole text, comments and string literals included, and keeps a
    name only if it is a schema that exists or that the files create. `public` and `app` always
    count.
    """
    created = {
        m.lower()
        for m in re.findall(
            r'create\s+schema\s+(?:if\s+not\s+exists\s+)?"?([A-Za-z_][\w$]*)"?', sql_text, re.I
        )
    }
    named = {m.lower() for m in re.findall(r'"?([A-Za-z_][\w$]*)"?\s*\.', sql_text)}
    named |= {
        m.lower()
        for m in re.findall(
            r'\bschema\s+(?:if\s+(?:not\s+)?exists\s+)?"?([A-Za-z_][\w$]*)"?', sql_text, re.I
        )
    }
    for path in re.findall(r"search_path\s*(?:=|to)\s*([^;]*)", sql_text, re.I):
        named |= {m.lower() for m in re.findall(r'"?([A-Za-z_][\w$]*)"?', path)}
    known = {e for e in existing if not e.startswith("pg_") and e != "information_schema"}
    return (named & (known | created)) | created | set(BASE_SCHEMAS)


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


def existing_schemas(cur: psycopg.Cursor) -> set[str]:
    return {r[0] for r in cur.execute("select nspname from pg_namespace").fetchall()}


def snapshot(cur: psycopg.Cursor, schemas: list[str]) -> Snapshot:
    return {
        name: set(cur.execute(query, {"s": schemas}).fetchall()) for name, query in CATALOG.items()
    }


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


class Tee:
    """Stdout that also keeps what it wrote: the raw output is what gets recorded."""

    def __init__(self) -> None:
        self.parts: list[str] = []

    def write(self, text: str) -> int:
        sys.__stdout__.write(text)  # type: ignore[union-attr]
        self.parts.append(text)
        return len(text)

    def flush(self) -> None:
        sys.__stdout__.flush()  # type: ignore[union-attr]


def run(args: argparse.Namespace, url: str) -> int:
    texts = [m.read_text(encoding="utf-8") for m in args.migrations]
    # The snapshot is read on its own connection: reading the catalog can load extensions, which
    # would spoil the apply connection's freshness.
    with psycopg.connect(url) as snap, snap.cursor() as cur:
        schemas = sorted(discover_schemas("\n".join(texts), existing_schemas(cur)))
        before = snapshot(cur, schemas)
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
            print(f"-- scope: {', '.join(schemas)}")
            print("-- catalog changes inside the transaction")
            show_changes(before, snapshot(cur, schemas))
            if args.exercise:
                print("-- exercise")
                load_exercise(args.exercise)(cur)
        with conn.cursor() as cur:
            after = snapshot(cur, schemas)
        conn.rollback()
        if after != before:
            print("-- AFTER ROLLBACK THE CATALOG DIFFERS")
            show_changes(before, after)
            return 1
        print("-- after rollback: catalog identical to before")
    return 0


def comparable(text: str) -> list[str]:
    """The part of a run that must repeat: from the scope line on (no pids, ages or timestamps)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("-- scope:")), len(lines))
    return lines[start:]


def record_path(migrations: list[Path]) -> Path:
    return RECORD_DIR / ("+".join(m.stem for m in migrations) + ".txt")


def commit() -> str:
    out = subprocess.run(  # noqa: S603 - fixed argv
        ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    return out.stdout.strip() or "unknown"


def record(args: argparse.Namespace, url: str, output: str) -> int:
    """Compare with the recorded run of these migrations, or write the first record."""
    path = record_path(args.migrations)
    if path.exists():
        old = comparable(path.read_text(encoding="utf-8"))
        new = comparable(output)
        if old == new:
            print(f"-- matches the recorded run ({path.relative_to(REPO_ROOT).as_posix()})")
            return 0
        print(f"-- DIFFERS FROM THE RECORDED RUN ({path.relative_to(REPO_ROOT).as_posix()}):")
        for line in difflib.unified_diff(old, new, "recorded", "this run", lineterm=""):
            print(line)
        if not args.accept_difference:
            print("-- the record is unchanged; --accept-difference replaces it once read")
            return 3
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    host = conninfo_to_dict(url).get("host", "?")
    shas = "; ".join(
        f"{m.name} sha256 {hashlib.sha256(m.read_bytes()).hexdigest()[:16]}"
        for m in args.migrations
    )
    header = (
        f"# dry run, rolled back, {datetime.now(UTC):%Y-%m-%d %H:%M UTC}; commit {commit()}; "
        f"target {host}\n# {shas}\n"
    )
    path.write_text(header + output, encoding="utf-8", newline="\n")
    print(f"-- recorded: {path.relative_to(REPO_ROOT).as_posix()}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("migrations", nargs="*", type=Path)
    ap.add_argument("--exercise", type=Path)
    ap.add_argument("--read-only", action="store_true")
    ap.add_argument("--url", help="default: DATABASE_URL from .env")
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--accept-difference", action="store_true")
    args = ap.parse_args()
    url = args.url or database_url()
    tee = Tee()
    with contextlib.redirect_stdout(tee):  # type: ignore[type-var]
        rc = run(args, url)
    if rc != 0 or args.read_only or not args.migrations or args.no_record:
        return rc
    return record(args, url, "".join(tee.parts))


if __name__ == "__main__":
    sys.exit(main())
