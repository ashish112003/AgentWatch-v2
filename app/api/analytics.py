# """
# app/api/analytics.py
# ─────────────────────
# FastAPI router for analytics and observability endpoints.

# Endpoints:
#   GET /analytics/stats                 — platform-wide aggregates (dashboard header)
#   GET /analytics/stats/{agent_id}      — per-agent aggregate breakdown
#   GET /analytics/tool-latency          — avg + P95 latency per tool

# These endpoints feed the Bootstrap dashboard's summary cards and charts.
# They are all read-only and require JWT authentication.

# Response caching note:
#   These aggregate queries are potentially expensive on large datasets.
#   For production, consider:
#     • A 30-second in-memory cache (functools.lru_cache on the service layer).
#     • A Redis cache keyed by endpoint + invalidated on new run_end events.
#     • Materialised views in PostgreSQL updated by a background task.
#   For the SQLite MVP, direct queries on each request are fast enough
#   with row counts in the thousands.
# """

# from fastapi import APIRouter, Depends, HTTPException, status
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.db.database import get_db
# from app.auth.dependencies import get_current_agent
# from app.models.agent import Agent
# from app.schemas.audit import SystemStats, AgentStats, ToolLatencyStat
# from app.services import audit_service

# router = APIRouter(prefix="/analytics", tags=["Analytics"])


# @router.get(
#     "/stats",
#     response_model=SystemStats,
#     status_code=status.HTTP_200_OK,
#     summary="Platform-wide aggregate statistics",
#     description=(
#         "Returns counters for the entire AgentWatch platform: "
#         "total agents, runs, events, tool calls, violations, and "
#         "violation rate.  Also includes per-tool latency stats. "
#         "Used by the dashboard summary cards."
#     ),
#     responses={
#         200: {"description": "Platform statistics."},
#         401: {"description": "Missing or invalid JWT."},
#     },
# )
# async def get_system_stats(
#     _auth: Agent = Depends(get_current_agent),
#     db: AsyncSession = Depends(get_db),
# ) -> SystemStats:
#     """
#     Retrieve platform-wide aggregate statistics.

#     Executes three DB queries (agent count, run counts by status,
#     event counts by type) plus the tool latency sub-queries.
#     All results reflect the live state of the database at request time.

#     Example:
#         GET /analytics/stats
#         Authorization: Bearer <token>
#     """
#     return await audit_service.get_system_stats(db)


# @router.get(
#     "/stats/{agent_id}",
#     response_model=AgentStats,
#     status_code=status.HTTP_200_OK,
#     summary="Per-agent aggregate statistics",
#     description=(
#         "Returns run counts, event counts, violation rate, average run "
#         "latency, and tools used for a specific agent. "
#         "Used by the agent detail panel in the dashboard."
#     ),
#     responses={
#         200: {"description": "Agent statistics."},
#         401: {"description": "Missing or invalid JWT."},
#         404: {"description": "Agent not found."},
#     },
# )
# async def get_agent_stats(
#     agent_id: str,
#     _auth: Agent = Depends(get_current_agent),
#     db: AsyncSession = Depends(get_db),
# ) -> AgentStats:
#     """
#     Retrieve aggregate statistics for a single agent.

#     Returns 404 if no agent with the given ID exists.

#     Example:
#         GET /analytics/stats/3fa85f64-5717-4562-b3fc-2c963f66afa6
#         Authorization: Bearer <token>
#     """
#     try:
#         return await audit_service.get_agent_stats(db, agent_id)
#     except ValueError as exc:
#         # audit_service raises ValueError when the agent is not found.
#         # Convert to HTTP 404 here — the service layer stays HTTP-agnostic.
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND,
#             detail=str(exc),
#         )


# @router.get(
#     "/tool-latency",
#     response_model=list[ToolLatencyStat],
#     status_code=status.HTTP_200_OK,
#     summary="Tool latency statistics",
#     description=(
#         "Returns average and approximate P95 latency in milliseconds "
#         "for each tool, sorted by call count descending. "
#         "Only includes tools that have at least one recorded tool_end event. "
#         "Used by the dashboard latency chart."
#     ),
#     responses={
#         200: {"description": "List of tool latency statistics."},
#         401: {"description": "Missing or invalid JWT."},
#     },
# )
# async def get_tool_latency(
#     _auth: Agent = Depends(get_current_agent),
#     db: AsyncSession = Depends(get_db),
# ) -> list[ToolLatencyStat]:
#     """
#     Retrieve latency statistics per tool.

#     Returns an empty list if no tool_end events have been recorded yet
#     (e.g. a fresh deployment with no runs completed).

#     Example:
#         GET /analytics/tool-latency
#         Authorization: Bearer <token>
#     """
#     return await audit_service.get_tool_latency_stats(db)




# """
# app/api/analytics.py
# ─────────────────────
# FastAPI router for analytics and observability endpoints.

# Endpoints:
#   GET /analytics/stats                 — platform-wide aggregates (dashboard header)
#   GET /analytics/stats/{agent_id}      — per-agent aggregate breakdown
#   GET /analytics/tool-latency          — avg + P95 latency per tool

# These endpoints feed the Bootstrap dashboard's summary cards and charts.
# They are all read-only and require JWT authentication.

# Response caching note:
#   These aggregate queries are potentially expensive on large datasets.
#   For production, consider:
#     • A 30-second in-memory cache (functools.lru_cache on the service layer).
#     • A Redis cache keyed by endpoint + invalidated on new run_end events.
#     • Materialised views in PostgreSQL updated by a background task.
#   For the SQLite MVP, direct queries on each request are fast enough
#   with row counts in the thousands.
# """

# from fastapi import APIRouter, Depends, HTTPException, status
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.db.database import get_db
# from app.auth.dependencies import get_current_agent
# from app.models.agent import Agent
# from app.schemas.audit import (
#     SystemStats, AgentStats, ToolLatencyStat,
#     AgentTrustResponse, SystemTrustResponse, TrustBreakdown,
# )
# from app.services import audit_service
# from app.services.trust_service import get_agent_trust_breakdown, get_trust_distribution

# router = APIRouter(prefix="/analytics", tags=["Analytics"])


# @router.get(
#     "/stats",
#     response_model=SystemStats,
#     status_code=status.HTTP_200_OK,
#     summary="Platform-wide aggregate statistics",
#     description=(
#         "Returns counters for the entire AgentWatch platform: "
#         "total agents, runs, events, tool calls, violations, and "
#         "violation rate.  Also includes per-tool latency stats. "
#         "Used by the dashboard summary cards."
#     ),
#     responses={
#         200: {"description": "Platform statistics."},
#         401: {"description": "Missing or invalid JWT."},
#     },
# )
# async def get_system_stats(
#     _auth: Agent = Depends(get_current_agent),
#     db: AsyncSession = Depends(get_db),
# ) -> SystemStats:
#     """
#     Retrieve platform-wide aggregate statistics.

#     Executes three DB queries (agent count, run counts by status,
#     event counts by type) plus the tool latency sub-queries.
#     All results reflect the live state of the database at request time.

#     Example:
#         GET /analytics/stats
#         Authorization: Bearer <token>
#     """
#     return await audit_service.get_system_stats(db)


# @router.get(
#     "/stats/{agent_id}",
#     response_model=AgentStats,
#     status_code=status.HTTP_200_OK,
#     summary="Per-agent aggregate statistics",
#     description=(
#         "Returns run counts, event counts, violation rate, average run "
#         "latency, and tools used for a specific agent. "
#         "Used by the agent detail panel in the dashboard."
#     ),
#     responses={
#         200: {"description": "Agent statistics."},
#         401: {"description": "Missing or invalid JWT."},
#         404: {"description": "Agent not found."},
#     },
# )
# async def get_agent_stats(
#     agent_id: str,
#     _auth: Agent = Depends(get_current_agent),
#     db: AsyncSession = Depends(get_db),
# ) -> AgentStats:
#     """
#     Retrieve aggregate statistics for a single agent.

#     Returns 404 if no agent with the given ID exists.

#     Example:
#         GET /analytics/stats/3fa85f64-5717-4562-b3fc-2c963f66afa6
#         Authorization: Bearer <token>
#     """
#     try:
#         return await audit_service.get_agent_stats(db, agent_id)
#     except ValueError as exc:
#         # audit_service raises ValueError when the agent is not found.
#         # Convert to HTTP 404 here — the service layer stays HTTP-agnostic.
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND,
#             detail=str(exc),
#         )


# @router.get(
#     "/tool-latency",
#     response_model=list[ToolLatencyStat],
#     status_code=status.HTTP_200_OK,
#     summary="Tool latency statistics",
#     description=(
#         "Returns average and approximate P95 latency in milliseconds "
#         "for each tool, sorted by call count descending. "
#         "Only includes tools that have at least one recorded tool_end event. "
#         "Used by the dashboard latency chart."
#     ),
#     responses={
#         200: {"description": "List of tool latency statistics."},
#         401: {"description": "Missing or invalid JWT."},
#     },
# )
# async def get_tool_latency(
#     _auth: Agent = Depends(get_current_agent),
#     db: AsyncSession = Depends(get_db),
# ) -> list[ToolLatencyStat]:
#     """
#     Retrieve latency statistics per tool.

#     Returns an empty list if no tool_end events have been recorded yet
#     (e.g. a fresh deployment with no runs completed).

#     Example:
#         GET /analytics/tool-latency
#         Authorization: Bearer <token>
#     """
#     return await audit_service.get_tool_latency_stats(db)


# @router.get(
#     "/trust",
#     response_model=SystemTrustResponse,
#     status_code=status.HTTP_200_OK,
#     summary="Platform-wide trust score distribution",
#     description=(
#         "Returns the average trust score across all registered agents "
#         "and a count of agents at each trust level "
#         "(TRUSTED / MONITORED / WARNING / HIGH_RISK)."
#     ),
#     responses={
#         200: {"description": "System trust distribution."},
#         401: {"description": "Missing or invalid JWT."},
#     },
# )
# async def get_system_trust(
#     _auth: Agent = Depends(get_current_agent),
#     db: AsyncSession = Depends(get_db),
# ) -> SystemTrustResponse:
#     """
#     Retrieve platform-wide trust score aggregates.

#     Scores every registered agent and returns the distribution.
#     With many agents this may take O(N) queries; for production
#     consider caching the result.

#     Example:
#         GET /analytics/trust
#         Authorization: Bearer <token>
#     """
#     data = await get_trust_distribution(db)
#     return SystemTrustResponse(
#         average_trust_score=data["average_trust_score"],
#         trust_distribution=data["trust_distribution"],
#     )


# @router.get(
#     "/trust/{agent_id}",
#     response_model=AgentTrustResponse,
#     status_code=status.HTTP_200_OK,
#     summary="Trust score for a specific agent",
#     description=(
#         "Returns the trust score, trust level, and a full explainable "
#         "breakdown for the specified agent. "
#         "Every contributing factor is listed with its point contribution."
#     ),
#     responses={
#         200: {"description": "Agent trust detail."},
#         401: {"description": "Missing or invalid JWT."},
#         404: {"description": "Agent not found."},
#     },
# )
# async def get_agent_trust(
#     agent_id: str,
#     _auth: Agent = Depends(get_current_agent),
#     db: AsyncSession = Depends(get_db),
# ) -> AgentTrustResponse:
#     """
#     Retrieve the trust score breakdown for a single agent.

#     Returns 404 if no agent with the given ID exists.

#     Example:
#         GET /analytics/trust/3fa85f64-5717-4562-b3fc-2c963f66afa6
#         Authorization: Bearer <token>
#     """
#     try:
#         data = await get_agent_trust_breakdown(db, agent_id)
#     except ValueError as exc:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND,
#             detail=str(exc),
#         )
#     return AgentTrustResponse(
#         agent_id=data["agent_id"],
#         agent_name=data["agent_name"],
#         trust_score=data["trust_score"],
#         trust_level=data["trust_level"],
#         breakdown=TrustBreakdown(**data["breakdown"]),
#     )
























































"""
app/api/analytics.py
─────────────────────
FastAPI router for analytics and observability endpoints.

Endpoints:
  GET /analytics/stats                 — platform-wide aggregates (dashboard header)
  GET /analytics/stats/{agent_id}      — per-agent aggregate breakdown
  GET /analytics/tool-latency          — avg + P95 latency per tool

These endpoints feed the Bootstrap dashboard's summary cards and charts.
They are all read-only and require JWT authentication.

Response caching note:
  These aggregate queries are potentially expensive on large datasets.
  For production, consider:
    • A 30-second in-memory cache (functools.lru_cache on the service layer).
    • A Redis cache keyed by endpoint + invalidated on new run_end events.
    • Materialised views in PostgreSQL updated by a background task.
  For the SQLite MVP, direct queries on each request are fast enough
  with row counts in the thousands.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.auth.dependencies import get_current_agent
from app.models.agent import Agent
from app.schemas.audit import (
    SystemStats, AgentStats, ToolLatencyStat,
    AgentTrustResponse, SystemTrustResponse, TrustBreakdown,
    AgentRiskResponse, SystemRiskResponse, RiskBreakdown,
)
from app.services import audit_service
from app.services.trust_service import get_agent_trust_breakdown, get_trust_distribution
from app.services.risk_service  import get_agent_risk_breakdown, get_risk_distribution

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get(
    "/stats",
    response_model=SystemStats,
    status_code=status.HTTP_200_OK,
    summary="Platform-wide aggregate statistics",
    description=(
        "Returns counters for the entire AgentWatch platform: "
        "total agents, runs, events, tool calls, violations, and "
        "violation rate.  Also includes per-tool latency stats. "
        "Used by the dashboard summary cards."
    ),
    responses={
        200: {"description": "Platform statistics."},
        401: {"description": "Missing or invalid JWT."},
    },
)
async def get_system_stats(
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> SystemStats:
    """
    Retrieve platform-wide aggregate statistics.

    Executes three DB queries (agent count, run counts by status,
    event counts by type) plus the tool latency sub-queries.
    All results reflect the live state of the database at request time.

    Example:
        GET /analytics/stats
        Authorization: Bearer <token>
    """
    return await audit_service.get_system_stats(db)


@router.get(
    "/stats/{agent_id}",
    response_model=AgentStats,
    status_code=status.HTTP_200_OK,
    summary="Per-agent aggregate statistics",
    description=(
        "Returns run counts, event counts, violation rate, average run "
        "latency, and tools used for a specific agent. "
        "Used by the agent detail panel in the dashboard."
    ),
    responses={
        200: {"description": "Agent statistics."},
        401: {"description": "Missing or invalid JWT."},
        404: {"description": "Agent not found."},
    },
)
async def get_agent_stats(
    agent_id: str,
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> AgentStats:
    """
    Retrieve aggregate statistics for a single agent.

    Returns 404 if no agent with the given ID exists.

    Example:
        GET /analytics/stats/3fa85f64-5717-4562-b3fc-2c963f66afa6
        Authorization: Bearer <token>
    """
    try:
        return await audit_service.get_agent_stats(db, agent_id)
    except ValueError as exc:
        # audit_service raises ValueError when the agent is not found.
        # Convert to HTTP 404 here — the service layer stays HTTP-agnostic.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


@router.get(
    "/tool-latency",
    response_model=list[ToolLatencyStat],
    status_code=status.HTTP_200_OK,
    summary="Tool latency statistics",
    description=(
        "Returns average and approximate P95 latency in milliseconds "
        "for each tool, sorted by call count descending. "
        "Only includes tools that have at least one recorded tool_end event. "
        "Used by the dashboard latency chart."
    ),
    responses={
        200: {"description": "List of tool latency statistics."},
        401: {"description": "Missing or invalid JWT."},
    },
)
async def get_tool_latency(
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> list[ToolLatencyStat]:
    """
    Retrieve latency statistics per tool.

    Returns an empty list if no tool_end events have been recorded yet
    (e.g. a fresh deployment with no runs completed).

    Example:
        GET /analytics/tool-latency
        Authorization: Bearer <token>
    """
    return await audit_service.get_tool_latency_stats(db)


@router.get(
    "/trust",
    response_model=SystemTrustResponse,
    status_code=status.HTTP_200_OK,
    summary="Platform-wide trust score distribution",
    description=(
        "Returns the average trust score across all registered agents "
        "and a count of agents at each trust level "
        "(TRUSTED / MONITORED / WARNING / HIGH_RISK)."
    ),
    responses={
        200: {"description": "System trust distribution."},
        401: {"description": "Missing or invalid JWT."},
    },
)
async def get_system_trust(
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> SystemTrustResponse:
    """
    Retrieve platform-wide trust score aggregates.

    Scores every registered agent and returns the distribution.
    With many agents this may take O(N) queries; for production
    consider caching the result.

    Example:
        GET /analytics/trust
        Authorization: Bearer <token>
    """
    data = await get_trust_distribution(db)
    return SystemTrustResponse(
        average_trust_score=data["average_trust_score"],
        trust_distribution=data["trust_distribution"],
    )


@router.get(
    "/trust/{agent_id}",
    response_model=AgentTrustResponse,
    status_code=status.HTTP_200_OK,
    summary="Trust score for a specific agent",
    description=(
        "Returns the trust score, trust level, and a full explainable "
        "breakdown for the specified agent. "
        "Every contributing factor is listed with its point contribution."
    ),
    responses={
        200: {"description": "Agent trust detail."},
        401: {"description": "Missing or invalid JWT."},
        404: {"description": "Agent not found."},
    },
)
async def get_agent_trust(
    agent_id: str,
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> AgentTrustResponse:
    """
    Retrieve the trust score breakdown for a single agent.

    Returns 404 if no agent with the given ID exists.

    Example:
        GET /analytics/trust/3fa85f64-5717-4562-b3fc-2c963f66afa6
        Authorization: Bearer <token>
    """
    try:
        data = await get_agent_trust_breakdown(db, agent_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    return AgentTrustResponse(
        agent_id=data["agent_id"],
        agent_name=data["agent_name"],
        trust_score=data["trust_score"],
        trust_level=data["trust_level"],
        breakdown=TrustBreakdown(**data["breakdown"]),
    )

@router.get(
    "/risk",
    response_model=SystemRiskResponse,
    status_code=status.HTTP_200_OK,
    summary="Platform-wide risk score distribution",
    description=(
        "Returns the average risk score across all registered agents "
        "and a count of agents at each risk level "
        "(SAFE / LOW / MEDIUM / HIGH / CRITICAL)."
    ),
    responses={
        200: {"description": "System risk distribution."},
        401: {"description": "Missing or invalid JWT."},
    },
)
async def get_system_risk(
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> SystemRiskResponse:
    """
    Retrieve platform-wide risk score aggregates.

    Scores every registered agent and returns the distribution.

    Example:
        GET /analytics/risk
        Authorization: Bearer <token>
    """
    data = await get_risk_distribution(db)
    return SystemRiskResponse(
        average_risk_score=data["average_risk_score"],
        risk_distribution=data["risk_distribution"],
    )


@router.get(
    "/risk/{agent_id}",
    response_model=AgentRiskResponse,
    status_code=status.HTTP_200_OK,
    summary="Risk score for a specific agent",
    description=(
        "Returns the risk score, risk level, and a full explainable "
        "breakdown for the specified agent. "
        "Every contributing factor is listed with its point contribution."
    ),
    responses={
        200: {"description": "Agent risk detail."},
        401: {"description": "Missing or invalid JWT."},
        404: {"description": "Agent not found."},
    },
)
async def get_agent_risk(
    agent_id: str,
    _auth: Agent = Depends(get_current_agent),
    db: AsyncSession = Depends(get_db),
) -> AgentRiskResponse:
    """
    Retrieve the risk score breakdown for a single agent.

    Returns 404 if no agent with the given ID exists.

    Example:
        GET /analytics/risk/3fa85f64-5717-4562-b3fc-2c963f66afa6
        Authorization: Bearer <token>
    """
    try:
        data = await get_agent_risk_breakdown(db, agent_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    return AgentRiskResponse(
        agent_id=data["agent_id"],
        agent_name=data["agent_name"],
        risk_score=data["risk_score"],
        risk_level=data["risk_level"],
        breakdown=RiskBreakdown(**data["breakdown"]),
    )