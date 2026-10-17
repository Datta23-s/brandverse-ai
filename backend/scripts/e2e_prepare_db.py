"""Create a throwaway database with the real migrations, for the CI end-to-end job.

Applies the Supabase platform stub and every migration in order, exactly as the database test
suite does (tests/db/conftest.py). Refuses a managed Supabase host before connecting: this drops
and recreates a database.

Usage:  uv run python -m scripts.e2e_prepare_db postgresql://postgres:postgres@127.0.0.1:5432/postgres
Prints the URL of the prepared database.
"""

import sys

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.core.config import REPO_ROOT
from tests.db.target_guard import check_not_managed_supabase

DB_NAME = "brandverse_e2e"
STUB = REPO_ROOT / "backend" / "tests" / "db" / "supabase_platform_stub.sql"
MIGRATIONS = sorted((REPO_ROOT / "supabase" / "migrations").glob("*.sql"))


def main(admin_url: str) -> str:
    check_not_managed_supabase(admin_url, "the e2e database URL")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        name = sql.Identifier(DB_NAME)
        admin.execute(sql.SQL("drop database if exists {} with (force)").format(name))
        admin.execute(sql.SQL("create database {}").format(name))
    p = conninfo_to_dict(admin_url)
    url = (
        f"postgresql://{p['user']}:{p.get('password', '')}"
        f"@{p['host']}:{p.get('port', 5432)}/{DB_NAME}"
    )
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(STUB.read_text(encoding="utf-8").encode())
        for migration in MIGRATIONS:
            conn.execute(migration.read_text(encoding="utf-8").encode())
    return url


if __name__ == "__main__":
    print(main(sys.argv[1]))
