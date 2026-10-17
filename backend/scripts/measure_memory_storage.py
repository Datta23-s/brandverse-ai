"""One-off measurement: on-disk size of memory_items per row, including the HNSW index.

Applies the test platform stub and supabase/migrations to a fresh database, inserts a known
number of rows with a real vector(768) embedding (random values: incompressible, like real
embeddings) and fixed-length content, vacuums, and prints heap, TOAST and per-index sizes and
pg_total_relation_size, overall and per row. Report-only; run in CI's database job against
pgvector/pgvector:pg17 (Architecture.md §11 records the result).

Usage: TEST_DATABASE_URL=... python scripts/measure_memory_storage.py [--rows N] [--chars C]
"""

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from tests.db.target_guard import (  # noqa: E402 — needs the path above
    ManagedSupabaseTarget,
    check_not_managed_supabase,
)

STUB = REPO_ROOT / "backend" / "tests" / "db" / "supabase_platform_stub.sql"
DB_NAME = "brandverse_storage_measure"
ORG = "00000000-0000-0000-0000-000000000001"
BRAND = "00000000-0000-0000-0000-000000000002"

INSERT = """
insert into memory_items (org_id, brand_id, kind, content, embedding, embedding_model,
  embedding_version, sector, source_url, provenance, confidence, freshness_days, approved)
select %(org)s, %(brand)s, 'insight',
  left((select string_agg('w' || (random() * 2000)::int || 'ord', ' ')
        from generate_series(1, %(words)s) where g > 0), %(chars)s),
  (select array_agg(random()::real) from generate_series(1, 768) where g > 0),
  'embedding-model', 'v1', 'footwear',
  'https://www.example.com/product/page-' || g, 'run:' || gen_random_uuid(), 0.80, 90, true
from generate_series(1, %(rows)s) g
"""

SIZES = """
select c.relname, am.amname, pg_relation_size(c.oid)
from pg_index i join pg_class c on c.oid = i.indexrelid join pg_am am on am.oid = c.relam
where i.indrelid = 'memory_items'::regclass order by c.relname
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=10_000)
    ap.add_argument("--chars", type=int, default=400)
    ap.add_argument("--migrations", type=Path, default=REPO_ROOT / "supabase" / "migrations")
    args = ap.parse_args()

    url = os.environ["TEST_DATABASE_URL"]
    try:
        check_not_managed_supabase(url)  # drops and recreates a database: never Supabase
    except ManagedSupabaseTarget as e:
        raise SystemExit(str(e)) from None
    with psycopg.connect(url, autocommit=True) as admin:
        name = sql.Identifier(DB_NAME)
        admin.execute(sql.SQL("drop database if exists {} with (force)").format(name))
        admin.execute(sql.SQL("create database {}").format(name))

    with psycopg.connect(make_conninfo(url, dbname=DB_NAME), autocommit=True) as c:
        c.execute(STUB.read_text(encoding="utf-8").encode())
        for migration in sorted(args.migrations.glob("*.sql")):
            c.execute(migration.read_text(encoding="utf-8").encode())
        c.execute("insert into organisations (id, name) values (%s, 'o')", (ORG,))
        # 'footwear' is seeded by migration 0018.
        c.execute(
            "insert into brands (id, org_id, name, sector) values (%s, %s, 'b', 'footwear')",
            (BRAND, ORG),
        )
        params = {
            "org": ORG,
            "brand": BRAND,
            "rows": args.rows,
            "chars": args.chars,
            "words": args.chars // 5 + 1,
        }
        c.execute(INSERT, params)
        c.execute("vacuum analyze memory_items")
        version = c.execute("select version()").fetchone()
        vector = c.execute(
            "select extversion from pg_extension where extname = 'vector'"
        ).fetchone()
        row = c.execute(
            "select pg_relation_size('memory_items'), "
            "coalesce(pg_total_relation_size(reltoastrelid), 0), "
            "pg_total_relation_size('memory_items'), "
            "(select avg(pg_column_size(embedding)) from memory_items) "
            "from pg_class where relname = 'memory_items'"
        ).fetchone()
        indexes = c.execute(SIZES).fetchall()

    if row is None:
        raise SystemExit("memory_items not found")
    heap, toast, total, emb = row
    n = args.rows
    lines = [
        f"server: {version[0] if version else '?'}",
        f"pgvector: {vector[0] if vector else 'not installed'}",
        f"rows={n} content_chars={args.chars} embedding_bytes={float(emb):.0f}",
        f"heap        {heap:>12,} B  {heap / n:>8.0f} B/row",
        f"toast       {toast:>12,} B  {toast / n:>8.0f} B/row",
    ]
    for relname, amname, size in indexes:
        lines.append(f"{amname:<6}{relname[:40]:<40} {size:>12,} B  {size / n:>8.0f} B/row")
    lines.append(f"pg_total_relation_size {total:>12,} B  {total / n:>8.0f} B/row")
    print("\n".join(lines))
    publish(lines)


def publish(lines: list[str]) -> None:
    """On GitHub Actions, also emit the report as one notice annotation (readable from the public
    check-runs API without authentication) and as a job summary. One annotation, not one per
    line: GitHub keeps at most 10 notice annotations per step."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    body = "\n".join(lines)
    escaped = body.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::notice title=memory_items storage::{escaped}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### memory_items storage\n\n```\n{body}\n```\n")


if __name__ == "__main__":
    main()
