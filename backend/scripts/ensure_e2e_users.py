"""Create (once) the two fixed end-to-end test users on the live Supabase project, each with one
organisation, and measure the project's access-token lifetime.

Decided 2026-10-16: E2E against real Supabase uses two fixed, confirmed users — never a new user
per run, because organisations and memberships can never be deleted by application roles (0010)
and would accumulate. Permanent footprint: 2 users + 2 organisations.

- Users are created through the Auth admin API with the SECRET key, confirmed (no email sent).
  The secret key is read from Settings here, in a local script; it never reaches a request path
  or frontend code (Rules.md P2).
- Passwords are generated once and kept in the repository's `.env.e2e` (git-ignored, like .env).
- Each user's organisation is created through OUR API (POST /api/v1/organisations →
  app.create_organisation, migration 0012), so the live function is exercised. The API must be
  running locally with SUPABASE_URL set.
- Each organisation the script's users own is then flagged `is_test` (migration 0020), with the
  backend's own database role, so every evaluation read excludes it and everything in it (the
  run fixture included) by the flag, never by a remembered id. The organisation is found through
  the user's own membership, not by a stored id.

Usage (from backend/, API running):  uv run python -m scripts.ensure_e2e_users
Idempotent: a second run changes nothing and reprints the state.
"""

import asyncio
import base64
import json
import secrets
import sys
from pathlib import Path
from typing import Any

import httpx
import psycopg

from app.core.config import REPO_ROOT, get_settings

E2E_FILE: Path = REPO_ROOT / ".env.e2e"
API = "http://127.0.0.1:8000"
USERS = {
    "A": ("e2e-a@brandverse-e2e.example.com", "E2E Org A"),
    "B": ("e2e-b@brandverse-e2e.example.com", "E2E Org B"),
}


# Only an organisation this user OWNS is flagged: the script cannot mark anyone else's as a test.
FLAG_TEST_ORG_SQL = """
update public.organisations o set is_test = true
where o.id = %(org)s
  and exists (select 1 from public.org_members m
              where m.org_id = o.id and m.user_id = %(user)s and m.role = 'owner'
                and m.revoked_at is null)
returning o.name, o.is_test
"""


def read_env_file() -> dict[str, str]:
    if not E2E_FILE.exists():
        return {}
    pairs = (line.split("=", 1) for line in E2E_FILE.read_text("utf-8").splitlines() if "=" in line)
    return {k.strip(): v.strip() for k, v in pairs}


def write_env_file(values: dict[str, str]) -> None:
    lines = ["# Fixed E2E test users on the live Supabase project. Never commit (Rules.md P1)."]
    lines += [f"{k}={v}" for k, v in sorted(values.items())]
    E2E_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def claims_of(token: str) -> dict[str, Any]:
    """Read (not verify) a JWT's claims; for reporting the lifetime only."""
    payload = token.split(".")[1]
    data: dict[str, Any] = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    return data


async def main() -> int:
    s = get_settings()
    if (
        s.supabase_url is None
        or s.supabase_secret_key is None
        or s.supabase_publishable_key is None
        or s.database_url is None
    ):
        print(
            "SUPABASE_URL, SUPABASE_SECRET_KEY, SUPABASE_PUBLISHABLE_KEY and DATABASE_URL must be "
            "set in .env"
        )
        return 1
    base = str(s.supabase_url).rstrip("/")
    admin = {"apikey": s.supabase_secret_key.get_secret_value()}
    public = {"apikey": s.supabase_publishable_key}
    env = read_env_file()

    async with httpx.AsyncClient(timeout=20) as http:
        listed = await http.get(f"{base}/auth/v1/admin/users?per_page=1000", headers=admin)
        listed.raise_for_status()
        existing = {u["email"]: u for u in listed.json()["users"]}

        for label, (email, org_name) in USERS.items():
            password = env.get(f"E2E_{label}_PASSWORD") or secrets.token_urlsafe(24)
            if email not in existing:
                r = await http.post(
                    f"{base}/auth/v1/admin/users",
                    headers=admin,
                    json={"email": email, "password": password, "email_confirm": True},
                )
                r.raise_for_status()
                print(f"{label}: created {email}")
            elif f"E2E_{label}_PASSWORD" not in env:
                # The user exists but this machine has no password for it: set a new one.
                r = await http.put(
                    f"{base}/auth/v1/admin/users/{existing[email]['id']}",
                    headers=admin,
                    json={"password": password},
                )
                r.raise_for_status()
                print(f"{label}: reset password for {email}")
            else:
                print(f"{label}: exists {email}")
            env[f"E2E_{label}_EMAIL"] = email
            env[f"E2E_{label}_PASSWORD"] = password
            write_env_file(env)

            # Sign in exactly as the frontend does: password grant with the publishable key.
            token_r = await http.post(
                f"{base}/auth/v1/token?grant_type=password",
                headers=public,
                json={"email": email, "password": password},
            )
            token_r.raise_for_status()
            access = token_r.json()["access_token"]
            claims = claims_of(access)
            print(
                f"{label}: access token lifetime {claims['exp'] - claims['iat']} s "
                f"(expires_in {token_r.json()['expires_in']} s), alg/iss checked by our API next"
            )

            bearer = {"Authorization": f"Bearer {access}"}
            me = await http.get(f"{API}/api/v1/me", headers=bearer)
            me.raise_for_status()  # proves the API verifies a real project token
            orgs = me.json()["organisations"]
            if not orgs:
                created = await http.post(
                    f"{API}/api/v1/organisations", headers=bearer, json={"name": org_name}
                )
                created.raise_for_status()
                orgs = [created.json()]
                print(f"{label}: created organisation {orgs[0]['name']} ({orgs[0]['id']})")
            env[f"E2E_{label}_ORG_ID"] = orgs[0]["id"]
            write_env_file(env)
            print(f"{label}: organisations {[(o['name'], o['roles']) for o in orgs]}")
            async with await psycopg.AsyncConnection.connect(
                s.database_url.get_secret_value()
            ) as conn:  # commits on exit
                for org in orgs:
                    cur = await conn.execute(
                        FLAG_TEST_ORG_SQL, {"org": org["id"], "user": me.json()["user_id"]}
                    )
                    print(f"{label}: flagged is_test {await cur.fetchall()}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop))
