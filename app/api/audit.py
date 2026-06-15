"""
app/api/audit.py
─────────────────
FastAPI router for audit log endpoints.

Endpoints:
  GET /audit/logs                 — full paginated audit log, filterable
  GET /audit/logs/{agent_id}      — audit log scoped to one agent

Both endpoints require JWT authentication.  They are read-only —
audit events are immutable records of what happened, never writable
via the API.

Design:
  Routers are deliberately thin.  Every query lives in audit_service.
  The router's only responsibilities are:
    • Declare the HTTP method, path, status code, and response model.
    • Extract and validate query parameters (FastAPI/Pydantic handles this).
    • Inject auth and DB dependencies.
    • Call the service and return its result.
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.auth.dependencies import get_current_agent
from app.models.agent import Agent
from app.schemas.audit import AuditLogResponse
from app.services import audit_service

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get(
    "/logs",
    response_model=AuditLogResponse,
    status_code=status.HTTP_200_OK,
    summary="List audit events",
    description=(
        "Returns a paginated, filterable list of all agent audit events "
        "ordered by timestamp descending (newest first). "
        "Optionally filter by agent_id, event_type, or run_id."
    ),
    responses={
        200: {"description": "Paginated audit event list."},
        401: {"description": "Missing or invalid JWT."},
    },
)
async def get_audit_logs(
    agent_id: str | None = Query(
        default=None,
        description="Filter events to a specific agent UUID.",
    ),
    event_type: str | None = Query(
        default=None,
        description=(
            "Filter by event type. "
            "One of: run_start, tool_call, tool_end, violation, run_end."
        ),
    ),
    run_id: str | None = Query(
        default=None,
        description="Filter events to a specific run UUID.",
    ),
    skip: int = Query(default=0, ge=0, description="Pagination offset."),
    limit: int = Query(default=50, ge=1, le=200, description="Page size."),
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> AuditLogResponse:
    """
    Retrieve audit events with optional filtering.

    Examples:
        GET /audit/logs
        GET /audit/logs?event_type=violation
        GET /audit/logs?agent_id=<uuid>&event_type=tool_call
        GET /audit/logs?run_id=<uuid>
        GET /audit/logs?skip=50&limit=25
    """
    return await audit_service.get_audit_logs(
        db,
        agent_id=agent_id,
        event_type=event_type,
        run_id=run_id,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/logs/{agent_id}",
    response_model=AuditLogResponse,
    status_code=status.HTTP_200_OK,
    summary="List audit events for a specific agent",
    description=(
        "Returns the complete audit log for a single agent, "
        "paginated and ordered newest-first. "
        "Equivalent to GET /audit/logs?agent_id={agent_id}."
    ),
    responses={
        200: {"description": "Paginated audit event list for the agent."},
        401: {"description": "Missing or invalid JWT."},
    },
)
async def get_agent_audit_logs(
    agent_id: str,
    skip: int = Query(default=0, ge=0, description="Pagination offset."),
    limit: int = Query(default=50, ge=1, le=200, description="Page size."),
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> AuditLogResponse:
    """
    Retrieve audit events for a specific agent.

    Returns an empty list (not 404) if the agent has no events yet,
    or if the agent_id does not exist.  The caller can distinguish
    "agent exists but no events" from "agent does not exist" by
    checking GET /agents/{agent_id} separately.

    Example:
        GET /audit/logs/3fa85f64-5717-4562-b3fc-2c963f66afa6
    """
    return await audit_service.get_audit_logs_for_agent(
        db, agent_id, skip=skip, limit=limit
    )