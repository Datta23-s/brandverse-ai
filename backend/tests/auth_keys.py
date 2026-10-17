"""A test token issuer: real ES256 keys, real JWTs, a JWKS served without a network.

Tests use the production TokenVerifier unchanged; only the issuer is ours instead of Supabase's.
"""

import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from app.core.auth import JwksCache, TokenVerifier

ISSUER = "http://auth.test/auth/v1"
JWKS_URL = "http://auth.test/auth/v1/.well-known/jwks.json"


class SigningKey:
    def __init__(self, kid: str) -> None:
        self.kid = kid
        self.private = ec.generate_private_key(ec.SECP256R1())

    def jwk(self) -> dict[str, Any]:
        public: dict[str, Any] = ECAlgorithm.to_jwk(self.private.public_key(), as_dict=True)
        return {**public, "kid": self.kid, "alg": "ES256", "use": "sig"}


class TestIssuer:
    """Holds the published key set (rotate by changing `published`) and mints tokens."""

    __test__ = False  # not a pytest test class

    def __init__(self) -> None:
        self.key = SigningKey("test-key-1")
        self.published: list[SigningKey] = [self.key]
        self.jwks_requests = 0
        self.fail_jwks = False

    def token(
        self,
        sub: UUID | str,
        *,
        key: SigningKey | None = None,
        email: str | None = "user@example.test",
        role: str = "authenticated",
        aud: str = "authenticated",
        iss: str = ISSUER,
        expires_in: int = 3600,
        drop: tuple[str, ...] = (),
        **extra: Any,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "sub": str(sub),
            "role": role,
            "aud": aud,
            "iss": iss,
            "iat": now,
            "exp": now + expires_in,
            "email": email,
            **extra,
        }
        for name in drop:
            claims.pop(name, None)
        signer = key or self.key
        return jwt.encode(claims, signer.private, algorithm="ES256", headers={"kid": signer.kid})

    def _serve(self, request: httpx.Request) -> httpx.Response:
        self.jwks_requests += 1
        if self.fail_jwks:
            return httpx.Response(503)
        return httpx.Response(200, json={"keys": [k.jwk() for k in self.published]})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._serve))

    def verifier(
        self,
        *,
        ttl_s: float = 3600,
        min_refetch_interval_s: float = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> TokenVerifier:
        jwks = JwksCache(
            JWKS_URL,
            self.client(),
            ttl_s=ttl_s,
            min_refetch_interval_s=min_refetch_interval_s,
            clock=clock,
        )
        return TokenVerifier(jwks, issuer=ISSUER, audience="authenticated", leeway_s=0)
