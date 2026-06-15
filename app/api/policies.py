"""
app/api/policies.py
────────────────────
FastAPI router for Policy Engine endpoints.

Endpoints:
  POST   /policies                              — create a policy
  GET    /policies                              — list all policies
  GET    /policies/{policy_id}                  — get one policy
  POST   /policies/{policy_id}/agents/{agent_id} — attach policy to agent
  DELETE /policies/{policy_id}/agents/{agent_id} — detach policy from agent
  GET    /agents/{agent_id}/policies            — list agent's policies

All endpoints require JWT authentication.
All business logic lives in app/services/policy_service.py.
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.auth.dependencies import get_current_agent
from app.models.agent import Agent
from app.schemas.policy import (
    PolicyCreate,
    PolicyResponse,
    PolicyListResponse,
    AgentPolicyResponse,
    AgentPolicyListResponse,
)
from app.services import policy_service

router = APIRouter(tags=["Policies"])


# ── Policy CRUD ───────────────────────────────────────────────────────────────

@router.post(
    "/policies",
    response_model=PolicyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a governance policy",
    description=(
        "Creates a named, reusable governance rule. "
        "Policies must be explicitly assigned to agents via "
        "POST /policies/{policy_id}/agents/{agent_id} to take effect."
    ),
    responses={
        201: {"description": "Policy created."},
        401: {"description": "Missing or invalid JWT."},
        409: {"description": "Policy name already exists."},
        422: {"description": "Validation error or invalid rule_config shape."},
    },
)
async def create_policy(
    payload: PolicyCreate,
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> PolicyResponse:
    return await policy_service.create_policy(payload, db)


@router.get(
    "/policies",
    response_model=PolicyListResponse,
    status_code=status.HTTP_200_OK,
    summary="List all governance policies",
    description="Returns a paginated list of all policies with agent counts.",
    responses={
        200: {"description": "Paginated policy list."},
        401: {"description": "Missing or invalid JWT."},
    },
)
async def list_policies(
    skip:  int = Query(default=0, ge=0, description="Pagination offset."),
    limit: int = Query(default=50, ge=1, le=200, description="Page size."),
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> PolicyListResponse:
    return await policy_service.list_policies(db, skip=skip, limit=limit)


@router.get(
    "/policies/{policy_id}",
    response_model=PolicyResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a single policy by ID",
    responses={
        200: {"description": "Policy detail."},
        401: {"description": "Missing or invalid JWT."},
        404: {"description": "Policy not found."},
    },
)
async def get_policy(
    policy_id: str,
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> PolicyResponse:
    return await policy_service.get_policy_by_id(db, policy_id)


# ── Agent↔Policy assignments ──────────────────────────────────────────────────

@router.post(
    "/policies/{policy_id}/agents/{agent_id}",
    response_model=AgentPolicyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assign a policy to an agent",
    description=(
        "Attaches the policy to the agent. From the next run onwards, "
        "this policy will be evaluated before any tool calls are made."
    ),
    responses={
        201: {"description": "Policy assigned."},
        401: {"description": "Missing or invalid JWT."},
        404: {"description": "Policy or agent not found."},
        409: {"description": "Policy already assigned to this agent."},
    },
)
async def assign_policy(
    policy_id: str,
    agent_id: str,
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> AgentPolicyResponse:
    return await policy_service.assign_policy_to_agent(db, policy_id, agent_id)


@router.delete(
    "/policies/{policy_id}/agents/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a policy from an agent",
    description="Detaches the policy. Takes effect from the next run.",
    responses={
        204: {"description": "Policy detached."},
        401: {"description": "Missing or invalid JWT."},
        404: {"description": "Assignment not found."},
    },
)
async def remove_policy(
    policy_id: str,
    agent_id: str,
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> None:
    await policy_service.remove_policy_from_agent(db, policy_id, agent_id)


@router.get(
    "/agents/{agent_id}/policies",
    response_model=AgentPolicyListResponse,
    status_code=status.HTTP_200_OK,
    summary="List policies assigned to an agent",
    description=(
        "Returns all policies currently assigned to the agent, "
        "both active and inactive."
    ),
    responses={
        200: {"description": "Agent's policy list."},
        401: {"description": "Missing or invalid JWT."},
        404: {"description": "Agent not found."},
    },
)
async def list_agent_policies(
    agent_id: str,
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> AgentPolicyListResponse:
    return await policy_service.list_policies_for_agent(db, agent_id)