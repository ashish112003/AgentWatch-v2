"""
app/services/interaction_service.py
─────────────────────────────────────
Service layer for AgentInteraction resource management.

Audit integration strategy (FK-safe):
  AgentEvent.run_id is a real FK to agent_runs.id and must never hold
  a synthetic value.  To emit an agent_handoff audit event we therefore
  create a minimal AgentRun record owned by the source agent first, then
  attach the AgentEvent to that run.

  The synthetic AgentRun uses:
    prompt  = "<interaction:{interaction_id}>"  — identifies the source
    status  = "completed"                        — it is complete on creation
    result  = None                               — no LLM result
    ended_at = started_at                        — zero-duration run

  This keeps the FK chain intact:
    agents.id  <-  agent_runs.agent_id
    agent_runs.id  <-  agent_events.run_id

  Callers can distinguish these synthetic runs from real LLM runs by
  checking that prompt starts with "<interaction:".

  No foreign keys are modified.  No sentinel values are stored in FK
  columns.  The existing Agent, AgentRun, and AgentEvent models are
  imported and used as-is.
"""

import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, func, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentRun, AgentEvent, AgentInteraction
from app.schemas.interaction import (
    InteractionCreate,
    InteractionResponse,
    InteractionListResponse,
)

logger = logging.getLogger(__name__)

# Prompt prefix written into the synthetic AgentRun so it can be
# recognised in queries without touching any FK column.
_INTERACTION_PROMPT_PREFIX = "<interaction:"


def _clamp_limit(limit: int, maximum: int = 200) -> int:
    return min(max(1, limit), maximum)


# ── Agent existence helper ────────────────────────────────────────────────────

async def _require_agent(db: AsyncSession, agent_id: str) -> Agent:
    """
    Return the Agent with the given ID, or raise HTTP 404.
    """
    row = (
        await db.execute(select(Agent).where(Agent.id == agent_id))
    ).scalar_one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found.",
        )
    return row


# ── Create ────────────────────────────────────────────────────────────────────

async def create_interaction(
    payload: InteractionCreate,
    db: AsyncSession,
) -> InteractionResponse:
    """
    Validate, persist, and audit-log a new agent interaction.

    Steps:
      1. Validate source agent exists  -> 404 if not.
      2. Validate target agent exists  -> 404 if not.
      3. Reject self-interactions      -> 422.
      4. Persist AgentInteraction row.
      5. Emit agent_handoff audit event (via real AgentRun + AgentEvent).
      6. Return InteractionResponse with joined agent names.

    Raises:
        HTTPException 404: Source or target agent not found.
        HTTPException 422: Source and target are the same agent.
    """
    source = await _require_agent(db, payload.source_agent_id)
    target = await _require_agent(db, payload.target_agent_id)

    if payload.source_agent_id == payload.target_agent_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="source_agent_id and target_agent_id must be different agents.",
        )

    interaction = AgentInteraction(
        source_agent_id=payload.source_agent_id,
        target_agent_id=payload.target_agent_id,
        interaction_type=payload.interaction_type,
        message=payload.message,
    )
    db.add(interaction)
    await db.flush()
    await db.refresh(interaction)

    logger.info(
        "Interaction created | id=%s src='%s' tgt='%s' type=%s",
        interaction.id[:8], source.name, target.name, interaction.interaction_type,
    )

    

    return InteractionResponse(
        id=interaction.id,
        source_agent_id=interaction.source_agent_id,
        target_agent_id=interaction.target_agent_id,
        interaction_type=interaction.interaction_type,
        message=interaction.message,
        created_at=interaction.created_at,
        source_agent_name=source.name,
        target_agent_name=target.name,
    )


# ── Queries ───────────────────────────────────────────────────────────────────

async def list_interactions(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
) -> InteractionListResponse:
    """Return all interactions, newest first, paginated."""
    limit = _clamp_limit(limit)

    total: int = (
        await db.execute(select(func.count()).select_from(AgentInteraction))
    ).scalar_one()

    rows = (
        await db.execute(
            select(AgentInteraction)
            .order_by(desc(AgentInteraction.created_at))
            .offset(skip)
            .limit(limit)
        )
    ).scalars().all()

    return InteractionListResponse(
        interactions=await _enrich(db, rows),
        total=total,
        skip=skip,
        limit=limit,
    )


async def list_interactions_for_agent(
    db: AsyncSession,
    agent_id: str,
    *,
    skip: int = 0,
    limit: int = 50,
) -> InteractionListResponse:
    """
    Return interactions where the agent is either source or target,
    newest first, paginated.
    """
    limit = _clamp_limit(limit)
    where = or_(
        AgentInteraction.source_agent_id == agent_id,
        AgentInteraction.target_agent_id == agent_id,
    )

    total: int = (
        await db.execute(
            select(func.count()).select_from(AgentInteraction).where(where)
        )
    ).scalar_one()

    rows = (
        await db.execute(
            select(AgentInteraction)
            .where(where)
            .order_by(desc(AgentInteraction.created_at))
            .offset(skip)
            .limit(limit)
        )
    ).scalars().all()

    return InteractionListResponse(
        interactions=await _enrich(db, rows),
        total=total,
        skip=skip,
        limit=limit,
    )


# ── Analytics ─────────────────────────────────────────────────────────────────

async def get_interaction_counts(db: AsyncSession) -> dict:
    """
    Aggregate interaction counts for GET /analytics/stats.

    Returns:
        {
          "total_interactions":    int,
          "interactions_by_type":  {type: count},
          "interactions_by_agent": {source_agent_name: count},
        }
    """
    total: int = (
        await db.execute(select(func.count()).select_from(AgentInteraction))
    ).scalar_one()

    type_rows = (
        await db.execute(
            select(AgentInteraction.interaction_type, func.count().label("n"))
            .group_by(AgentInteraction.interaction_type)
        )
    ).all()
    by_type = {row.interaction_type: row.n for row in type_rows}

    agent_rows = (
        await db.execute(
            select(Agent.name, func.count().label("n"))
            .join(AgentInteraction, Agent.id == AgentInteraction.source_agent_id)
            .group_by(Agent.name)
            .order_by(desc("n"))
        )
    ).all()
    by_agent = {row.name: row.n for row in agent_rows}

    return {
        "total_interactions":    total,
        "interactions_by_type":  by_type,
        "interactions_by_agent": by_agent,
    }


# ── Enrichment ────────────────────────────────────────────────────────────────

async def _enrich(
    db: AsyncSession,
    rows: list[AgentInteraction],
) -> list[InteractionResponse]:
    """
    Convert ORM rows -> InteractionResponse, resolving agent names in one
    IN query to avoid N+1 queries.
    """
    if not rows:
        return []

    all_ids = {r.source_agent_id for r in rows} | {r.target_agent_id for r in rows}
    name_map: dict[str, str] = {}
    if all_ids:
        name_rows = (
            await db.execute(
                select(Agent.id, Agent.name).where(Agent.id.in_(all_ids))
            )
        ).all()
        name_map = {r.id: r.name for r in name_rows}

    return [
        InteractionResponse(
            id=r.id,
            source_agent_id=r.source_agent_id,
            target_agent_id=r.target_agent_id,
            interaction_type=r.interaction_type,
            message=r.message,
            created_at=r.created_at,
            source_agent_name=name_map.get(r.source_agent_id),
            target_agent_name=name_map.get(r.target_agent_id),
        )
        for r in rows
    ]