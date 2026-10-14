"""Database fixtures: a fresh database with the real migrations applied, and per-test transactions.

Needs a PostgreSQL with pgvector and pg_trgm (CI: pgvector/pgvector:pg17 service container).
Set TEST_DATABASE_URL to a superuser connection on a throwaway server; a managed Supabase host is
refused before any connection (target_guard.py). With REQUIRE_DB_TESTS=1
(set in CI) a missing database is a failure, not a skip, so these tests can never pass silently.
"""

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from tests.db.target_guard import ManagedSupabaseTarget, check_not_managed_supabase

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
PLATFORM_STUB = Path(__file__).with_name("supabase_platform_stub.sql")
TEST_DB_NAME = "brandverse_rls_test"


@dataclass(frozen=True)
class Seed:
    users: dict[str, UUID]
    org_a: UUID
    org_b: UUID
    brand_a: UUID
    brand_b: UUID
    product_a: UUID
    run_a: UUID
    campaign_a: UUID
    org_practice: UUID
    brand_practice: UUID
    campaign_practice: UUID


@pytest.fixture(scope="session")
def db_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        if os.environ.get("REQUIRE_DB_TESTS") == "1":
            pytest.fail("REQUIRE_DB_TESTS=1 but TEST_DATABASE_URL is not set")
        pytest.skip("TEST_DATABASE_URL not set; database tests skipped locally")

    # These tests drop and recreate a database: never against the Supabase project. Stops the
    # whole session before any connection is opened.
    try:
        check_not_managed_supabase(url)
    except ManagedSupabaseTarget as e:
        pytest.exit(str(e), returncode=pytest.ExitCode.USAGE_ERROR)

    with psycopg.connect(url, autocommit=True) as admin:
        name = sql.Identifier(TEST_DB_NAME)
        admin.execute(sql.SQL("drop database if exists {} with (force)").format(name))
        admin.execute(sql.SQL("create database {}").format(name))

    test_url = make_conninfo(url, dbname=TEST_DB_NAME)
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert migrations, f"no migrations found in {MIGRATIONS_DIR}"
    with psycopg.connect(test_url, autocommit=True) as conn:
        conn.execute(PLATFORM_STUB.read_text(encoding="utf-8").encode())
        for migration in migrations:
            # Applied exactly as written; nothing is substituted.
            conn.execute(migration.read_text(encoding="utf-8").encode())
    return test_url


@pytest.fixture(scope="session")
def seed(db_url: str) -> Seed:
    names = [
        "owner_a",
        "editor_a",
        "viewer_a",
        "approver_a",
        "approver_b",
        "agency",
        "second_scorer",
        "outsider",
        "admin",
    ]
    users = {n: uuid4() for n in names}
    org_a, org_b, org_practice = uuid4(), uuid4(), uuid4()
    with psycopg.connect(db_url, autocommit=True) as c:
        for uid in users.values():
            c.execute("insert into auth.users (id) values (%s)", (uid,))
        c.execute(
            "insert into sectors (id, label, regulated) values "
            "('health-supplements', 'Health supplements', true), ('footwear', 'Footwear', false)"
        )
        c.execute(
            "insert into organisations (id, name, is_practice) "
            "values (%s, 'A', false), (%s, 'B', false), (%s, 'Practice', true)",
            (org_a, org_b, org_practice),
        )
        for user, org, role in [
            ("owner_a", org_a, "owner"),
            ("editor_a", org_a, "editor"),
            ("viewer_a", org_a, "viewer"),
            ("approver_a", org_a, "approver"),
            ("approver_b", org_b, "approver"),
            # One user, two organisations (agency persona, PRD.md §3).
            ("agency", org_a, "editor"),
            ("agency", org_b, "viewer"),
            # Calibration (rubric §6): both scorers approve in the practice org; the second
            # scorer holds no role anywhere else.
            ("approver_a", org_practice, "approver"),
            ("second_scorer", org_practice, "approver"),
        ]:
            c.execute(
                "insert into org_members (org_id, user_id, role) values (%s, %s, %s)",
                (org, users[user], role),
            )

        def one(query: str, params: tuple[object, ...]) -> UUID:
            row = c.execute(query, params).fetchone()
            assert row is not None
            value: UUID = row[0]
            return value

        brand_a = one(
            "insert into brands (org_id, name, sector) values (%s, 'BA', 'footwear') returning id",
            (org_a,),
        )
        brand_b = one(
            "insert into brands (org_id, name, sector) values (%s, 'BB', 'footwear') returning id",
            (org_b,),
        )
        product_a = one(
            "insert into products (org_id, brand_id, name) values (%s, %s, 'PA') returning id",
            (org_a, brand_a),
        )
        run_a = one(
            "insert into runs (org_id, product_id, request) values (%s, %s, '{}') returning id",
            (org_a, product_a),
        )
        campaign_a = one(
            "insert into campaigns (org_id, run_id) values (%s, %s) returning id", (org_a, run_a)
        )
        brand_p = one(
            "insert into brands (org_id, name) values (%s, 'BP') returning id", (org_practice,)
        )
        product_p = one(
            "insert into products (org_id, brand_id, name) values (%s, %s, 'PP') returning id",
            (org_practice, brand_p),
        )
        run_p = one(
            "insert into runs (org_id, product_id, request) values (%s, %s, '{}') returning id",
            (org_practice, product_p),
        )
        campaign_practice = one(
            "insert into campaigns (org_id, run_id) values (%s, %s) returning id",
            (org_practice, run_p),
        )
    return Seed(
        users,
        org_a,
        org_b,
        brand_a,
        brand_b,
        product_a,
        run_a,
        campaign_a,
        org_practice,
        brand_p,
        campaign_practice,
    )


class Tx:
    """One test's transaction. Statements run as the superuser until `as_user`/`as_role`."""

    def __init__(self, conn: psycopg.Connection, seed: Seed) -> None:
        self.conn = conn
        self.seed = seed

    def execute(
        self, query: str, params: tuple[object, ...] | dict[str, object] = ()
    ) -> list[tuple[object, ...]]:
        cur = self.conn.execute(query, params)
        return cur.fetchall() if cur.description else []

    def as_user(self, user: str, **extra_claims: str) -> None:
        """Act as an authenticated user, the way PostgREST does: role + request.jwt.claims GUC.

        The JWT carries no organisation; membership comes from org_members keyed on `sub`
        (auth.uid()).
        `extra_claims` lets a test forge claims the policies must ignore."""
        claims = {"sub": str(self.seed.users[user]), "role": "authenticated", **extra_claims}
        self.set_claims(claims)

    def set_claims(self, claims: dict[str, str]) -> None:
        """Act as `authenticated` with exactly these JWT claims."""
        self.conn.execute("set local role authenticated")
        self.conn.execute(
            "select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),)
        )

    def as_service_role(self) -> None:
        self.conn.execute("set local role service_role")
        self.conn.execute(
            "select set_config('request.jwt.claims', %s, true)",
            (json.dumps({"role": "service_role"}),),
        )


@pytest.fixture
def tx(db_url: str, seed: Seed) -> Iterator[Tx]:
    """Each test runs in a transaction that is always rolled back."""
    with psycopg.connect(db_url) as conn:
        try:
            yield Tx(conn, seed)
        finally:
            conn.rollback()
