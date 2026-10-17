"""A stand-in for Supabase Auth, for the CI end-to-end job ONLY.

CI has no Supabase project and must not use live secrets (Rules.md §6: mock every external call).
This server plays the part of `{SUPABASE_URL}/auth/v1` for the browser's supabase-js client and
for the API's token verifier:

- `/.well-known/jwks.json` publishes an ES256 public key (generated at start);
- `POST /token?grant_type=password|refresh_token` signs a real ES256 access token for a known user;
- `POST /signup` answers like the real project: the user is created, confirmation is pending,
  no session (mailer_autoconfirm is off on the project);
- `GET /user`, `POST /logout`; CORS for the browser.

The API verifies these tokens with its production TokenVerifier, unchanged: only the issuer is
not Supabase. Users are fixed (E2E_A_*, E2E_B_* below) and inserted into the e2e database's
auth.users at start, as Supabase would hold them.

Usage:  uv run python -m scripts.e2e_fake_auth <database-url> [port]
"""

import json
import secrets
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
import psycopg
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from tests.db.target_guard import check_not_managed_supabase

USERS = {
    "e2e-a@ci.example.com": ("ci-password-a", uuid.UUID("aaaaaaaa-0000-4000-8000-00000000000a")),
    "e2e-b@ci.example.com": ("ci-password-b", uuid.UUID("bbbbbbbb-0000-4000-8000-00000000000b")),
}
KEY = ec.generate_private_key(ec.SECP256R1())
KID = "ci-e2e-key"
LIFETIME_S = 3600
refresh_tokens: dict[str, str] = {}  # refresh token -> email


def user_json(email: str) -> dict[str, Any]:
    return {
        "id": str(USERS[email][1]),
        "aud": "authenticated",
        "role": "authenticated",
        "email": email,
        "email_confirmed_at": "2026-10-17T00:00:00Z",
        "app_metadata": {"provider": "email"},
        "user_metadata": {},
        "created_at": "2026-10-17T00:00:00Z",
    }


def session_for(email: str, issuer: str) -> dict[str, Any]:
    now = int(time.time())
    claims = {
        "sub": str(USERS[email][1]),
        "email": email,
        "role": "authenticated",
        "aud": "authenticated",
        "iss": issuer,
        "iat": now,
        "exp": now + LIFETIME_S,
    }
    refresh = secrets.token_urlsafe(24)
    refresh_tokens[refresh] = email
    return {
        "access_token": jwt.encode(claims, KEY, algorithm="ES256", headers={"kid": KID}),
        "token_type": "bearer",
        "expires_in": LIFETIME_S,
        "expires_at": now + LIFETIME_S,
        "refresh_token": refresh,
        "user": user_json(email),
    }


class Handler(BaseHTTPRequestHandler):
    issuer = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        sys.stderr.write("fake-auth: " + (format % args) + "\n")

    def _send(self, status: int, body: dict[str, Any] | None = None) -> None:
        payload = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        data: dict[str, Any] = json.loads(self.rfile.read(length) or b"{}")
        return data

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib naming
        self._send(204)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/auth/v1/.well-known/jwks.json":
            jwk = ECAlgorithm.to_jwk(KEY.public_key(), as_dict=True)
            self._send(200, {"keys": [{**jwk, "kid": KID, "alg": "ES256", "use": "sig"}]})
        elif path == "/auth/v1/user":
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
            try:
                claims = jwt.decode(
                    token, KEY.public_key(), algorithms=["ES256"], audience="authenticated"
                )
            except jwt.InvalidTokenError:
                self._send(401, {"msg": "invalid token"})
                return
            self._send(200, user_json(claims["email"]))
        else:
            self._send(404, {"msg": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        body = self._body()
        if url.path == "/auth/v1/token":
            grant = parse_qs(url.query).get("grant_type", [""])[0]
            if grant == "password":
                email = str(body.get("email", "")).lower()
                if email in USERS and body.get("password") == USERS[email][0]:
                    self._send(200, session_for(email, self.issuer))
                else:
                    self._send(
                        400,
                        {
                            "error": "invalid_grant",
                            "error_description": "Invalid login credentials",
                            "code": "invalid_credentials",
                            "msg": "Invalid login credentials",
                        },
                    )
            elif grant == "refresh_token" and body.get("refresh_token") in refresh_tokens:
                self._send(200, session_for(refresh_tokens[body["refresh_token"]], self.issuer))
            else:
                self._send(400, {"error": "invalid_grant", "msg": "Invalid Refresh Token"})
        elif url.path == "/auth/v1/signup":
            # As on the project: created, confirmation pending, no session.
            email = str(body.get("email", "")).lower()
            self._send(
                200,
                {
                    "id": str(uuid.uuid4()),
                    "aud": "authenticated",
                    "role": "authenticated",
                    "email": email,
                    "confirmation_sent_at": "2026-10-17T00:00:00Z",
                    "app_metadata": {},
                    "user_metadata": {},
                    "identities": [{"id": "x"}],
                },
            )
        elif url.path == "/auth/v1/logout":
            self._send(204)
        else:
            self._send(404, {"msg": "not found"})


def main(database_url: str, port: int) -> None:
    check_not_managed_supabase(database_url, "the e2e database URL")
    with psycopg.connect(database_url, autocommit=True) as conn:
        for _password, user_id in USERS.values():
            conn.execute(
                "insert into auth.users (id) values (%s) on conflict do nothing", (user_id,)
            )
    Handler.issuer = f"http://127.0.0.1:{port}/auth/v1"
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    sys.stderr.write(f"fake-auth: serving {Handler.issuer} for {sorted(USERS)}\n")
    server.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 9999)
