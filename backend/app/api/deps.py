"""Request dependencies: process-wide resources created once in the app's lifespan, and the
signed-in caller."""

from dataclasses import dataclass
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.auth import Identity, TokenVerifier
from app.core.config import Settings
from app.core.run_events import RunEventLog
from app.errors import AuthenticationFailed


@dataclass(frozen=True, slots=True)
class Resources:
    settings: Settings
    redis: ArqRedis
    events: RunEventLog
    db_pool: AsyncConnectionPool[AsyncConnection] | None
    verifier: TokenVerifier | None


def get_resources(request: Request) -> Resources:
    resources: Resources = request.app.state.resources
    return resources


ResourcesDep = Annotated[Resources, Depends(get_resources)]

# auto_error=False: a missing header is answered by `current_identity` with our own 401.
_bearer = HTTPBearer(auto_error=False, description="A Supabase access token")

_UNAUTHENTICATED = {"WWW-Authenticate": "Bearer"}


async def current_identity(
    resources: ResourcesDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Identity:
    """The signed-in caller, from a verified Supabase access token; 401 otherwise."""
    if resources.verifier is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "authentication not configured")
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in required", _UNAUTHENTICATED)
    try:
        return await resources.verifier.verify(credentials.credentials)
    except AuthenticationFailed as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e), _UNAUTHENTICATED) from e


IdentityDep = Annotated[Identity, Depends(current_identity)]


def require_db(resources: Resources) -> AsyncConnectionPool[AsyncConnection]:
    if resources.db_pool is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database not configured")
    return resources.db_pool
