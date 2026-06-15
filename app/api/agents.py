"""
app/api/agents.py
──────────────────
FastAPI router for Agent resource endpoints.

Router responsibility (thin layer):
  • Define HTTP method, path, status codes, and response models.
  • Validate inbound request bodies via Pydantic (automatic).
  • Extract authentication via FastAPI dependencies.
  • Delegate ALL business logic to the service layer.
  • Return serialised Pydantic response schemas.

No business logic lives here.  If you find yourself writing
"if ... raise HTTPException" in a route handler, it belongs
in the service layer instead.

Endpoints:
  POST /agents/register   — public, registers an agent, returns JWT
  GET  /agents            — protected, lists all agents
  GET  /agents/{agent_id} — protected, returns one agent's details
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.schemas.agent import AgentCreate, AgentResponse, AgentListResponse
from app.schemas.token import TokenResponse
from app.services import agent_service
from app.auth.dependencies import get_current_agent
from app.models.agent import Agent


# ── Router ────────────────────────────────────────────────────────────────────
# prefix="/agents" is applied to all routes below.
# tags=["Agents"] groups them in the OpenAPI /docs UI.
router = APIRouter(prefix="/agents", tags=["Agents"])


# ── POST /agents/register ─────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new AI agent",
    description=(
        "Creates a new agent record with the given name, description, "
        "and allowed tool list.  Returns a JWT access token that the "
        "agent must include in the Authorization header for all "
        "subsequent protected requests."
    ),
    responses={
        201: {"description": "Agent registered successfully."},
        409: {"description": "An agent with this name already exists."},
        422: {"description": "Request body validation failed."},
    },
)
async def register_agent(
    payload: AgentCreate,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Register a new agent.

    This endpoint is intentionally public (no auth dependency) because
    it IS the first authentication step — you cannot have a token before
    you register.

    In a multi-tenant production system you might protect this behind
    an admin-only API key to control who can register new agents.
    """
    return await agent_service.register_agent(payload, db)


# ── GET /agents ────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=AgentListResponse,
    status_code=status.HTTP_200_OK,
    summary="List all registered agents",
    description=(
        "Returns a paginated list of all registered agents. "
        "Requires a valid JWT in the Authorization header."
    ),
    responses={
        200: {"description": "Paginated agent list."},
        401: {"description": "Missing or invalid JWT."},
    },
)
async def list_agents(
    skip: int = Query(default=0, ge=0, description="Records to skip (pagination offset)."),
    limit: int = Query(default=50, ge=1, le=200, description="Max records to return."),
    # get_current_agent validates the JWT and confirms the agent exists.
    # The underscore signals to linters that we don't use the value —
    # we only call this dep for its auth side-effect.
    _current_agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> AgentListResponse:
    """
    List all agents (paginated).

    Example:
        GET /agents?skip=0&limit=10
        Authorization: Bearer eyJhbGci...
    """
    return await agent_service.list_agents(db, skip=skip, limit=limit)


# ── GET /agents/{agent_id} ────────────────────────────────────────────────────

@router.get(
    "/{agent_id}",
    response_model=AgentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a single agent by ID",
    description=(
        "Returns full details for the agent with the given UUID. "
        "Requires a valid JWT."
    ),
    responses={
        200: {"description": "Agent details."},
        401: {"description": "Missing or invalid JWT."},
        404: {"description": "Agent not found."},
    },
)
async def get_agent(
    agent_id: str,
    _current_agent: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    """
    Retrieve a single agent's full profile.

    Note: Any authenticated agent can look up any other agent's profile.
    In a production multi-tenant system you would add an ownership check:
        if agent_id != _current_agent.id:
            raise HTTPException(403, "Not authorised to view this agent.")
    For this educational MVP, all authenticated agents share a namespace.
    """
    agent = await agent_service.get_agent_by_id(agent_id, db)
    # model_validate() uses from_attributes=True to read ORM attributes.
    return AgentResponse.model_validate(agent)