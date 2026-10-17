"""Local ES256 verification against a cached JWKS, including key rotation and its rate limit."""

import asyncio
from uuid import uuid4

import jwt
import pytest

from app.errors import AuthenticationFailed
from tests.auth_keys import SigningKey, TestIssuer

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_valid_token_gives_the_identity() -> None:
    issuer = TestIssuer()
    user = uuid4()
    identity = await issuer.verifier().verify(issuer.token(user, email="a@example.test"))
    assert identity.user_id == user
    assert identity.email == "a@example.test"
    assert identity.claims["role"] == "authenticated"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"expires_in": -10}, "expired"),
        ({"aud": "anon"}, "invalid token"),
        ({"iss": "http://evil.test/auth/v1"}, "invalid token"),
        ({"role": "anon"}, "not a signed-in user"),
        ({"role": "service_role"}, "not a signed-in user"),
        ({"drop": ("sub",)}, "invalid token"),
        ({"drop": ("exp",)}, "invalid token"),
        ({"sub": "not-a-uuid"}, "invalid subject"),
    ],
)
async def test_bad_claims_are_refused(kwargs: dict[str, object], reason: str) -> None:
    issuer = TestIssuer()
    sub = kwargs.pop("sub", uuid4())
    with pytest.raises(AuthenticationFailed, match=reason):
        await issuer.verifier().verify(issuer.token(sub, **kwargs))  # type: ignore[arg-type]


async def test_hs256_and_none_are_refused_before_any_key_lookup() -> None:
    issuer = TestIssuer()
    verifier = issuer.verifier()
    claims = {"sub": str(uuid4()), "role": "authenticated", "aud": "authenticated"}
    hs256 = jwt.encode(claims, "a-shared-secret-of-thirty-two-bytes!", algorithm="HS256")
    unsigned = jwt.encode(claims, None, algorithm="none")
    for token in (hs256, unsigned):
        with pytest.raises(AuthenticationFailed, match="unexpected signing algorithm"):
            await verifier.verify(token)
    assert issuer.jwks_requests == 0


@pytest.mark.parametrize("token", ["", "not.a.jwt", "a.b"])
async def test_malformed_tokens_are_refused(token: str) -> None:
    with pytest.raises(AuthenticationFailed):
        await TestIssuer().verifier().verify(token)


async def test_known_kid_with_a_bad_signature_is_refused_without_refetching() -> None:
    issuer = TestIssuer()
    verifier = issuer.verifier()
    await verifier.verify(issuer.token(uuid4()))  # warms the cache
    impostor = SigningKey(issuer.key.kid)  # same kid, different private key
    with pytest.raises(AuthenticationFailed, match="invalid token"):
        await verifier.verify(issuer.token(uuid4(), key=impostor))
    assert issuer.jwks_requests == 1


async def test_rotation_an_unknown_kid_refetches_once_and_then_verifies() -> None:
    issuer = TestIssuer()
    clock = Clock()
    verifier = issuer.verifier(clock=clock)
    await verifier.verify(issuer.token(uuid4()))
    # Supabase rotates: a new key is published and starts signing tokens.
    new_key = SigningKey("test-key-2")
    issuer.published = [issuer.key, new_key]
    clock.now += 60  # beyond the refetch interval since the first fetch
    identity = await verifier.verify(issuer.token(uuid4(), key=new_key))
    assert identity.user_id is not None
    assert issuer.jwks_requests == 2


async def test_unknown_kid_is_refused_after_one_refetch() -> None:
    issuer = TestIssuer()
    clock = Clock()
    verifier = issuer.verifier(clock=clock)
    await verifier.verify(issuer.token(uuid4()))
    clock.now += 60
    with pytest.raises(AuthenticationFailed, match="unknown key"):
        await verifier.verify(issuer.token(uuid4(), key=SigningKey("never-published")))
    assert issuer.jwks_requests == 2


async def test_a_flood_of_unknown_kids_causes_at_most_one_refetch_per_interval() -> None:
    issuer = TestIssuer()
    clock = Clock()
    verifier = issuer.verifier(clock=clock, min_refetch_interval_s=30)
    await verifier.verify(issuer.token(uuid4()))
    clock.now += 60
    bad = [issuer.token(uuid4(), key=SigningKey(f"forged-{i}")) for i in range(50)]

    async def attempt(token: str) -> None:
        with pytest.raises(AuthenticationFailed):
            await verifier.verify(token)

    await asyncio.gather(*(attempt(t) for t in bad))
    assert issuer.jwks_requests == 2  # the first fetch + ONE refetch for all 50
    clock.now += 10  # still inside the interval
    await attempt(bad[0])
    assert issuer.jwks_requests == 2
    clock.now += 30  # interval passed: one more refetch allowed
    await attempt(bad[1])
    assert issuer.jwks_requests == 3


async def test_the_key_set_is_refreshed_after_its_ttl() -> None:
    issuer = TestIssuer()
    clock = Clock()
    verifier = issuer.verifier(clock=clock, ttl_s=3600)
    await verifier.verify(issuer.token(uuid4()))
    clock.now += 100
    await verifier.verify(issuer.token(uuid4()))
    assert issuer.jwks_requests == 1  # cached
    clock.now += 3600
    await verifier.verify(issuer.token(uuid4()))
    assert issuer.jwks_requests == 2


async def test_jwks_outage_keeps_serving_with_the_cached_keys() -> None:
    issuer = TestIssuer()
    clock = Clock()
    verifier = issuer.verifier(clock=clock, ttl_s=3600)
    await verifier.verify(issuer.token(uuid4()))
    issuer.fail_jwks = True
    clock.now += 4000  # past the TTL: a refresh is attempted and fails
    identity = await verifier.verify(issuer.token(uuid4()))
    assert identity.user_id is not None
    assert issuer.jwks_requests == 2


async def test_no_keys_at_all_is_refused_not_crashed() -> None:
    issuer = TestIssuer()
    issuer.fail_jwks = True
    with pytest.raises(AuthenticationFailed, match="signing keys unavailable"):
        await issuer.verifier().verify(issuer.token(uuid4()))
