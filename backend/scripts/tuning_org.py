"""Create the TUNING organisation for chunk F's live runs, in a throwaway local database.

Live model calls made while developing and tuning log to a THROWAWAY database, never to the live
project, whose `llm_calls` and `runs` rows can never be deleted (Architecture.md section 4.4). The
live ledger cannot see them, so they are tallied by hand. This script recreates that database
(`brandverse_tuning`), applies the migrations, and creates:

- a practice organisation (`is_practice`), the only kind the gateway lets near the strong tier;
- one user who is its owner AND its approver (an owner does not hold approval, Rules.md T6, so the
  approver role is a second membership), so a run started through the API can be decided;
- a non-regulated brand and product with verified claims (the same fixture as chunk B's live run).

It prints the ids and the settings a run needs. It does NOT set `LLM_STRONG_TIER_ORG_ID`: the strong
tier stays off ("no strong tier without asking", owner standing budget); the line to enable it for
this organisation alone is printed for the day the owner says yes. No model is called here.

Usage (from backend/):  PYTHONPATH=. uv run python -m scripts.tuning_org
"""

import json
import sys
from uuid import UUID, uuid4

import psycopg

from scripts.e2e_prepare_db import main as prepare_database
from tests.db.target_guard import managed_supabase_hosts

ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:55432/postgres"
DB_NAME = "brandverse_tuning"
CLAIMS = ("Upper made of recycled mesh", "Weighs 240 g per shoe")


def main() -> int:
    url = prepare_database(ADMIN_URL, DB_NAME)  # refuses a managed host, recreates the database
    if managed_supabase_hosts(url):
        print("refusing to use the live project")
        return 1
    user = uuid4()
    with psycopg.connect(url, autocommit=True) as c:

        def one(query: str, params: tuple[object, ...]) -> UUID:
            row = c.execute(query, params).fetchone()
            if row is None:
                raise RuntimeError("insert returned no row")
            value: UUID = row[0]
            return value

        c.execute("insert into auth.users (id) values (%s)", (user,))
        org = one(
            "insert into organisations (name, is_practice) values ('Tuning', true) returning id", ()
        )
        for role in ("owner", "editor", "approver"):
            c.execute(
                "insert into org_members (org_id, user_id, role) values (%s, %s, %s)",
                (org, user, role),
            )
        brand = one(
            "insert into brands (org_id, name, sector) values (%s, 'Tuning brand', 'footwear') "
            "returning id",
            (org,),
        )
        product = one(
            "insert into products (org_id, brand_id, name, category) "
            "values (%s, %s, 'Tuning shoe', 'running shoes') returning id",
            (org, brand),
        )
        with c.transaction():
            c.execute(
                "select set_config('role', 'authenticated', true), "
                "set_config('request.jwt.claims', %s, true)",
                (json.dumps({"sub": str(user), "role": "authenticated"}),),
            )
            for claim in CLAIMS:
                c.execute(
                    "insert into product_claims (org_id, product_id, claim, claim_type, "
                    "evidence_url, created_by) values (%s, %s, %s, 'verified', "
                    "'https://example.invalid/spec', 'human')",
                    (org, product, claim),
                )
    print(f"database       {DB_NAME} (local, throwaway; recreated)")
    print(f"organisation   {org}   (is_practice, owner + editor + approver: user {user})")
    print(f"product        {product}")
    print("approvals here are kind 'calibration' unless this is the tuning organisation:")
    print(
        f"  LLM_STRONG_TIER_ORG_ID={org}   <- NOT set; strong tier stays off until the owner asks"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
