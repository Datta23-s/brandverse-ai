"""Verifying Supabase access tokens locally: ES256 against the project's cached JWKS.

The API never calls Supabase Auth per request. It fetches the project's public key set
(`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`), caches it, and verifies each token's signature
and claims itself.

Key rotation: a token signed with a key the cache does not know (unknown `kid`) triggers ONE
refetch before it is rejected, so a rotation does not break every request until a restart.
That refetch is rate-limited (`jwks_min_refetch_interval_s`): a flood of tokens with invented
`kid`s causes at most one request to Supabase per interval. A token with a known `kid` and a bad
signature is rejected without any refetch.

Known limitation (Architecture.md §11, "Authentication"): local verification cannot see a
revoked session. A signed-out user's access token stays valid until it expires.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import jwt
from jwt import PyJWK
from pydantic import BaseModel, ValidationError

from app.errors import AuthenticationFailed

log = logging.getLogger(__name__)

ALGORITHM = "ES256"
_REQUIRED_CLAIMS = ["exp", "iat", "sub", "aud", "iss"]


class _JwksDocument(BaseModel):
    keys: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class Identity:
    """A verified caller."""

    user_id: UUID
    email: str | None
    # The verified claims, as PostgREST would set them for row-level security.
    claims: Mapping[str, Any]


class JwksCache:
    def __init__(
        self,
        jwks_url: str,
        client: httpx.AsyncClient,
        *,
        ttl_s: float,
        min_refetch_interval_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = jwks_url
        self._client = client
        self._ttl_s = ttl_s
        self._min_interval_s = min_refetch_interval_s
        self._clock = clock
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float | None = None  # last successful fetch
        self._attempted_at: float | None = None  # last attempt, successful or not
        self._lock = asyncio.Lock()
        self.fetch_count = 0

    async def key_for(self, kid: str) -> PyJWK:
        if self._fetched_at is None or self._clock() - self._fetched_at > self._ttl_s:
            await self._refresh(rate_limited=self._fetched_at is not None)
        key = self._keys.get(kid)
        if key is None:
            # Unknown kid: perhaps a rotation. Refetch once (rate-limited), then decide.
            await self._refresh(rate_limited=True)
            key = self._keys.get(kid)
        if key is None:
            raise AuthenticationFailed("token signed with an unknown key")
        return key

    async def _refresh(self, *, rate_limited: bool) -> None:
        async with self._lock:
            now = self._clock()
            if (
                rate_limited
                and self._attempted_at is not None
                and now - self._attempted_at < self._min_interval_s
            ):
                return
            self._attempted_at = now
            self.fetch_count += 1
            try:
                response = await self._client.get(self._url, timeout=5.0)
                response.raise_for_status()
                document = _JwksDocument.model_validate_json(response.content)
                keys = {
                    str(jwk["kid"]): PyJWK(jwk, algorithm=ALGORITHM)
                    for jwk in document.keys
                    if jwk.get("kid") and jwk.get("kty") == "EC" and jwk.get("use", "sig") == "sig"
                }
            except (httpx.HTTPError, ValidationError, jwt.PyJWKError) as e:
                # Keep serving with the keys we have; a later request retries after the interval.
                log.exception("JWKS refresh failed", extra={"jwks_url": self._url})
                if not self._keys:
                    raise AuthenticationFailed("signing keys unavailable") from e
                return
            self._keys = keys
            self._fetched_at = now
            log.info("JWKS refreshed", extra={"key_ids": sorted(keys)})


class TokenVerifier:
    def __init__(self, jwks: JwksCache, *, issuer: str, audience: str, leeway_s: float) -> None:
        self._jwks = jwks
        self._issuer = issuer
        self._audience = audience
        self._leeway_s = leeway_s

    async def verify(self, token: str) -> Identity:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as e:
            raise AuthenticationFailed("malformed token") from e
        # Pin the algorithm before looking at anything else: no "none", no HS256 confusion.
        if header.get("alg") != ALGORITHM:
            raise AuthenticationFailed("unexpected signing algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise AuthenticationFailed("token has no key id")
        key = await self._jwks.key_for(kid)
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_s,
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.ExpiredSignatureError as e:
            raise AuthenticationFailed("token expired") from e
        except jwt.InvalidTokenError as e:
            raise AuthenticationFailed("invalid token") from e
        if claims.get("role") != "authenticated":
            raise AuthenticationFailed("not a signed-in user's token")
        try:
            user_id = UUID(str(claims["sub"]))
        except ValueError as e:
            raise AuthenticationFailed("invalid subject") from e
        email = claims.get("email")
        return Identity(
            user_id=user_id, email=email if isinstance(email, str) else None, claims=claims
        )


def build_verifier(
    *,
    supabase_url: str,
    client: httpx.AsyncClient,
    audience: str,
    ttl_s: float,
    min_refetch_interval_s: float,
    leeway_s: float,
) -> TokenVerifier:
    base = supabase_url.rstrip("/")
    jwks = JwksCache(
        f"{base}/auth/v1/.well-known/jwks.json",
        client,
        ttl_s=ttl_s,
        min_refetch_interval_s=min_refetch_interval_s,
    )
    return TokenVerifier(jwks, issuer=f"{base}/auth/v1", audience=audience, leeway_s=leeway_s)
