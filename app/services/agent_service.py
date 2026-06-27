"""
app/services/agent_service.py
──────────────────────────────
Service layer for Agent resource management.

Architecture philosophy:
  The service layer sits between the API router and the database.
  It contains ALL business logic, keeping routers thin (HTTP concerns
  only) and models thin (schema definition only).

  Router  →  Service  →  ORM Model  →  Database
  (HTTP)     (logic)     (schema)       (persistence)

  Benefits:
    • Business logic is testable without spinning up an HTTP server.
    • Multiple routers (REST, WebSocket, CLI) can share the same service.
    • Async DB sessions are managed at the service level so callers
      don't need to know about SQLAlchemy internals.

Functions in this module:
  register_agent()   — create a new Agent record, return token response
  get_agent_by_id()  — fetch a single agent (raises 404 if not found)
  get_agent_by_name()— fetch by name (used for uniqueness checks)
  list_agents()      — paginated list of all agents
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from fastapi import HTTPException, status

from app.models.agent import Agent
from app.schemas.agent import AgentCreate, AgentResponse, AgentSummary, AgentListResponse
from app.schemas.token import TokenResponse
from app.auth.hashing import hash_secret
from app.auth.jwt import create_access_token


# ── Registration ──────────────────────────────────────────────────────────────

async def register_agent(
    payload: AgentCreate,
    db: AsyncSession,
) -> TokenResponse:
    """
    Register a new AI agent and issue its first JWT access token.

    Steps:
      1. Check name uniqueness — return 409 if taken.
      2. Hash the plain-text secret with bcrypt.
      3. Persist the Agent record.
      4. Mint a JWT containing the agent's ID and name.
      5. Return a TokenResponse (agent_id + access_token).

    Why return a token on registration instead of a separate login?
      For agent-to-agent or CI/CD workflows, having to make two requests
      (register, then authenticate) adds unnecessary latency and
      complexity.  Registration IS the first authentication event.

    Args:
        payload: Validated AgentCreate schema from the request body.
        db:      Async SQLAlchemy session (injected by FastAPI).

    Returns:
        TokenResponse containing agent_id and JWT access_token.

    Raises:
        HTTPException 409: An agent with the same name already exists.
    """
    # ── Step 1: Uniqueness check ──────────────────────────────────────
    # We check by name (not just relying on the DB unique constraint)
    # so we can return a friendly 409 instead of a raw IntegrityError.
    existing = await get_agent_by_name(payload.name, db)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An agent named '{payload.name}' is already registered.",
        )

    # ── Step 2: Hash the secret ───────────────────────────────────────
    # bcrypt hash is computed here in the service layer, NOT in the
    # router or the ORM model.  The ORM model only stores the hash;
    # it never sees the plain text.
    hashed = hash_secret(payload.secret)

    # ── Step 3: Persist the agent ─────────────────────────────────────
    # We create the ORM instance manually (not using model_validate)
    # because AgentCreate contains `secret` which has no corresponding
    # ORM column — we map to `hashed_secret` instead.
    new_agent = Agent(
        name=payload.name,
        description=payload.description,
        allowed_tools=payload.allowed_tools,
        hashed_secret=hashed,
        # id and created_at use their column defaults (UUID + func.now())
    )
    db.add(new_agent)
    # flush() sends the INSERT to the DB and populates new_agent.id
    # without committing the transaction.  The outer get_db() dependency
    # commits after the request handler returns successfully.
    await db.flush()
    await db.refresh(new_agent)   # re-read from DB to get server defaults

    # ── Step 4: Mint the JWT ──────────────────────────────────────────
    access_token = create_access_token(
        agent_id=new_agent.id,
        agent_name=new_agent.name,
    )

    # ── Step 5: Return token response ────────────────────────────────
    return TokenResponse(
        agent_id=new_agent.id,
        access_token=access_token,
    )


# ── Queries ───────────────────────────────────────────────────────────────────

async def get_agent_by_id(
    agent_id: str,
    db: AsyncSession,
) -> Agent:
    """
    Fetch a single Agent by its UUID.

    Args:
        agent_id: UUID string.
        db:       Async SQLAlchemy session.

    Returns:
        The Agent ORM instance.

    Raises:
        HTTPException 404: No agent with the given ID exists.
    """
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id)
    )
    agent: Agent | None = result.scalar_one_or_none()

    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found.",
        )
    return agent


async def get_agent_by_name(
    name: str,
    db: AsyncSession,
) -> Agent | None:
    """
    Fetch a single Agent by its unique name.

    Returns None instead of raising 404 — this lets callers decide
    whether a missing agent is an error (registration check) or
    expected (existence check).

    Args:
        name: Agent display name (case-sensitive, slug format).
        db:   Async SQLAlchemy session.

    Returns:
        The Agent ORM instance, or None if not found.
    """
    result = await db.execute(
        select(Agent).where(Agent.name == name)
    )
    return result.scalar_one_or_none()


async def list_agents(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 50,
) -> AgentListResponse:
    """
    Return a paginated list of all registered agents.

    Uses two queries:
      1. COUNT(*) — to populate the `total` field without loading all rows.
      2. SELECT with OFFSET/LIMIT — to load only the requested page.

    This is more efficient than loading everything and slicing in Python,
    especially as the agent count grows.

    Args:
        db:    Async SQLAlchemy session.
        skip:  Number of records to skip (for pagination).
        limit: Maximum number of records to return (default 50, max 200).

    Returns:
        AgentListResponse with agents list and total count.
    """
    # Clamp limit to a safe maximum to prevent accidental huge payloads.
    limit = min(limit, 200)

    # ── Total count ───────────────────────────────────────────────────
    count_result = await db.execute(select(func.count()).select_from(Agent))
    total: int = count_result.scalar_one()

    # ── Paginated rows ────────────────────────────────────────────────
    rows_result = await db.execute(
        select(Agent)
        .order_by(Agent.created_at.desc())   # newest agents first
        .offset(skip)
        .limit(limit)
    )
    agents = rows_result.scalars().all()

    # Convert ORM instances to Pydantic summary schemas.
    # model_validate() uses from_attributes=True under the hood.
    summaries = [AgentSummary.model_validate(a) for a in agents]

    return AgentListResponse(agents=summaries, total=total)

