"""
app/auth/dependencies.py
─────────────────────────
FastAPI dependency injection functions for authentication and
authorisation.

How FastAPI dependencies work:
  A dependency is any callable that FastAPI can call and inject into
  route handlers via `Depends()`.  Dependencies can themselves depend
  on other dependencies, forming a tree.

  Route handler signature:
      @router.post("/run")
      async def run_agent(
          payload: RunRequest,
          current_agent: Agent = Depends(get_current_agent),
          db: AsyncSession = Depends(get_db),
      ):
          ...

  FastAPI calls get_current_agent() before the handler, and if it
  raises HTTPException the handler is never called.

Dependency chain in this file:
  get_current_agent
    └─► get_current_agent_id        ← extracts + validates JWT
          └─► oauth2_scheme         ← extracts Bearer token from header
    └─► db session (get_db)         ← async DB session
    └─► agent lookup by ID          ← ensures agent still exists in DB

This layering means:
  • Token extraction is reusable independently of DB lookup.
  • Tests can override individual dependencies cheaply.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.auth.jwt import decode_access_token
from app.schemas.token import TokenPayload
from app.models.agent import Agent


# ── OAuth2 scheme ─────────────────────────────────────────────────────────────
# OAuth2PasswordBearer does two things:
#   1. Adds "BearerAuth" to the OpenAPI /docs security section.
#   2. Extracts the token from the Authorization: Bearer <token> header.
#
# tokenUrl points to the endpoint that ISSUES tokens.
# It's used by Swagger UI's "Authorize" dialog to show the correct flow.
# POST /agents/register is our token-issuing endpoint for this MVP.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/agents/register")


# ── Reusable 401 exception ────────────────────────────────────────────────────
# RFC 9110 §15.5.2: 401 Unauthorized SHOULD include a WWW-Authenticate header.
# FastAPI/Starlette adds this automatically when we pass `headers=`.
_CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials.",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_agent_id(
    token: str = Depends(oauth2_scheme),
) -> str:
    """
    Dependency: extract and validate the JWT, return the agent's UUID.

    This is a lightweight dependency that doesn't touch the database.
    Use it when you only need the agent_id (e.g. for filtering audit logs)
    and want to avoid the extra DB round-trip.

    Args:
        token: Raw JWT extracted from the Authorization header by oauth2_scheme.

    Returns:
        The agent UUID stored in the token's `sub` claim.

    Raises:
        HTTPException 401: Token is missing, malformed, expired, or has no `sub`.
    """
    try:
        payload: TokenPayload = decode_access_token(token)
    except JWTError:
        # JWTError covers: invalid signature, expired token, malformed token.
        raise _CREDENTIALS_EXCEPTION

    agent_id = payload.sub
    if agent_id is None:
        # Valid JWT structure but missing `sub` claim — treat as invalid.
        raise _CREDENTIALS_EXCEPTION

    return agent_id


async def get_current_agent(
    agent_id: str = Depends(get_current_agent_id),
    db: AsyncSession = Depends(get_db),
) -> Agent:
    """
    Dependency: validate JWT AND confirm the agent exists in the database.

    This is the primary auth dependency for protected endpoints.
    It ensures:
      1. The JWT is cryptographically valid (delegated to get_current_agent_id).
      2. The agent the token was issued for still exists in the database.
         (Handles the case where an agent was deleted after token issuance.)

    Args:
        agent_id: Extracted from the JWT by get_current_agent_id.
        db:       Async DB session from get_db.

    Returns:
        The live Agent ORM instance for the authenticated agent.

    Raises:
        HTTPException 401: Agent not found in the database.
    """
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id)
    )
    agent: Agent | None = result.scalar_one_or_none()

    if agent is None:
        # The token was valid but the agent no longer exists.
        # Return 401 (not 404) to avoid leaking information about which
        # agent IDs exist in the system.
        raise _CREDENTIALS_EXCEPTION

    return agent


async def get_current_agent_optional(
    token: str | None = Depends(
        OAuth2PasswordBearer(tokenUrl="/agents/register", auto_error=False)
    ),
    db: AsyncSession = Depends(get_db),
) -> Agent | None:
    """
    Optional auth dependency — returns None instead of raising 401.

    Use this for endpoints that serve different responses to authenticated
    vs unauthenticated callers (e.g. a public health endpoint that shows
    extra detail when authenticated).

    Returns:
        Agent ORM instance if a valid token is provided, None otherwise.
    """
    if token is None:
        return None

    try:
        payload: TokenPayload = decode_access_token(token)
    except JWTError:
        return None

    if payload.sub is None:
        return None

    result = await db.execute(
        select(Agent).where(Agent.id == payload.sub)
    )
    return result.scalar_one_or_none()