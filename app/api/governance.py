"""
app/api/governance.py
──────────────────────
FastAPI router for governance enforcement endpoints.

Endpoints:
  GET /governance/violations              — all violations, filterable
  GET /governance/violations/{agent_id}   — violations for one agent
  GET /governance/runs                    — run history with violation counts

These endpoints provide the governance-focused view of the audit data.
While /audit/logs shows all event types, /governance/violations shows
only blocked tool calls — making it easy to build compliance dashboards
that focus on policy enforcement rather than the full event stream.
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.auth.dependencies import get_current_agent
from app.models.agent import Agent
from app.schemas.audit import ViolationListResponse, RunListResponse
from app.services import audit_service

router = APIRouter(prefix="/governance", tags=["Governance"])


@router.get(
    "/violations",
    response_model=ViolationListResponse,
    status_code=status.HTTP_200_OK,
    summary="List all governance violations",
    description=(
        "Returns a paginated list of governance violations across all agents, "
        "ordered by timestamp descending. "
        "Each violation includes the blocked tool name, the input the agent "
        "attempted to pass, and the denial message returned."
    ),
    responses={
        200: {"description": "Paginated violation list."},
        401: {"description": "Missing or invalid JWT."},
    },
)
async def get_violations(
    agent_id: str | None = Query(
        default=None,
        description="Filter violations to a specific agent UUID.",
    ),
    skip:  int = Query(default=0, ge=0, description="Pagination offset."),
    limit: int = Query(default=50, ge=1, le=200, description="Page size."),
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> ViolationListResponse:
    """
    Retrieve governance violations.

    Examples:
        GET /governance/violations
        GET /governance/violations?agent_id=<uuid>
        GET /governance/violations?skip=0&limit=20
    """
    return await audit_service.get_violations(
        db, agent_id=agent_id, skip=skip, limit=limit
    )


@router.get(
    "/violations/{agent_id}",
    response_model=ViolationListResponse,
    status_code=status.HTTP_200_OK,
    summary="List governance violations for a specific agent",
    description=(
        "Returns all governance violations produced by a single agent. "
        "Equivalent to GET /governance/violations?agent_id={agent_id}."
    ),
    responses={
        200: {"description": "Paginated violation list for the agent."},
        401: {"description": "Missing or invalid JWT."},
    },
)
async def get_agent_violations(
    agent_id: str,
    skip:  int = Query(default=0, ge=0, description="Pagination offset."),
    limit: int = Query(default=50, ge=1, le=200, description="Page size."),
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> ViolationListResponse:
    """
    Retrieve violations for a specific agent by UUID.

    Example:
        GET /governance/violations/3fa85f64-5717-4562-b3fc-2c963f66afa6
    """
    return await audit_service.get_violations(
        db, agent_id=agent_id, skip=skip, limit=limit
    )


@router.get(
    "/runs",
    response_model=RunListResponse,
    status_code=status.HTTP_200_OK,
    summary="List agent runs with violation counts",
    description=(
        "Returns a paginated list of all agent runs, newest first. "
        "Each run summary includes its violation_count — the number of "
        "governance violations that occurred during that run. "
        "Optionally filter by agent_id or run status."
    ),
    responses={
        200: {"description": "Paginated run list."},
        401: {"description": "Missing or invalid JWT."},
    },
)
async def get_runs(
    agent_id: str | None = Query(
        default=None,
        description="Filter runs to a specific agent UUID.",
    ),
    run_status: str | None = Query(
        default=None,
        alias="status",
        description="Filter by run status: completed | failed | running.",
    ),
    skip:  int = Query(default=0, ge=0, description="Pagination offset."),
    limit: int = Query(default=50, ge=1, le=200, description="Page size."),
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> RunListResponse:
    """
    Retrieve run history with per-run violation counts.

    Useful for identifying which specific runs produced governance
    violations, then drilling down with GET /audit/logs?run_id={id}.

    Examples:
        GET /governance/runs
        GET /governance/runs?status=completed
        GET /governance/runs?agent_id=<uuid>&status=failed
    """
    return await audit_service.get_runs(
        db, agent_id=agent_id, status=run_status, skip=skip, limit=limit
    )