"""
app/api/interactions.py
────────────────────────
FastAPI router for agent-to-agent interaction endpoints.

Endpoints:
  POST /agent-interactions            — record a new interaction
  GET  /agent-interactions            — list all interactions (paginated)
  GET  /agent-interactions/{agent_id} — interactions where agent is source or target

All endpoints require JWT authentication.
All business logic is in app/services/interaction_service.py.

This router is intentionally thin:
  • Declares HTTP method, path, status code, response model.
  • Validates request body via Pydantic (automatic).
  • Injects auth and DB dependencies.
  • Delegates to the service layer.
  • Returns serialised Pydantic schemas.
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.auth.dependencies import get_current_agent
from app.models.agent import Agent
from app.schemas.interaction import (
    InteractionCreate,
    InteractionResponse,
    InteractionListResponse,
)
from app.services import interaction_service

router = APIRouter(prefix="/agent-interactions", tags=["Agent Interactions"])


@router.post(
    "",
    response_model=InteractionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record an agent-to-agent interaction",
    description=(
        "Creates an interaction record between two registered agents. "
        "Automatically emits an agent_handoff audit event so the interaction "
        "is visible in GET /audit/logs without any additional steps. "
        "Both source_agent_id and target_agent_id must be UUIDs of agents "
        "already registered via POST /agents/register."
    ),
    responses={
        201: {"description": "Interaction created and audit event emitted."},
        401: {"description": "Missing or invalid JWT."},
        404: {"description": "Source or target agent not found."},
        422: {"description": "Validation error or self-interaction."},
    },
)
async def create_interaction(
    payload: InteractionCreate,
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> InteractionResponse:
    """
    Record a directed interaction between two agents.

    Use this endpoint to model multi-agent communication patterns:
      - handoff:    agent A hands off control to agent B
      - delegation: agent A assigns a sub-task to agent B
      - request:    agent A asks agent B for information
      - response:   agent A replies to agent B's prior request

    The interaction is immediately visible in:
      GET /agent-interactions
      GET /agent-interactions/{agent_id}
      GET /audit/logs  (as an agent_handoff event)
    """
    return await interaction_service.create_interaction(payload, db)


@router.get(
    "",
    response_model=InteractionListResponse,
    status_code=status.HTTP_200_OK,
    summary="List all agent interactions",
    description=(
        "Returns a paginated list of all recorded agent-to-agent interactions, "
        "ordered by creation time descending (newest first). "
        "Each interaction includes the source and target agent names."
    ),
    responses={
        200: {"description": "Paginated interaction list."},
        401: {"description": "Missing or invalid JWT."},
    },
)
async def list_interactions(
    skip:  int = Query(default=0, ge=0, description="Pagination offset."),
    limit: int = Query(default=50, ge=1, le=200, description="Page size."),
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> InteractionListResponse:
    """
    Retrieve all agent interactions (paginated).

    Example:
        GET /agent-interactions?skip=0&limit=25
        Authorization: Bearer <token>
    """
    return await interaction_service.list_interactions(db, skip=skip, limit=limit)


@router.get(
    "/{agent_id}",
    response_model=InteractionListResponse,
    status_code=status.HTTP_200_OK,
    summary="List interactions for a specific agent",
    description=(
        "Returns all interactions where the given agent is either the source "
        "(initiated the interaction) or the target (received it). "
        "Returns an empty list (not 404) if the agent has no interactions yet."
    ),
    responses={
        200: {"description": "Paginated interaction list for the agent."},
        401: {"description": "Missing or invalid JWT."},
    },
)
async def list_interactions_for_agent(
    agent_id: str,
    skip:  int = Query(default=0, ge=0, description="Pagination offset."),
    limit: int = Query(default=50, ge=1, le=200, description="Page size."),
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> InteractionListResponse:
    """
    Retrieve interactions where the agent is source or target.

    Example:
        GET /agent-interactions/3fa85f64-5717-4562-b3fc-2c963f66afa6
        Authorization: Bearer <token>
    """
    return await interaction_service.list_interactions_for_agent(
        db, agent_id, skip=skip, limit=limit
    )