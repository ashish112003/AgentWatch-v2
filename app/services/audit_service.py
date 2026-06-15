"""
app/services/audit_service.py
──────────────────────────────
All read-only database queries for the audit, governance, and analytics
endpoints.  No business logic lives in the routers — every SQL statement
is here so it can be tested independently.

Query design principles:
  1. Single async query per operation where possible.
     COUNT + paginated SELECT are always two separate queries because
     SQLAlchemy's async API does not support combined count+fetch natively.
     We accept the two round-trips; SQLite latency is sub-millisecond.

  2. All joins use explicit ON clauses (not implicit foreign-key magic)
     for clarity when reading the code without an ORM reference open.

  3. Pagination is OFFSET/LIMIT.  For very large tables a keyset approach
     (WHERE id > last_seen_id) would be more efficient, but for an MVP
     with SQLite and expected row counts in the thousands, OFFSET is fine.

  4. P95 latency approximation for SQLite:
     SQLite has no PERCENTILE_CONT aggregate.  We approximate by:
       a. Fetching all latency_ms values for the tool in sorted order.
       b. Taking the row at index floor(N * 0.95).
     This is O(N) in the number of tool_end rows per tool.  With expected
     call counts in the hundreds this is fast enough.  If you migrate to
     PostgreSQL, replace with:
       SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)

  5. All timestamps from SQLite are timezone-naive.  The service returns
     them as-is; the Pydantic schema serialises to ISO-8601 strings in
     UTC (by convention — the storage layer is naive).
"""

# import logging
# from datetime import datetime

# from sqlalchemy import select, func, and_, desc, asc
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.models.agent import Agent, AgentRun, AgentEvent
# from app.schemas.audit import (
#     AuditEventSchema,
#     AuditLogResponse,
#     ViolationSchema,
#     ViolationListResponse,
#     RunSummary,
#     RunListResponse,
#     ToolLatencyStat,
#     AgentStats,
#     SystemStats,
# )

# logger = logging.getLogger(__name__)


# # ─────────────────────────────────────────────────────────────────────────────
# # Internal helpers
# # ─────────────────────────────────────────────────────────────────────────────

# def _clamp_limit(limit: int, maximum: int = 200) -> int:
#     """Prevent accidental huge payloads by enforcing a per-endpoint ceiling."""
#     return min(max(1, limit), maximum)


# async def _agent_name_map(db: AsyncSession, agent_ids: set[str]) -> dict[str, str]:
#     """
#     Return {agent_id: agent_name} for a set of agent UUIDs.

#     Used to enrich event/run responses without N+1 queries: we collect
#     all unique agent_ids from the result set first, then load names in
#     a single IN query.

#     Args:
#         db:        Async DB session.
#         agent_ids: Set of agent UUID strings.

#     Returns:
#         Dict mapping agent_id → name.  Missing agents map to None.
#     """
#     if not agent_ids:
#         return {}

#     result = await db.execute(
#         select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids))
#     )
#     return {row.id: row.name for row in result.all()}


# # ─────────────────────────────────────────────────────────────────────────────
# # Audit log queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_audit_logs(
#     db: AsyncSession,
#     *,
#     agent_id: str | None = None,
#     event_type: str | None = None,
#     run_id: str | None = None,
#     skip: int = 0,
#     limit: int = 50,
# ) -> AuditLogResponse:
#     """
#     Return a paginated, filtered list of AgentEvent rows.

#     All filter parameters are optional and additive (AND logic).
#     Results are ordered by timestamp DESC (newest first) so the
#     caller always sees the most recent activity at the top.

#     Args:
#         db:         Async DB session.
#         agent_id:   Filter to events for a specific agent UUID.
#         event_type: Filter to a specific event type string
#                     (run_start | tool_call | tool_end | violation | run_end).
#         run_id:     Filter to events for a specific run UUID.
#         skip:       Pagination offset.
#         limit:      Page size (clamped to 200).

#     Returns:
#         AuditLogResponse with events list and total count.
#     """
#     limit = _clamp_limit(limit)

#     # Build the shared WHERE clause once — reused for COUNT and SELECT.
#     filters = []
#     if agent_id:
#         filters.append(AgentEvent.agent_id == agent_id)
#     if event_type:
#         filters.append(AgentEvent.event_type == event_type)
#     if run_id:
#         filters.append(AgentEvent.run_id == run_id)

#     where = and_(*filters) if filters else True

#     # ── Total count ───────────────────────────────────────────────────
#     count_q = select(func.count()).select_from(AgentEvent).where(where)
#     total: int = (await db.execute(count_q)).scalar_one()

#     # ── Paginated rows ────────────────────────────────────────────────
#     rows_q = (
#         select(AgentEvent)
#         .where(where)
#         .order_by(desc(AgentEvent.timestamp))
#         .offset(skip)
#         .limit(limit)
#     )
#     rows = (await db.execute(rows_q)).scalars().all()

#     # ── Enrich with agent names (single IN query) ──────────────────────
#     agent_ids = {e.agent_id for e in rows}
#     name_map  = await _agent_name_map(db, agent_ids)

#     events = [
#         AuditEventSchema(
#             id=e.id,
#             run_id=e.run_id,
#             agent_id=e.agent_id,
#             trace_id=e.trace_id,
#             event_type=e.event_type,
#             tool_name=e.tool_name,
#             input_data=e.input_data,
#             output_data=e.output_data,
#             permitted=e.permitted,
#             latency_ms=e.latency_ms,
#             timestamp=e.timestamp,
#             agent_name=name_map.get(e.agent_id),
#         )
#         for e in rows
#     ]

#     return AuditLogResponse(events=events, total=total, skip=skip, limit=limit)


# async def get_audit_logs_for_agent(
#     db: AsyncSession,
#     agent_id: str,
#     *,
#     skip: int = 0,
#     limit: int = 50,
# ) -> AuditLogResponse:
#     """
#     Convenience wrapper — audit log filtered to a single agent.

#     Equivalent to get_audit_logs(db, agent_id=agent_id, ...) but
#     provides a cleaner call site for the GET /audit/logs/{agent_id} router.
#     """
#     return await get_audit_logs(
#         db, agent_id=agent_id, skip=skip, limit=limit
#     )


# # ─────────────────────────────────────────────────────────────────────────────
# # Governance / violations queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_violations(
#     db: AsyncSession,
#     *,
#     agent_id: str | None = None,
#     skip: int = 0,
#     limit: int = 50,
# ) -> ViolationListResponse:
#     """
#     Return a paginated list of governance violation events.

#     A violation event is an AgentEvent where:
#       event_type = 'violation'  AND  permitted = False

#     The denial_message is extracted from event.output_data["denial_message"]
#     and surfaced as a top-level field so callers don't need to parse JSON.

#     Args:
#         db:       Async DB session.
#         agent_id: Optional filter to one agent's violations.
#         skip:     Pagination offset.
#         limit:    Page size (clamped to 200).

#     Returns:
#         ViolationListResponse with violations list and total count.
#     """
#     limit = _clamp_limit(limit)

#     filters = [
#         AgentEvent.event_type == "violation",
#         AgentEvent.permitted  == False,  # noqa: E712 — SQLAlchemy needs == False
#     ]
#     if agent_id:
#         filters.append(AgentEvent.agent_id == agent_id)

#     where = and_(*filters)

#     # ── Total count ───────────────────────────────────────────────────
#     total: int = (
#         await db.execute(select(func.count()).select_from(AgentEvent).where(where))
#     ).scalar_one()

#     # ── Paginated rows ─────────────────────────────────────────────────
#     rows = (
#         await db.execute(
#             select(AgentEvent)
#             .where(where)
#             .order_by(desc(AgentEvent.timestamp))
#             .offset(skip)
#             .limit(limit)
#         )
#     ).scalars().all()

#     # ── Enrich with agent names ────────────────────────────────────────
#     agent_ids = {e.agent_id for e in rows}
#     name_map  = await _agent_name_map(db, agent_ids)

#     violations = [
#         ViolationSchema(
#             id=e.id,
#             run_id=e.run_id,
#             agent_id=e.agent_id,
#             agent_name=name_map.get(e.agent_id),
#             trace_id=e.trace_id,
#             tool_name=e.tool_name,
#             # input_data IS the attempted input captured by the governance proxy
#             attempted_input=e.input_data,
#             # denial_message is nested in output_data JSON
#             denial_message=(e.output_data or {}).get("denial_message"),
#             timestamp=e.timestamp,
#         )
#         for e in rows
#     ]

#     return ViolationListResponse(
#         violations=violations, total=total, skip=skip, limit=limit
#     )


# # ─────────────────────────────────────────────────────────────────────────────
# # Run history queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_runs(
#     db: AsyncSession,
#     *,
#     agent_id: str | None = None,
#     status: str | None = None,
#     skip: int = 0,
#     limit: int = 50,
# ) -> RunListResponse:
#     """
#     Return a paginated list of AgentRun rows with summary fields.

#     Each RunSummary includes a violation_count computed from the
#     agent_events table (one sub-count query per run would be N+1;
#     instead we aggregate in Python after loading both result sets).

#     Args:
#         db:       Async DB session.
#         agent_id: Filter to runs for a specific agent.
#         status:   Filter by status ('completed', 'failed', 'running').
#         skip:     Pagination offset.
#         limit:    Page size (clamped to 200).

#     Returns:
#         RunListResponse with runs list and total count.
#     """
#     limit = _clamp_limit(limit)

#     filters = []
#     if agent_id:
#         filters.append(AgentRun.agent_id == agent_id)
#     if status:
#         filters.append(AgentRun.status == status)

#     where = and_(*filters) if filters else True

#     # ── Total count ────────────────────────────────────────────────────
#     total: int = (
#         await db.execute(select(func.count()).select_from(AgentRun).where(where))
#     ).scalar_one()

#     # ── Paginated runs ─────────────────────────────────────────────────
#     run_rows = (
#         await db.execute(
#             select(AgentRun)
#             .where(where)
#             .order_by(desc(AgentRun.started_at))
#             .offset(skip)
#             .limit(limit)
#         )
#     ).scalars().all()

#     if not run_rows:
#         return RunListResponse(runs=[], total=total, skip=skip, limit=limit)

#     # ── Violation counts per run (single query, not N+1) ────────────────
#     # GROUP BY run_id to count violation events for each run in one shot.
#     run_ids = [r.id for r in run_rows]
#     viol_q = await db.execute(
#         select(
#             AgentEvent.run_id,
#             func.count().label("vcount"),
#         )
#         .where(
#             and_(
#                 AgentEvent.run_id.in_(run_ids),
#                 AgentEvent.event_type == "violation",
#             )
#         )
#         .group_by(AgentEvent.run_id)
#     )
#     violation_counts: dict[str, int] = {row.run_id: row.vcount for row in viol_q.all()}

#     # ── Agent names ───────────────────────────────────────────────────
#     agent_ids = {r.agent_id for r in run_rows}
#     name_map  = await _agent_name_map(db, agent_ids)

#     # ── Build summaries ───────────────────────────────────────────────
#     summaries = []
#     for r in run_rows:
#         # Compute latency_ms from stored timestamps (same logic as execution_service)
#         latency_ms: float | None = None
#         if r.ended_at and r.started_at:
#             latency_ms = round(
#                 (r.ended_at - r.started_at).total_seconds() * 1000, 2
#             )

#         summaries.append(
#             RunSummary(
#                 id=r.id,
#                 agent_id=r.agent_id,
#                 agent_name=name_map.get(r.agent_id),
#                 prompt=r.prompt,
#                 status=r.status,
#                 result=r.result,
#                 trace_id=r.trace_id,
#                 started_at=r.started_at,
#                 ended_at=r.ended_at,
#                 latency_ms=latency_ms,
#                 violation_count=violation_counts.get(r.id, 0),
#             )
#         )

#     return RunListResponse(runs=summaries, total=total, skip=skip, limit=limit)


# # ─────────────────────────────────────────────────────────────────────────────
# # Analytics queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_tool_latency_stats(db: AsyncSession) -> list[ToolLatencyStat]:
#     """
#     Return avg and approximate P95 latency for every tool.

#     Two-step process:
#       1. Fetch avg + count per tool in a single GROUP BY query.
#       2. For each tool, fetch sorted latency_ms values to compute P95.

#     The P95 step is O(N) per tool.  With expected call counts in the
#     hundreds, total time is well under 10 ms on SQLite.

#     PostgreSQL equivalent for step 2 (much more efficient at scale):
#         SELECT tool_name,
#                AVG(latency_ms),
#                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)
#         FROM agent_events
#         WHERE event_type = 'tool_end' AND latency_ms IS NOT NULL
#         GROUP BY tool_name

#     Returns:
#         List of ToolLatencyStat, one per distinct tool name, sorted
#         by call_count descending (most-used tools first).
#     """
#     # ── Step 1: avg + count per tool ──────────────────────────────────
#     agg_rows = (
#         await db.execute(
#             select(
#                 AgentEvent.tool_name,
#                 func.avg(AgentEvent.latency_ms).label("avg_ms"),
#                 func.count().label("call_count"),
#             )
#             .where(
#                 and_(
#                     AgentEvent.event_type == "tool_end",
#                     AgentEvent.latency_ms.is_not(None),
#                 )
#             )
#             .group_by(AgentEvent.tool_name)
#             .order_by(desc("call_count"))
#         )
#     ).all()

#     if not agg_rows:
#         return []

#     stats: list[ToolLatencyStat] = []

#     # ── Step 2: P95 per tool ──────────────────────────────────────────
#     for row in agg_rows:
#         tool_name  = row.tool_name
#         avg_ms     = round(row.avg_ms, 2) if row.avg_ms is not None else None
#         call_count = row.call_count

#         # Fetch sorted latency values for this tool to approximate P95.
#         lat_rows = (
#             await db.execute(
#                 select(AgentEvent.latency_ms)
#                 .where(
#                     and_(
#                         AgentEvent.event_type == "tool_end",
#                         AgentEvent.tool_name  == tool_name,
#                         AgentEvent.latency_ms.is_not(None),
#                     )
#                 )
#                 .order_by(asc(AgentEvent.latency_ms))
#             )
#         ).all()

#         latencies = [r[0] for r in lat_rows]
#         p95_ms: float | None = None
#         if latencies:
#             # Index of the 95th-percentile element (0-based, clamped to last).
#             # floor(N * 0.95) gives us the element just below the 95% mark
#             # which is the standard "nearest rank" method.
#             idx   = max(0, int(len(latencies) * 0.95) - 1)
#             p95_ms = round(latencies[idx], 2)

#         stats.append(
#             ToolLatencyStat(
#                 tool_name=tool_name,
#                 avg_ms=avg_ms,
#                 p95_ms=p95_ms,
#                 call_count=call_count,
#             )
#         )

#     return stats


# async def get_agent_stats(db: AsyncSession, agent_id: str) -> AgentStats:
#     """
#     Compute aggregate statistics for a single agent.

#     Executes five queries:
#       1. Agent name lookup (+ existence check — raises if not found).
#       2. Run counts grouped by status.
#       3. Event counts grouped by event_type.
#       4. Average run latency across completed runs.
#       5. Distinct tool names from permitted tool_end events.

#     Args:
#         db:       Async DB session.
#         agent_id: UUID of the agent to analyse.

#     Returns:
#         AgentStats with all counters populated.

#     Raises:
#         ValueError: Agent not found (router converts to 404).
#     """
#     # ── 1. Agent lookup ───────────────────────────────────────────────
#     agent_row = (
#         await db.execute(select(Agent).where(Agent.id == agent_id))
#     ).scalar_one_or_none()

#     if agent_row is None:
#         raise ValueError(f"Agent '{agent_id}' not found.")

#     # ── 2. Run counts by status ────────────────────────────────────────
#     run_counts_rows = (
#         await db.execute(
#             select(AgentRun.status, func.count().label("n"))
#             .where(AgentRun.agent_id == agent_id)
#             .group_by(AgentRun.status)
#         )
#     ).all()

#     run_counts: dict[str, int] = {row.status: row.n for row in run_counts_rows}
#     total_runs     = sum(run_counts.values())
#     completed_runs = run_counts.get("completed", 0)
#     failed_runs    = run_counts.get("failed", 0)

#     # ── 3. Event counts by type ────────────────────────────────────────
#     evt_counts_rows = (
#         await db.execute(
#             select(AgentEvent.event_type, func.count().label("n"))
#             .where(AgentEvent.agent_id == agent_id)
#             .group_by(AgentEvent.event_type)
#         )
#     ).all()

#     evt_counts: dict[str, int] = {row.event_type: row.n for row in evt_counts_rows}
#     total_events     = sum(evt_counts.values())
#     total_tool_calls = evt_counts.get("tool_call", 0)
#     total_violations = evt_counts.get("violation", 0)
#     violation_rate   = (
#         round(total_violations / total_tool_calls * 100, 1)
#         if total_tool_calls > 0 else 0.0
#     )

#     # ── 4. Average run latency (completed runs only) ───────────────────
#     # julianday() converts a datetime to a fractional day number.
#     # Subtracting start from end gives days; multiply by 86_400_000 for ms.
#     avg_lat_row = (
#         await db.execute(
#             select(
#                 func.avg(
#                     (
#                         func.julianday(AgentRun.ended_at)
#                         - func.julianday(AgentRun.started_at)
#                     ) * 86_400_000
#                 ).label("avg_ms")
#             )
#             .where(
#                 and_(
#                     AgentRun.agent_id == agent_id,
#                     AgentRun.status   == "completed",
#                     AgentRun.ended_at.is_not(None),
#                 )
#             )
#         )
#     ).one()

#     avg_run_latency_ms = (
#         round(avg_lat_row.avg_ms, 2) if avg_lat_row.avg_ms is not None else None
#     )

#     # ── 5. Distinct tools successfully used ───────────────────────────
#     tools_rows = (
#         await db.execute(
#             select(AgentEvent.tool_name)
#             .where(
#                 and_(
#                     AgentEvent.agent_id   == agent_id,
#                     AgentEvent.event_type == "tool_end",
#                     AgentEvent.permitted  == True,  # noqa: E712
#                     AgentEvent.tool_name.is_not(None),
#                 )
#             )
#             .distinct()
#         )
#     ).all()

#     tools_used = sorted([row[0] for row in tools_rows])

#     return AgentStats(
#         agent_id=agent_id,
#         agent_name=agent_row.name,
#         total_runs=total_runs,
#         completed_runs=completed_runs,
#         failed_runs=failed_runs,
#         total_events=total_events,
#         total_tool_calls=total_tool_calls,
#         total_violations=total_violations,
#         violation_rate=violation_rate,
#         avg_run_latency_ms=avg_run_latency_ms,
#         tools_used=tools_used,
#     )


# async def get_system_stats(db: AsyncSession) -> SystemStats:
#     """
#     Compute platform-wide aggregate counters for the dashboard header.

#     Executes three queries:
#       1. Scalar counts: agents, runs (with status breakdown), events, tool_calls, violations.
#       2. Tool latency stats (delegates to get_tool_latency_stats).

#     Returns:
#         SystemStats with all counters and tool latency data.
#     """
#     # ── Counts ────────────────────────────────────────────────────────
#     total_agents: int = (
#         await db.execute(select(func.count()).select_from(Agent))
#     ).scalar_one()

#     # Run counts in one GROUP BY query
#     run_count_rows = (
#         await db.execute(
#             select(AgentRun.status, func.count().label("n"))
#             .group_by(AgentRun.status)
#         )
#     ).all()
#     run_counts     = {row.status: row.n for row in run_count_rows}
#     total_runs     = sum(run_counts.values())
#     completed_runs = run_counts.get("completed", 0)
#     failed_runs    = run_counts.get("failed", 0)

#     # Event counts in one GROUP BY query
#     evt_count_rows = (
#         await db.execute(
#             select(AgentEvent.event_type, func.count().label("n"))
#             .group_by(AgentEvent.event_type)
#         )
#     ).all()
#     evt_counts       = {row.event_type: row.n for row in evt_count_rows}
#     total_events     = sum(evt_counts.values())
#     total_tool_calls = evt_counts.get("tool_call", 0)
#     total_violations = evt_counts.get("violation", 0)
#     violation_rate   = (
#         round(total_violations / total_tool_calls * 100, 1)
#         if total_tool_calls > 0 else 0.0
#     )

#     # ── Tool latency ──────────────────────────────────────────────────
#     tool_latency = await get_tool_latency_stats(db)

#     return SystemStats(
#         total_agents=total_agents,
#         total_runs=total_runs,
#         total_events=total_events,
#         total_tool_calls=total_tool_calls,
#         total_violations=total_violations,
#         violation_rate=violation_rate,
#         completed_runs=completed_runs,
#         failed_runs=failed_runs,
#         tool_latency=tool_latency,
#     )




# """
# app/services/audit_service.py
# ──────────────────────────────
# All read-only database queries for the audit, governance, and analytics
# endpoints.  No business logic lives in the routers — every SQL statement
# is here so it can be tested independently.

# Query design principles:
#   1. Single async query per operation where possible.
#      COUNT + paginated SELECT are always two separate queries because
#      SQLAlchemy's async API does not support combined count+fetch natively.
#      We accept the two round-trips; SQLite latency is sub-millisecond.

#   2. All joins use explicit ON clauses (not implicit foreign-key magic)
#      for clarity when reading the code without an ORM reference open.

#   3. Pagination is OFFSET/LIMIT.  For very large tables a keyset approach
#      (WHERE id > last_seen_id) would be more efficient, but for an MVP
#      with SQLite and expected row counts in the thousands, OFFSET is fine.

#   4. P95 latency approximation for SQLite:
#      SQLite has no PERCENTILE_CONT aggregate.  We approximate by:
#        a. Fetching all latency_ms values for the tool in sorted order.
#        b. Taking the row at index floor(N * 0.95).
#      This is O(N) in the number of tool_end rows per tool.  With expected
#      call counts in the hundreds this is fast enough.  If you migrate to
#      PostgreSQL, replace with:
#        SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)

#   5. All timestamps from SQLite are timezone-naive.  The service returns
#      them as-is; the Pydantic schema serialises to ISO-8601 strings in
#      UTC (by convention — the storage layer is naive).
# """

# import logging
# from datetime import datetime

# from sqlalchemy import select, func, and_, desc, asc
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.models.agent import Agent, AgentRun, AgentEvent
# # Interaction counts are fetched lazily to avoid circular imports.
# # interaction_service is imported inside get_system_stats() below.
# from app.schemas.audit import (
#     AuditEventSchema,
#     AuditLogResponse,
#     ViolationSchema,
#     ViolationListResponse,
#     RunSummary,
#     RunListResponse,
#     ToolLatencyStat,
#     AgentStats,
#     SystemStats,
# )

# logger = logging.getLogger(__name__)


# # ─────────────────────────────────────────────────────────────────────────────
# # Internal helpers
# # ─────────────────────────────────────────────────────────────────────────────

# def _clamp_limit(limit: int, maximum: int = 200) -> int:
#     """Prevent accidental huge payloads by enforcing a per-endpoint ceiling."""
#     return min(max(1, limit), maximum)


# async def _agent_name_map(db: AsyncSession, agent_ids: set[str]) -> dict[str, str]:
#     """
#     Return {agent_id: agent_name} for a set of agent UUIDs.

#     Used to enrich event/run responses without N+1 queries: we collect
#     all unique agent_ids from the result set first, then load names in
#     a single IN query.

#     Args:
#         db:        Async DB session.
#         agent_ids: Set of agent UUID strings.

#     Returns:
#         Dict mapping agent_id → name.  Missing agents map to None.
#     """
#     if not agent_ids:
#         return {}

#     result = await db.execute(
#         select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids))
#     )
#     return {row.id: row.name for row in result.all()}


# # ─────────────────────────────────────────────────────────────────────────────
# # Audit log queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_audit_logs(
#     db: AsyncSession,
#     *,
#     agent_id: str | None = None,
#     event_type: str | None = None,
#     run_id: str | None = None,
#     skip: int = 0,
#     limit: int = 50,
# ) -> AuditLogResponse:
#     """
#     Return a paginated, filtered list of AgentEvent rows.

#     All filter parameters are optional and additive (AND logic).
#     Results are ordered by timestamp DESC (newest first) so the
#     caller always sees the most recent activity at the top.

#     Args:
#         db:         Async DB session.
#         agent_id:   Filter to events for a specific agent UUID.
#         event_type: Filter to a specific event type string
#                     (run_start | tool_call | tool_end | violation | run_end |
#                      agent_handoff).
#         run_id:     Filter to events for a specific run UUID.
#         skip:       Pagination offset.
#         limit:      Page size (clamped to 200).

#     Returns:
#         AuditLogResponse with events list and total count.
#     """
#     limit = _clamp_limit(limit)

#     # Build the shared WHERE clause once — reused for COUNT and SELECT.
#     filters = []
#     if agent_id:
#         filters.append(AgentEvent.agent_id == agent_id)
#     if event_type:
#         filters.append(AgentEvent.event_type == event_type)
#     if run_id:
#         filters.append(AgentEvent.run_id == run_id)

#     where = and_(*filters) if filters else True

#     # ── Total count ───────────────────────────────────────────────────
#     count_q = select(func.count()).select_from(AgentEvent).where(where)
#     total: int = (await db.execute(count_q)).scalar_one()

#     # ── Paginated rows ────────────────────────────────────────────────
#     rows_q = (
#         select(AgentEvent)
#         .where(where)
#         .order_by(desc(AgentEvent.timestamp))
#         .offset(skip)
#         .limit(limit)
#     )
#     rows = (await db.execute(rows_q)).scalars().all()

#     # ── Enrich with agent names (single IN query) ──────────────────────
#     agent_ids = {e.agent_id for e in rows}
#     name_map  = await _agent_name_map(db, agent_ids)

#     events = [
#         AuditEventSchema(
#             id=e.id,
#             run_id=e.run_id,
#             agent_id=e.agent_id,
#             trace_id=e.trace_id,
#             event_type=e.event_type,
#             tool_name=e.tool_name,
#             input_data=e.input_data,
#             output_data=e.output_data,
#             permitted=e.permitted,
#             latency_ms=e.latency_ms,
#             timestamp=e.timestamp,
#             agent_name=name_map.get(e.agent_id),
#         )
#         for e in rows
#     ]

#     return AuditLogResponse(events=events, total=total, skip=skip, limit=limit)


# async def get_audit_logs_for_agent(
#     db: AsyncSession,
#     agent_id: str,
#     *,
#     skip: int = 0,
#     limit: int = 50,
# ) -> AuditLogResponse:
#     """
#     Convenience wrapper — audit log filtered to a single agent.

#     Equivalent to get_audit_logs(db, agent_id=agent_id, ...) but
#     provides a cleaner call site for the GET /audit/logs/{agent_id} router.
#     """
#     return await get_audit_logs(
#         db, agent_id=agent_id, skip=skip, limit=limit
#     )


# # ─────────────────────────────────────────────────────────────────────────────
# # Governance / violations queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_violations(
#     db: AsyncSession,
#     *,
#     agent_id: str | None = None,
#     skip: int = 0,
#     limit: int = 50,
# ) -> ViolationListResponse:
#     """
#     Return a paginated list of governance violation events.

#     A violation event is an AgentEvent where:
#       event_type = 'violation'  AND  permitted = False

#     The denial_message is extracted from event.output_data["denial_message"]
#     and surfaced as a top-level field so callers don't need to parse JSON.

#     Args:
#         db:       Async DB session.
#         agent_id: Optional filter to one agent's violations.
#         skip:     Pagination offset.
#         limit:    Page size (clamped to 200).

#     Returns:
#         ViolationListResponse with violations list and total count.
#     """
#     limit = _clamp_limit(limit)

#     filters = [
#         AgentEvent.event_type == "violation",
#         AgentEvent.permitted  == False,  # noqa: E712 — SQLAlchemy needs == False
#     ]
#     if agent_id:
#         filters.append(AgentEvent.agent_id == agent_id)

#     where = and_(*filters)

#     # ── Total count ───────────────────────────────────────────────────
#     total: int = (
#         await db.execute(select(func.count()).select_from(AgentEvent).where(where))
#     ).scalar_one()

#     # ── Paginated rows ─────────────────────────────────────────────────
#     rows = (
#         await db.execute(
#             select(AgentEvent)
#             .where(where)
#             .order_by(desc(AgentEvent.timestamp))
#             .offset(skip)
#             .limit(limit)
#         )
#     ).scalars().all()

#     # ── Enrich with agent names ────────────────────────────────────────
#     agent_ids = {e.agent_id for e in rows}
#     name_map  = await _agent_name_map(db, agent_ids)

#     violations = [
#         ViolationSchema(
#             id=e.id,
#             run_id=e.run_id,
#             agent_id=e.agent_id,
#             agent_name=name_map.get(e.agent_id),
#             trace_id=e.trace_id,
#             tool_name=e.tool_name,
#             # input_data IS the attempted input captured by the governance proxy
#             attempted_input=e.input_data,
#             # denial_message is nested in output_data JSON
#             denial_message=(e.output_data or {}).get("denial_message"),
#             timestamp=e.timestamp,
#         )
#         for e in rows
#     ]

#     return ViolationListResponse(
#         violations=violations, total=total, skip=skip, limit=limit
#     )


# # ─────────────────────────────────────────────────────────────────────────────
# # Run history queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_runs(
#     db: AsyncSession,
#     *,
#     agent_id: str | None = None,
#     status: str | None = None,
#     skip: int = 0,
#     limit: int = 50,
# ) -> RunListResponse:
#     """
#     Return a paginated list of AgentRun rows with summary fields.

#     Each RunSummary includes a violation_count computed from the
#     agent_events table (one sub-count query per run would be N+1;
#     instead we aggregate in Python after loading both result sets).

#     Args:
#         db:       Async DB session.
#         agent_id: Filter to runs for a specific agent.
#         status:   Filter by status ('completed', 'failed', 'running').
#         skip:     Pagination offset.
#         limit:    Page size (clamped to 200).

#     Returns:
#         RunListResponse with runs list and total count.
#     """
#     limit = _clamp_limit(limit)

#     filters = []
#     if agent_id:
#         filters.append(AgentRun.agent_id == agent_id)
#     if status:
#         filters.append(AgentRun.status == status)

#     where = and_(*filters) if filters else True

#     # ── Total count ────────────────────────────────────────────────────
#     total: int = (
#         await db.execute(select(func.count()).select_from(AgentRun).where(where))
#     ).scalar_one()

#     # ── Paginated runs ─────────────────────────────────────────────────
#     run_rows = (
#         await db.execute(
#             select(AgentRun)
#             .where(where)
#             .order_by(desc(AgentRun.started_at))
#             .offset(skip)
#             .limit(limit)
#         )
#     ).scalars().all()

#     if not run_rows:
#         return RunListResponse(runs=[], total=total, skip=skip, limit=limit)

#     # ── Violation counts per run (single query, not N+1) ────────────────
#     run_ids = [r.id for r in run_rows]
#     viol_q = await db.execute(
#         select(
#             AgentEvent.run_id,
#             func.count().label("vcount"),
#         )
#         .where(
#             and_(
#                 AgentEvent.run_id.in_(run_ids),
#                 AgentEvent.event_type == "violation",
#             )
#         )
#         .group_by(AgentEvent.run_id)
#     )
#     violation_counts: dict[str, int] = {row.run_id: row.vcount for row in viol_q.all()}

#     # ── Agent names ───────────────────────────────────────────────────
#     agent_ids = {r.agent_id for r in run_rows}
#     name_map  = await _agent_name_map(db, agent_ids)

#     # ── Build summaries ───────────────────────────────────────────────
#     summaries = []
#     for r in run_rows:
#         latency_ms: float | None = None
#         if r.ended_at and r.started_at:
#             latency_ms = round(
#                 (r.ended_at - r.started_at).total_seconds() * 1000, 2
#             )

#         summaries.append(
#             RunSummary(
#                 id=r.id,
#                 agent_id=r.agent_id,
#                 agent_name=name_map.get(r.agent_id),
#                 prompt=r.prompt,
#                 status=r.status,
#                 result=r.result,
#                 trace_id=r.trace_id,
#                 started_at=r.started_at,
#                 ended_at=r.ended_at,
#                 latency_ms=latency_ms,
#                 violation_count=violation_counts.get(r.id, 0),
#             )
#         )

#     return RunListResponse(runs=summaries, total=total, skip=skip, limit=limit)


# # ─────────────────────────────────────────────────────────────────────────────
# # Analytics queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_tool_latency_stats(db: AsyncSession) -> list[ToolLatencyStat]:
#     """
#     Return avg and approximate P95 latency for every tool.

#     Two-step process:
#       1. Fetch avg + count per tool in a single GROUP BY query.
#       2. For each tool, fetch sorted latency_ms values to compute P95.

#     PostgreSQL equivalent for step 2:
#         SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)

#     Returns:
#         List of ToolLatencyStat, one per distinct tool name, sorted
#         by call_count descending (most-used tools first).
#     """
#     agg_rows = (
#         await db.execute(
#             select(
#                 AgentEvent.tool_name,
#                 func.avg(AgentEvent.latency_ms).label("avg_ms"),
#                 func.count().label("call_count"),
#             )
#             .where(
#                 and_(
#                     AgentEvent.event_type == "tool_end",
#                     AgentEvent.latency_ms.is_not(None),
#                 )
#             )
#             .group_by(AgentEvent.tool_name)
#             .order_by(desc("call_count"))
#         )
#     ).all()

#     if not agg_rows:
#         return []

#     stats: list[ToolLatencyStat] = []

#     for row in agg_rows:
#         tool_name  = row.tool_name
#         avg_ms     = round(row.avg_ms, 2) if row.avg_ms is not None else None
#         call_count = row.call_count

#         lat_rows = (
#             await db.execute(
#                 select(AgentEvent.latency_ms)
#                 .where(
#                     and_(
#                         AgentEvent.event_type == "tool_end",
#                         AgentEvent.tool_name  == tool_name,
#                         AgentEvent.latency_ms.is_not(None),
#                     )
#                 )
#                 .order_by(asc(AgentEvent.latency_ms))
#             )
#         ).all()

#         latencies = [r[0] for r in lat_rows]
#         p95_ms: float | None = None
#         if latencies:
#             idx    = max(0, int(len(latencies) * 0.95) - 1)
#             p95_ms = round(latencies[idx], 2)

#         stats.append(
#             ToolLatencyStat(
#                 tool_name=tool_name,
#                 avg_ms=avg_ms,
#                 p95_ms=p95_ms,
#                 call_count=call_count,
#             )
#         )

#     return stats


# async def get_agent_stats(db: AsyncSession, agent_id: str) -> AgentStats:
#     """
#     Compute aggregate statistics for a single agent.

#     Args:
#         db:       Async DB session.
#         agent_id: UUID of the agent to analyse.

#     Returns:
#         AgentStats with all counters populated.

#     Raises:
#         ValueError: Agent not found (router converts to 404).
#     """
#     agent_row = (
#         await db.execute(select(Agent).where(Agent.id == agent_id))
#     ).scalar_one_or_none()

#     if agent_row is None:
#         raise ValueError(f"Agent '{agent_id}' not found.")

#     run_counts_rows = (
#         await db.execute(
#             select(AgentRun.status, func.count().label("n"))
#             .where(AgentRun.agent_id == agent_id)
#             .group_by(AgentRun.status)
#         )
#     ).all()

#     run_counts: dict[str, int] = {row.status: row.n for row in run_counts_rows}
#     total_runs     = sum(run_counts.values())
#     completed_runs = run_counts.get("completed", 0)
#     failed_runs    = run_counts.get("failed", 0)

#     evt_counts_rows = (
#         await db.execute(
#             select(AgentEvent.event_type, func.count().label("n"))
#             .where(AgentEvent.agent_id == agent_id)
#             .group_by(AgentEvent.event_type)
#         )
#     ).all()

#     evt_counts: dict[str, int] = {row.event_type: row.n for row in evt_counts_rows}
#     total_events     = sum(evt_counts.values())
#     total_tool_calls = evt_counts.get("tool_call", 0)
#     total_violations = evt_counts.get("violation", 0)
#     violation_rate   = (
#         round(total_violations / total_tool_calls * 100, 1)
#         if total_tool_calls > 0 else 0.0
#     )

#     avg_lat_row = (
#         await db.execute(
#             select(
#                 func.avg(
#                     (
#                         func.julianday(AgentRun.ended_at)
#                         - func.julianday(AgentRun.started_at)
#                     ) * 86_400_000
#                 ).label("avg_ms")
#             )
#             .where(
#                 and_(
#                     AgentRun.agent_id == agent_id,
#                     AgentRun.status   == "completed",
#                     AgentRun.ended_at.is_not(None),
#                 )
#             )
#         )
#     ).one()

#     avg_run_latency_ms = (
#         round(avg_lat_row.avg_ms, 2) if avg_lat_row.avg_ms is not None else None
#     )

#     tools_rows = (
#         await db.execute(
#             select(AgentEvent.tool_name)
#             .where(
#                 and_(
#                     AgentEvent.agent_id   == agent_id,
#                     AgentEvent.event_type == "tool_end",
#                     AgentEvent.permitted  == True,  # noqa: E712
#                     AgentEvent.tool_name.is_not(None),
#                 )
#             )
#             .distinct()
#         )
#     ).all()

#     tools_used = sorted([row[0] for row in tools_rows])

#     return AgentStats(
#         agent_id=agent_id,
#         agent_name=agent_row.name,
#         total_runs=total_runs,
#         completed_runs=completed_runs,
#         failed_runs=failed_runs,
#         total_events=total_events,
#         total_tool_calls=total_tool_calls,
#         total_violations=total_violations,
#         violation_rate=violation_rate,
#         avg_run_latency_ms=avg_run_latency_ms,
#         tools_used=tools_used,
#     )


# async def get_system_stats(db: AsyncSession) -> SystemStats:
#     """
#     Compute platform-wide aggregate counters for the dashboard header.

#     Returns:
#         SystemStats with all counters and tool latency data.
#     """
#     total_agents: int = (
#         await db.execute(select(func.count()).select_from(Agent))
#     ).scalar_one()

#     run_count_rows = (
#         await db.execute(
#             select(AgentRun.status, func.count().label("n"))
#             .group_by(AgentRun.status)
#         )
#     ).all()
#     run_counts     = {row.status: row.n for row in run_count_rows}
#     total_runs     = sum(run_counts.values())
#     completed_runs = run_counts.get("completed", 0)
#     failed_runs    = run_counts.get("failed", 0)

#     evt_count_rows = (
#         await db.execute(
#             select(AgentEvent.event_type, func.count().label("n"))
#             .group_by(AgentEvent.event_type)
#         )
#     ).all()
#     evt_counts       = {row.event_type: row.n for row in evt_count_rows}
#     total_events     = sum(evt_counts.values())
#     total_tool_calls = evt_counts.get("tool_call", 0)
#     total_violations = evt_counts.get("violation", 0)
#     violation_rate   = (
#         round(total_violations / total_tool_calls * 100, 1)
#         if total_tool_calls > 0 else 0.0
#     )

#     tool_latency = await get_tool_latency_stats(db)

#     # ── Interaction counts (Phase 2) ─────────────────────────────────
#     # Imported inside the function to avoid circular imports between
#     # audit_service and interaction_service.
#     from app.services.interaction_service import get_interaction_counts
#     interaction_counts = await get_interaction_counts(db)

#     return SystemStats(
#         total_agents=total_agents,
#         total_runs=total_runs,
#         total_events=total_events,
#         total_tool_calls=total_tool_calls,
#         total_violations=total_violations,
#         violation_rate=violation_rate,
#         completed_runs=completed_runs,
#         failed_runs=failed_runs,
#         tool_latency=tool_latency,
#         total_interactions=interaction_counts["total_interactions"],
#         interactions_by_type=interaction_counts["interactions_by_type"],
#         interactions_by_agent=interaction_counts["interactions_by_agent"],
#     )
























# """
# app/services/audit_service.py
# ──────────────────────────────
# All read-only database queries for the audit, governance, and analytics
# endpoints.  No business logic lives in the routers — every SQL statement
# is here so it can be tested independently.

# Query design principles:
#   1. Single async query per operation where possible.
#      COUNT + paginated SELECT are always two separate queries because
#      SQLAlchemy's async API does not support combined count+fetch natively.
#      We accept the two round-trips; SQLite latency is sub-millisecond.

#   2. All joins use explicit ON clauses (not implicit foreign-key magic)
#      for clarity when reading the code without an ORM reference open.

#   3. Pagination is OFFSET/LIMIT.  For very large tables a keyset approach
#      (WHERE id > last_seen_id) would be more efficient, but for an MVP
#      with SQLite and expected row counts in the thousands, OFFSET is fine.

#   4. P95 latency approximation for SQLite:
#      SQLite has no PERCENTILE_CONT aggregate.  We approximate by:
#        a. Fetching all latency_ms values for the tool in sorted order.
#        b. Taking the row at index floor(N * 0.95).
#      This is O(N) in the number of tool_end rows per tool.  With expected
#      call counts in the hundreds this is fast enough.  If you migrate to
#      PostgreSQL, replace with:
#        SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)

#   5. All timestamps from SQLite are timezone-naive.  The service returns
#      them as-is; the Pydantic schema serialises to ISO-8601 strings in
#      UTC (by convention — the storage layer is naive).
# """

# import logging
# from datetime import datetime

# from sqlalchemy import select, func, and_, desc, asc
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.models.agent import Agent, AgentRun, AgentEvent
# # Interaction counts are fetched lazily to avoid circular imports.
# # interaction_service is imported inside get_system_stats() below.
# from app.schemas.audit import (
#     AuditEventSchema,
#     AuditLogResponse,
#     ViolationSchema,
#     ViolationListResponse,
#     RunSummary,
#     RunListResponse,
#     ToolLatencyStat,
#     AgentStats,
#     SystemStats,
# )

# logger = logging.getLogger(__name__)


# # ─────────────────────────────────────────────────────────────────────────────
# # Internal helpers
# # ─────────────────────────────────────────────────────────────────────────────

# def _clamp_limit(limit: int, maximum: int = 200) -> int:
#     """Prevent accidental huge payloads by enforcing a per-endpoint ceiling."""
#     return min(max(1, limit), maximum)


# async def _agent_name_map(db: AsyncSession, agent_ids: set[str]) -> dict[str, str]:
#     """
#     Return {agent_id: agent_name} for a set of agent UUIDs.

#     Used to enrich event/run responses without N+1 queries: we collect
#     all unique agent_ids from the result set first, then load names in
#     a single IN query.

#     Args:
#         db:        Async DB session.
#         agent_ids: Set of agent UUID strings.

#     Returns:
#         Dict mapping agent_id → name.  Missing agents map to None.
#     """
#     if not agent_ids:
#         return {}

#     result = await db.execute(
#         select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids))
#     )
#     return {row.id: row.name for row in result.all()}


# # ─────────────────────────────────────────────────────────────────────────────
# # Audit log queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_audit_logs(
#     db: AsyncSession,
#     *,
#     agent_id: str | None = None,
#     event_type: str | None = None,
#     run_id: str | None = None,
#     skip: int = 0,
#     limit: int = 50,
# ) -> AuditLogResponse:
#     """
#     Return a paginated, filtered list of AgentEvent rows.

#     All filter parameters are optional and additive (AND logic).
#     Results are ordered by timestamp DESC (newest first) so the
#     caller always sees the most recent activity at the top.

#     Args:
#         db:         Async DB session.
#         agent_id:   Filter to events for a specific agent UUID.
#         event_type: Filter to a specific event type string
#                     (run_start | tool_call | tool_end | violation | run_end).
#         run_id:     Filter to events for a specific run UUID.
#         skip:       Pagination offset.
#         limit:      Page size (clamped to 200).

#     Returns:
#         AuditLogResponse with events list and total count.
#     """
#     limit = _clamp_limit(limit)

#     # Build the shared WHERE clause once — reused for COUNT and SELECT.
#     filters = []
#     if agent_id:
#         filters.append(AgentEvent.agent_id == agent_id)
#     if event_type:
#         filters.append(AgentEvent.event_type == event_type)
#     if run_id:
#         filters.append(AgentEvent.run_id == run_id)

#     where = and_(*filters) if filters else True

#     # ── Total count ───────────────────────────────────────────────────
#     count_q = select(func.count()).select_from(AgentEvent).where(where)
#     total: int = (await db.execute(count_q)).scalar_one()

#     # ── Paginated rows ────────────────────────────────────────────────
#     rows_q = (
#         select(AgentEvent)
#         .where(where)
#         .order_by(desc(AgentEvent.timestamp))
#         .offset(skip)
#         .limit(limit)
#     )
#     rows = (await db.execute(rows_q)).scalars().all()

#     # ── Enrich with agent names (single IN query) ──────────────────────
#     agent_ids = {e.agent_id for e in rows}
#     name_map  = await _agent_name_map(db, agent_ids)

#     events = [
#         AuditEventSchema(
#             id=e.id,
#             run_id=e.run_id,
#             agent_id=e.agent_id,
#             trace_id=e.trace_id,
#             event_type=e.event_type,
#             tool_name=e.tool_name,
#             input_data=e.input_data,
#             output_data=e.output_data,
#             permitted=e.permitted,
#             latency_ms=e.latency_ms,
#             timestamp=e.timestamp,
#             agent_name=name_map.get(e.agent_id),
#         )
#         for e in rows
#     ]

#     return AuditLogResponse(events=events, total=total, skip=skip, limit=limit)


# async def get_audit_logs_for_agent(
#     db: AsyncSession,
#     agent_id: str,
#     *,
#     skip: int = 0,
#     limit: int = 50,
# ) -> AuditLogResponse:
#     """
#     Convenience wrapper — audit log filtered to a single agent.

#     Equivalent to get_audit_logs(db, agent_id=agent_id, ...) but
#     provides a cleaner call site for the GET /audit/logs/{agent_id} router.
#     """
#     return await get_audit_logs(
#         db, agent_id=agent_id, skip=skip, limit=limit
#     )


# # ─────────────────────────────────────────────────────────────────────────────
# # Governance / violations queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_violations(
#     db: AsyncSession,
#     *,
#     agent_id: str | None = None,
#     skip: int = 0,
#     limit: int = 50,
# ) -> ViolationListResponse:
#     """
#     Return a paginated list of governance violation events.

#     A violation event is an AgentEvent where:
#       event_type = 'violation'  AND  permitted = False

#     The denial_message is extracted from event.output_data["denial_message"]
#     and surfaced as a top-level field so callers don't need to parse JSON.

#     Args:
#         db:       Async DB session.
#         agent_id: Optional filter to one agent's violations.
#         skip:     Pagination offset.
#         limit:    Page size (clamped to 200).

#     Returns:
#         ViolationListResponse with violations list and total count.
#     """
#     limit = _clamp_limit(limit)

#     filters = [
#         AgentEvent.event_type == "violation",
#         AgentEvent.permitted  == False,  # noqa: E712 — SQLAlchemy needs == False
#     ]
#     if agent_id:
#         filters.append(AgentEvent.agent_id == agent_id)

#     where = and_(*filters)

#     # ── Total count ───────────────────────────────────────────────────
#     total: int = (
#         await db.execute(select(func.count()).select_from(AgentEvent).where(where))
#     ).scalar_one()

#     # ── Paginated rows ─────────────────────────────────────────────────
#     rows = (
#         await db.execute(
#             select(AgentEvent)
#             .where(where)
#             .order_by(desc(AgentEvent.timestamp))
#             .offset(skip)
#             .limit(limit)
#         )
#     ).scalars().all()

#     # ── Enrich with agent names ────────────────────────────────────────
#     agent_ids = {e.agent_id for e in rows}
#     name_map  = await _agent_name_map(db, agent_ids)

#     violations = [
#         ViolationSchema(
#             id=e.id,
#             run_id=e.run_id,
#             agent_id=e.agent_id,
#             agent_name=name_map.get(e.agent_id),
#             trace_id=e.trace_id,
#             tool_name=e.tool_name,
#             # input_data IS the attempted input captured by the governance proxy
#             attempted_input=e.input_data,
#             # denial_message is nested in output_data JSON
#             denial_message=(e.output_data or {}).get("denial_message"),
#             timestamp=e.timestamp,
#         )
#         for e in rows
#     ]

#     return ViolationListResponse(
#         violations=violations, total=total, skip=skip, limit=limit
#     )


# # ─────────────────────────────────────────────────────────────────────────────
# # Run history queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_runs(
#     db: AsyncSession,
#     *,
#     agent_id: str | None = None,
#     status: str | None = None,
#     skip: int = 0,
#     limit: int = 50,
# ) -> RunListResponse:
#     """
#     Return a paginated list of AgentRun rows with summary fields.

#     Each RunSummary includes a violation_count computed from the
#     agent_events table (one sub-count query per run would be N+1;
#     instead we aggregate in Python after loading both result sets).

#     Args:
#         db:       Async DB session.
#         agent_id: Filter to runs for a specific agent.
#         status:   Filter by status ('completed', 'failed', 'running').
#         skip:     Pagination offset.
#         limit:    Page size (clamped to 200).

#     Returns:
#         RunListResponse with runs list and total count.
#     """
#     limit = _clamp_limit(limit)

#     filters = []
#     if agent_id:
#         filters.append(AgentRun.agent_id == agent_id)
#     if status:
#         filters.append(AgentRun.status == status)

#     where = and_(*filters) if filters else True

#     # ── Total count ────────────────────────────────────────────────────
#     total: int = (
#         await db.execute(select(func.count()).select_from(AgentRun).where(where))
#     ).scalar_one()

#     # ── Paginated runs ─────────────────────────────────────────────────
#     run_rows = (
#         await db.execute(
#             select(AgentRun)
#             .where(where)
#             .order_by(desc(AgentRun.started_at))
#             .offset(skip)
#             .limit(limit)
#         )
#     ).scalars().all()

#     if not run_rows:
#         return RunListResponse(runs=[], total=total, skip=skip, limit=limit)

#     # ── Violation counts per run (single query, not N+1) ────────────────
#     # GROUP BY run_id to count violation events for each run in one shot.
#     run_ids = [r.id for r in run_rows]
#     viol_q = await db.execute(
#         select(
#             AgentEvent.run_id,
#             func.count().label("vcount"),
#         )
#         .where(
#             and_(
#                 AgentEvent.run_id.in_(run_ids),
#                 AgentEvent.event_type == "violation",
#             )
#         )
#         .group_by(AgentEvent.run_id)
#     )
#     violation_counts: dict[str, int] = {row.run_id: row.vcount for row in viol_q.all()}

#     # ── Agent names ───────────────────────────────────────────────────
#     agent_ids = {r.agent_id for r in run_rows}
#     name_map  = await _agent_name_map(db, agent_ids)

#     # ── Build summaries ───────────────────────────────────────────────
#     summaries = []
#     for r in run_rows:
#         # Compute latency_ms from stored timestamps (same logic as execution_service)
#         latency_ms: float | None = None
#         if r.ended_at and r.started_at:
#             latency_ms = round(
#                 (r.ended_at - r.started_at).total_seconds() * 1000, 2
#             )

#         summaries.append(
#             RunSummary(
#                 id=r.id,
#                 agent_id=r.agent_id,
#                 agent_name=name_map.get(r.agent_id),
#                 prompt=r.prompt,
#                 status=r.status,
#                 result=r.result,
#                 trace_id=r.trace_id,
#                 started_at=r.started_at,
#                 ended_at=r.ended_at,
#                 latency_ms=latency_ms,
#                 violation_count=violation_counts.get(r.id, 0),
#             )
#         )

#     return RunListResponse(runs=summaries, total=total, skip=skip, limit=limit)


# # ─────────────────────────────────────────────────────────────────────────────
# # Analytics queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_tool_latency_stats(db: AsyncSession) -> list[ToolLatencyStat]:
#     """
#     Return avg and approximate P95 latency for every tool.

#     Two-step process:
#       1. Fetch avg + count per tool in a single GROUP BY query.
#       2. For each tool, fetch sorted latency_ms values to compute P95.

#     The P95 step is O(N) per tool.  With expected call counts in the
#     hundreds, total time is well under 10 ms on SQLite.

#     PostgreSQL equivalent for step 2 (much more efficient at scale):
#         SELECT tool_name,
#                AVG(latency_ms),
#                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)
#         FROM agent_events
#         WHERE event_type = 'tool_end' AND latency_ms IS NOT NULL
#         GROUP BY tool_name

#     Returns:
#         List of ToolLatencyStat, one per distinct tool name, sorted
#         by call_count descending (most-used tools first).
#     """
#     # ── Step 1: avg + count per tool ──────────────────────────────────
#     agg_rows = (
#         await db.execute(
#             select(
#                 AgentEvent.tool_name,
#                 func.avg(AgentEvent.latency_ms).label("avg_ms"),
#                 func.count().label("call_count"),
#             )
#             .where(
#                 and_(
#                     AgentEvent.event_type == "tool_end",
#                     AgentEvent.latency_ms.is_not(None),
#                 )
#             )
#             .group_by(AgentEvent.tool_name)
#             .order_by(desc("call_count"))
#         )
#     ).all()

#     if not agg_rows:
#         return []

#     stats: list[ToolLatencyStat] = []

#     # ── Step 2: P95 per tool ──────────────────────────────────────────
#     for row in agg_rows:
#         tool_name  = row.tool_name
#         avg_ms     = round(row.avg_ms, 2) if row.avg_ms is not None else None
#         call_count = row.call_count

#         # Fetch sorted latency values for this tool to approximate P95.
#         lat_rows = (
#             await db.execute(
#                 select(AgentEvent.latency_ms)
#                 .where(
#                     and_(
#                         AgentEvent.event_type == "tool_end",
#                         AgentEvent.tool_name  == tool_name,
#                         AgentEvent.latency_ms.is_not(None),
#                     )
#                 )
#                 .order_by(asc(AgentEvent.latency_ms))
#             )
#         ).all()

#         latencies = [r[0] for r in lat_rows]
#         p95_ms: float | None = None
#         if latencies:
#             # Index of the 95th-percentile element (0-based, clamped to last).
#             # floor(N * 0.95) gives us the element just below the 95% mark
#             # which is the standard "nearest rank" method.
#             idx   = max(0, int(len(latencies) * 0.95) - 1)
#             p95_ms = round(latencies[idx], 2)

#         stats.append(
#             ToolLatencyStat(
#                 tool_name=tool_name,
#                 avg_ms=avg_ms,
#                 p95_ms=p95_ms,
#                 call_count=call_count,
#             )
#         )

#     return stats


# async def get_agent_stats(db: AsyncSession, agent_id: str) -> AgentStats:
#     """
#     Compute aggregate statistics for a single agent.

#     Executes five queries:
#       1. Agent name lookup (+ existence check — raises if not found).
#       2. Run counts grouped by status.
#       3. Event counts grouped by event_type.
#       4. Average run latency across completed runs.
#       5. Distinct tool names from permitted tool_end events.

#     Args:
#         db:       Async DB session.
#         agent_id: UUID of the agent to analyse.

#     Returns:
#         AgentStats with all counters populated.

#     Raises:
#         ValueError: Agent not found (router converts to 404).
#     """
#     # ── 1. Agent lookup ───────────────────────────────────────────────
#     agent_row = (
#         await db.execute(select(Agent).where(Agent.id == agent_id))
#     ).scalar_one_or_none()

#     if agent_row is None:
#         raise ValueError(f"Agent '{agent_id}' not found.")

#     # ── 2. Run counts by status ────────────────────────────────────────
#     run_counts_rows = (
#         await db.execute(
#             select(AgentRun.status, func.count().label("n"))
#             .where(AgentRun.agent_id == agent_id)
#             .group_by(AgentRun.status)
#         )
#     ).all()

#     run_counts: dict[str, int] = {row.status: row.n for row in run_counts_rows}
#     total_runs     = sum(run_counts.values())
#     completed_runs = run_counts.get("completed", 0)
#     failed_runs    = run_counts.get("failed", 0)

#     # ── 3. Event counts by type ────────────────────────────────────────
#     evt_counts_rows = (
#         await db.execute(
#             select(AgentEvent.event_type, func.count().label("n"))
#             .where(AgentEvent.agent_id == agent_id)
#             .group_by(AgentEvent.event_type)
#         )
#     ).all()

#     evt_counts: dict[str, int] = {row.event_type: row.n for row in evt_counts_rows}
#     total_events     = sum(evt_counts.values())
#     total_tool_calls = evt_counts.get("tool_call", 0)
#     total_violations = evt_counts.get("violation", 0)
#     violation_rate   = (
#         round(total_violations / total_tool_calls * 100, 1)
#         if total_tool_calls > 0 else 0.0
#     )

#     # ── 4. Average run latency (completed runs only) ───────────────────
#     # julianday() converts a datetime to a fractional day number.
#     # Subtracting start from end gives days; multiply by 86_400_000 for ms.
#     avg_lat_row = (
#         await db.execute(
#             select(
#                 func.avg(
#                     (
#                         func.julianday(AgentRun.ended_at)
#                         - func.julianday(AgentRun.started_at)
#                     ) * 86_400_000
#                 ).label("avg_ms")
#             )
#             .where(
#                 and_(
#                     AgentRun.agent_id == agent_id,
#                     AgentRun.status   == "completed",
#                     AgentRun.ended_at.is_not(None),
#                 )
#             )
#         )
#     ).one()

#     avg_run_latency_ms = (
#         round(avg_lat_row.avg_ms, 2) if avg_lat_row.avg_ms is not None else None
#     )

#     # ── 5. Distinct tools successfully used ───────────────────────────
#     tools_rows = (
#         await db.execute(
#             select(AgentEvent.tool_name)
#             .where(
#                 and_(
#                     AgentEvent.agent_id   == agent_id,
#                     AgentEvent.event_type == "tool_end",
#                     AgentEvent.permitted  == True,  # noqa: E712
#                     AgentEvent.tool_name.is_not(None),
#                 )
#             )
#             .distinct()
#         )
#     ).all()

#     tools_used = sorted([row[0] for row in tools_rows])

#     return AgentStats(
#         agent_id=agent_id,
#         agent_name=agent_row.name,
#         total_runs=total_runs,
#         completed_runs=completed_runs,
#         failed_runs=failed_runs,
#         total_events=total_events,
#         total_tool_calls=total_tool_calls,
#         total_violations=total_violations,
#         violation_rate=violation_rate,
#         avg_run_latency_ms=avg_run_latency_ms,
#         tools_used=tools_used,
#     )


# async def get_system_stats(db: AsyncSession) -> SystemStats:
#     """
#     Compute platform-wide aggregate counters for the dashboard header.

#     Executes three queries:
#       1. Scalar counts: agents, runs (with status breakdown), events, tool_calls, violations.
#       2. Tool latency stats (delegates to get_tool_latency_stats).

#     Returns:
#         SystemStats with all counters and tool latency data.
#     """
#     # ── Counts ────────────────────────────────────────────────────────
#     total_agents: int = (
#         await db.execute(select(func.count()).select_from(Agent))
#     ).scalar_one()

#     # Run counts in one GROUP BY query
#     run_count_rows = (
#         await db.execute(
#             select(AgentRun.status, func.count().label("n"))
#             .group_by(AgentRun.status)
#         )
#     ).all()
#     run_counts     = {row.status: row.n for row in run_count_rows}
#     total_runs     = sum(run_counts.values())
#     completed_runs = run_counts.get("completed", 0)
#     failed_runs    = run_counts.get("failed", 0)

#     # Event counts in one GROUP BY query
#     evt_count_rows = (
#         await db.execute(
#             select(AgentEvent.event_type, func.count().label("n"))
#             .group_by(AgentEvent.event_type)
#         )
#     ).all()
#     evt_counts       = {row.event_type: row.n for row in evt_count_rows}
#     total_events     = sum(evt_counts.values())
#     total_tool_calls = evt_counts.get("tool_call", 0)
#     total_violations = evt_counts.get("violation", 0)
#     violation_rate   = (
#         round(total_violations / total_tool_calls * 100, 1)
#         if total_tool_calls > 0 else 0.0
#     )

#     # ── Tool latency ──────────────────────────────────────────────────
#     tool_latency = await get_tool_latency_stats(db)

#     # ── Interaction counts (Phase 2) ─────────────────────────────────
#     # Imported inside the function to avoid circular imports between
#     # audit_service and interaction_service.
#     from app.services.interaction_service import get_interaction_counts
#     interaction_counts = await get_interaction_counts(db)

#     # ── Policy counts (Phase 3) ───────────────────────────────────
#     from app.services.policy_service import get_policy_counts
#     policy_counts = await get_policy_counts(db)

#     return SystemStats(
#         total_agents=total_agents,
#         total_runs=total_runs,
#         total_events=total_events,
#         total_tool_calls=total_tool_calls,
#         total_violations=total_violations,
#         violation_rate=violation_rate,
#         completed_runs=completed_runs,
#         failed_runs=failed_runs,
#         tool_latency=tool_latency,
#         total_interactions=interaction_counts["total_interactions"],
#         interactions_by_type=interaction_counts["interactions_by_type"],
#         interactions_by_agent=interaction_counts["interactions_by_agent"],
#         total_policies=policy_counts["total_policies"],
#         active_policies=policy_counts["active_policies"],
#         total_policy_violations=policy_counts["total_policy_violations"],
#         violations_by_severity=policy_counts["violations_by_severity"],
#     )



















"""
app/services/audit_service.py
──────────────────────────────
All read-only database queries for the audit, governance, and analytics
endpoints.  No business logic lives in the routers — every SQL statement
is here so it can be tested independently.

Query design principles:
  1. Single async query per operation where possible.
     COUNT + paginated SELECT are always two separate queries because
     SQLAlchemy's async API does not support combined count+fetch natively.
     We accept the two round-trips; SQLite latency is sub-millisecond.

  2. All joins use explicit ON clauses (not implicit foreign-key magic)
     for clarity when reading the code without an ORM reference open.

  3. Pagination is OFFSET/LIMIT.  For very large tables a keyset approach
     (WHERE id > last_seen_id) would be more efficient, but for an MVP
     with SQLite and expected row counts in the thousands, OFFSET is fine.

  4. P95 latency approximation for SQLite:
     SQLite has no PERCENTILE_CONT aggregate.  We approximate by:
       a. Fetching all latency_ms values for the tool in sorted order.
       b. Taking the row at index floor(N * 0.95).
     This is O(N) in the number of tool_end rows per tool.  With expected
     call counts in the hundreds this is fast enough.  If you migrate to
     PostgreSQL, replace with:
       SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)

  5. All timestamps from SQLite are timezone-naive.  The service returns
     them as-is; the Pydantic schema serialises to ISO-8601 strings in
     UTC (by convention — the storage layer is naive).
"""

import logging
from datetime import datetime

from sqlalchemy import select, func, and_, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentRun, AgentEvent
# Interaction counts are fetched lazily to avoid circular imports.
# interaction_service is imported inside get_system_stats() below.
from app.schemas.audit import (
    AuditEventSchema,
    AuditLogResponse,
    ViolationSchema,
    ViolationListResponse,
    RunSummary,
    RunListResponse,
    ToolLatencyStat,
    AgentStats,
    SystemStats,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clamp_limit(limit: int, maximum: int = 200) -> int:
    """Prevent accidental huge payloads by enforcing a per-endpoint ceiling."""
    return min(max(1, limit), maximum)


async def _agent_name_map(db: AsyncSession, agent_ids: set[str]) -> dict[str, str]:
    """
    Return {agent_id: agent_name} for a set of agent UUIDs.

    Used to enrich event/run responses without N+1 queries: we collect
    all unique agent_ids from the result set first, then load names in
    a single IN query.

    Args:
        db:        Async DB session.
        agent_ids: Set of agent UUID strings.

    Returns:
        Dict mapping agent_id → name.  Missing agents map to None.
    """
    if not agent_ids:
        return {}

    result = await db.execute(
        select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids))
    )
    return {row.id: row.name for row in result.all()}


# ─────────────────────────────────────────────────────────────────────────────
# Audit log queries
# ─────────────────────────────────────────────────────────────────────────────

async def get_audit_logs(
    db: AsyncSession,
    *,
    agent_id: str | None = None,
    event_type: str | None = None,
    run_id: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> AuditLogResponse:
    """
    Return a paginated, filtered list of AgentEvent rows.

    All filter parameters are optional and additive (AND logic).
    Results are ordered by timestamp DESC (newest first) so the
    caller always sees the most recent activity at the top.

    Args:
        db:         Async DB session.
        agent_id:   Filter to events for a specific agent UUID.
        event_type: Filter to a specific event type string
                    (run_start | tool_call | tool_end | violation | run_end).
        run_id:     Filter to events for a specific run UUID.
        skip:       Pagination offset.
        limit:      Page size (clamped to 200).

    Returns:
        AuditLogResponse with events list and total count.
    """
    limit = _clamp_limit(limit)

    # Build the shared WHERE clause once — reused for COUNT and SELECT.
    filters = []
    if agent_id:
        filters.append(AgentEvent.agent_id == agent_id)
    if event_type:
        filters.append(AgentEvent.event_type == event_type)
    if run_id:
        filters.append(AgentEvent.run_id == run_id)

    where = and_(*filters) if filters else True

    # ── Total count ───────────────────────────────────────────────────
    count_q = select(func.count()).select_from(AgentEvent).where(where)
    total: int = (await db.execute(count_q)).scalar_one()

    # ── Paginated rows ────────────────────────────────────────────────
    rows_q = (
        select(AgentEvent)
        .where(where)
        .order_by(desc(AgentEvent.timestamp))
        .offset(skip)
        .limit(limit)
    )
    rows = (await db.execute(rows_q)).scalars().all()

    # ── Enrich with agent names (single IN query) ──────────────────────
    agent_ids = {e.agent_id for e in rows}
    name_map  = await _agent_name_map(db, agent_ids)

    events = [
        AuditEventSchema(
            id=e.id,
            run_id=e.run_id,
            agent_id=e.agent_id,
            trace_id=e.trace_id,
            event_type=e.event_type,
            tool_name=e.tool_name,
            input_data=e.input_data,
            output_data=e.output_data,
            permitted=e.permitted,
            latency_ms=e.latency_ms,
            timestamp=e.timestamp,
            agent_name=name_map.get(e.agent_id),
        )
        for e in rows
    ]

    return AuditLogResponse(events=events, total=total, skip=skip, limit=limit)


async def get_audit_logs_for_agent(
    db: AsyncSession,
    agent_id: str,
    *,
    skip: int = 0,
    limit: int = 50,
) -> AuditLogResponse:
    """
    Convenience wrapper — audit log filtered to a single agent.

    Equivalent to get_audit_logs(db, agent_id=agent_id, ...) but
    provides a cleaner call site for the GET /audit/logs/{agent_id} router.
    """
    return await get_audit_logs(
        db, agent_id=agent_id, skip=skip, limit=limit
    )


# ─────────────────────────────────────────────────────────────────────────────
# Governance / violations queries
# ─────────────────────────────────────────────────────────────────────────────

async def get_violations(
    db: AsyncSession,
    *,
    agent_id: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> ViolationListResponse:
    """
    Return a paginated list of governance violation events.

    A violation event is an AgentEvent where:
      event_type = 'violation'  AND  permitted = False

    The denial_message is extracted from event.output_data["denial_message"]
    and surfaced as a top-level field so callers don't need to parse JSON.

    Args:
        db:       Async DB session.
        agent_id: Optional filter to one agent's violations.
        skip:     Pagination offset.
        limit:    Page size (clamped to 200).

    Returns:
        ViolationListResponse with violations list and total count.
    """
    limit = _clamp_limit(limit)

    filters = [
        AgentEvent.event_type == "violation",
        AgentEvent.permitted  == False,  # noqa: E712 — SQLAlchemy needs == False
    ]
    if agent_id:
        filters.append(AgentEvent.agent_id == agent_id)

    where = and_(*filters)

    # ── Total count ───────────────────────────────────────────────────
    total: int = (
        await db.execute(select(func.count()).select_from(AgentEvent).where(where))
    ).scalar_one()

    # ── Paginated rows ─────────────────────────────────────────────────
    rows = (
        await db.execute(
            select(AgentEvent)
            .where(where)
            .order_by(desc(AgentEvent.timestamp))
            .offset(skip)
            .limit(limit)
        )
    ).scalars().all()

    # ── Enrich with agent names ────────────────────────────────────────
    agent_ids = {e.agent_id for e in rows}
    name_map  = await _agent_name_map(db, agent_ids)

    violations = [
        ViolationSchema(
            id=e.id,
            run_id=e.run_id,
            agent_id=e.agent_id,
            agent_name=name_map.get(e.agent_id),
            trace_id=e.trace_id,
            tool_name=e.tool_name,
            # input_data IS the attempted input captured by the governance proxy
            attempted_input=e.input_data,
            # denial_message is nested in output_data JSON
            denial_message=(e.output_data or {}).get("denial_message"),
            timestamp=e.timestamp,
        )
        for e in rows
    ]

    return ViolationListResponse(
        violations=violations, total=total, skip=skip, limit=limit
    )


# ─────────────────────────────────────────────────────────────────────────────
# Run history queries
# ─────────────────────────────────────────────────────────────────────────────

async def get_runs(
    db: AsyncSession,
    *,
    agent_id: str | None = None,
    status: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> RunListResponse:
    """
    Return a paginated list of AgentRun rows with summary fields.

    Each RunSummary includes a violation_count computed from the
    agent_events table (one sub-count query per run would be N+1;
    instead we aggregate in Python after loading both result sets).

    Args:
        db:       Async DB session.
        agent_id: Filter to runs for a specific agent.
        status:   Filter by status ('completed', 'failed', 'running').
        skip:     Pagination offset.
        limit:    Page size (clamped to 200).

    Returns:
        RunListResponse with runs list and total count.
    """
    limit = _clamp_limit(limit)

    filters = []
    if agent_id:
        filters.append(AgentRun.agent_id == agent_id)
    if status:
        filters.append(AgentRun.status == status)

    where = and_(*filters) if filters else True

    # ── Total count ────────────────────────────────────────────────────
    total: int = (
        await db.execute(select(func.count()).select_from(AgentRun).where(where))
    ).scalar_one()

    # ── Paginated runs ─────────────────────────────────────────────────
    run_rows = (
        await db.execute(
            select(AgentRun)
            .where(where)
            .order_by(desc(AgentRun.started_at))
            .offset(skip)
            .limit(limit)
        )
    ).scalars().all()

    if not run_rows:
        return RunListResponse(runs=[], total=total, skip=skip, limit=limit)

    # ── Violation counts per run (single query, not N+1) ────────────────
    # GROUP BY run_id to count violation events for each run in one shot.
    run_ids = [r.id for r in run_rows]
    viol_q = await db.execute(
        select(
            AgentEvent.run_id,
            func.count().label("vcount"),
        )
        .where(
            and_(
                AgentEvent.run_id.in_(run_ids),
                AgentEvent.event_type == "violation",
            )
        )
        .group_by(AgentEvent.run_id)
    )
    violation_counts: dict[str, int] = {row.run_id: row.vcount for row in viol_q.all()}

    # ── Agent names ───────────────────────────────────────────────────
    agent_ids = {r.agent_id for r in run_rows}
    name_map  = await _agent_name_map(db, agent_ids)

    # ── Build summaries ───────────────────────────────────────────────
    summaries = []
    for r in run_rows:
        # Compute latency_ms from stored timestamps (same logic as execution_service)
        latency_ms: float | None = None
        if r.ended_at and r.started_at:
            latency_ms = round(
                (r.ended_at - r.started_at).total_seconds() * 1000, 2
            )

        summaries.append(
            RunSummary(
                id=r.id,
                agent_id=r.agent_id,
                agent_name=name_map.get(r.agent_id),
                prompt=r.prompt,
                status=r.status,
                result=r.result,
                trace_id=r.trace_id,
                started_at=r.started_at,
                ended_at=r.ended_at,
                latency_ms=latency_ms,
                violation_count=violation_counts.get(r.id, 0),
            )
        )

    return RunListResponse(runs=summaries, total=total, skip=skip, limit=limit)


# ─────────────────────────────────────────────────────────────────────────────
# Analytics queries
# ─────────────────────────────────────────────────────────────────────────────

async def get_tool_latency_stats(db: AsyncSession) -> list[ToolLatencyStat]:
    """
    Return avg and approximate P95 latency for every tool.

    Two-step process:
      1. Fetch avg + count per tool in a single GROUP BY query.
      2. For each tool, fetch sorted latency_ms values to compute P95.

    The P95 step is O(N) per tool.  With expected call counts in the
    hundreds, total time is well under 10 ms on SQLite.

    PostgreSQL equivalent for step 2 (much more efficient at scale):
        SELECT tool_name,
               AVG(latency_ms),
               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)
        FROM agent_events
        WHERE event_type = 'tool_end' AND latency_ms IS NOT NULL
        GROUP BY tool_name

    Returns:
        List of ToolLatencyStat, one per distinct tool name, sorted
        by call_count descending (most-used tools first).
    """
    # ── Step 1: avg + count per tool ──────────────────────────────────
    agg_rows = (
        await db.execute(
            select(
                AgentEvent.tool_name,
                func.avg(AgentEvent.latency_ms).label("avg_ms"),
                func.count().label("call_count"),
            )
            .where(
                and_(
                    AgentEvent.event_type == "tool_end",
                    AgentEvent.latency_ms.is_not(None),
                )
            )
            .group_by(AgentEvent.tool_name)
            .order_by(desc("call_count"))
        )
    ).all()

    if not agg_rows:
        return []

    stats: list[ToolLatencyStat] = []

    # ── Step 2: P95 per tool ──────────────────────────────────────────
    for row in agg_rows:
        tool_name  = row.tool_name
        avg_ms     = round(row.avg_ms, 2) if row.avg_ms is not None else None
        call_count = row.call_count

        # Fetch sorted latency values for this tool to approximate P95.
        lat_rows = (
            await db.execute(
                select(AgentEvent.latency_ms)
                .where(
                    and_(
                        AgentEvent.event_type == "tool_end",
                        AgentEvent.tool_name  == tool_name,
                        AgentEvent.latency_ms.is_not(None),
                    )
                )
                .order_by(asc(AgentEvent.latency_ms))
            )
        ).all()

        latencies = [r[0] for r in lat_rows]
        p95_ms: float | None = None
        if latencies:
            # Index of the 95th-percentile element (0-based, clamped to last).
            # floor(N * 0.95) gives us the element just below the 95% mark
            # which is the standard "nearest rank" method.
            idx   = max(0, int(len(latencies) * 0.95) - 1)
            p95_ms = round(latencies[idx], 2)

        stats.append(
            ToolLatencyStat(
                tool_name=tool_name,
                avg_ms=avg_ms,
                p95_ms=p95_ms,
                call_count=call_count,
            )
        )

    return stats


async def get_agent_stats(db: AsyncSession, agent_id: str) -> AgentStats:
    """
    Compute aggregate statistics for a single agent.

    Executes five queries:
      1. Agent name lookup (+ existence check — raises if not found).
      2. Run counts grouped by status.
      3. Event counts grouped by event_type.
      4. Average run latency across completed runs.
      5. Distinct tool names from permitted tool_end events.

    Args:
        db:       Async DB session.
        agent_id: UUID of the agent to analyse.

    Returns:
        AgentStats with all counters populated.

    Raises:
        ValueError: Agent not found (router converts to 404).
    """
    # ── 1. Agent lookup ───────────────────────────────────────────────
    agent_row = (
        await db.execute(select(Agent).where(Agent.id == agent_id))
    ).scalar_one_or_none()

    if agent_row is None:
        raise ValueError(f"Agent '{agent_id}' not found.")

    # ── 2. Run counts by status ────────────────────────────────────────
    run_counts_rows = (
        await db.execute(
            select(AgentRun.status, func.count().label("n"))
            .where(AgentRun.agent_id == agent_id)
            .group_by(AgentRun.status)
        )
    ).all()

    run_counts: dict[str, int] = {row.status: row.n for row in run_counts_rows}
    total_runs     = sum(run_counts.values())
    completed_runs = run_counts.get("completed", 0)
    failed_runs    = run_counts.get("failed", 0)

    # ── 3. Event counts by type ────────────────────────────────────────
    evt_counts_rows = (
        await db.execute(
            select(AgentEvent.event_type, func.count().label("n"))
            .where(AgentEvent.agent_id == agent_id)
            .group_by(AgentEvent.event_type)
        )
    ).all()

    evt_counts: dict[str, int] = {row.event_type: row.n for row in evt_counts_rows}
    total_events     = sum(evt_counts.values())
    total_tool_calls = evt_counts.get("tool_call", 0)
    total_violations = evt_counts.get("violation", 0)
    violation_rate   = (
        round(total_violations / total_tool_calls * 100, 1)
        if total_tool_calls > 0 else 0.0
    )

    # ── 4. Average run latency (completed runs only) ───────────────────
    # julianday() converts a datetime to a fractional day number.
    # Subtracting start from end gives days; multiply by 86_400_000 for ms.
    avg_lat_row = (
        await db.execute(
            select(
                func.avg(
                    (
                        func.julianday(AgentRun.ended_at)
                        - func.julianday(AgentRun.started_at)
                    ) * 86_400_000
                ).label("avg_ms")
            )
            .where(
                and_(
                    AgentRun.agent_id == agent_id,
                    AgentRun.status   == "completed",
                    AgentRun.ended_at.is_not(None),
                )
            )
        )
    ).one()

    avg_run_latency_ms = (
        round(avg_lat_row.avg_ms, 2) if avg_lat_row.avg_ms is not None else None
    )

    # ── 5. Distinct tools successfully used ───────────────────────────
    tools_rows = (
        await db.execute(
            select(AgentEvent.tool_name)
            .where(
                and_(
                    AgentEvent.agent_id   == agent_id,
                    AgentEvent.event_type == "tool_end",
                    AgentEvent.permitted  == True,  # noqa: E712
                    AgentEvent.tool_name.is_not(None),
                )
            )
            .distinct()
        )
    ).all()

    tools_used = sorted([row[0] for row in tools_rows])

    # ── 6. Trust score (Phase 4) ─────────────────────────────────────
    from app.services.trust_service import calculate_agent_trust_score
    trust_calc = await calculate_agent_trust_score(db, agent_id)

    # ── 7. Risk score (Phase 5) ──────────────────────────────────────
    from app.services.risk_service import calculate_agent_risk_score
    risk_calc = await calculate_agent_risk_score(db, agent_id)

    return AgentStats(
        agent_id=agent_id,
        agent_name=agent_row.name,
        total_runs=total_runs,
        completed_runs=completed_runs,
        failed_runs=failed_runs,
        total_events=total_events,
        total_tool_calls=total_tool_calls,
        total_violations=total_violations,
        violation_rate=violation_rate,
        avg_run_latency_ms=avg_run_latency_ms,
        tools_used=tools_used,
        trust_score=trust_calc.final_score,
        trust_level=trust_calc.trust_level,
        risk_score=risk_calc.final_score,
        risk_level=risk_calc.risk_level,
    )


async def get_system_stats(db: AsyncSession) -> SystemStats:
    """
    Compute platform-wide aggregate counters for the dashboard header.

    Executes three queries:
      1. Scalar counts: agents, runs (with status breakdown), events, tool_calls, violations.
      2. Tool latency stats (delegates to get_tool_latency_stats).

    Returns:
        SystemStats with all counters and tool latency data.
    """
    # ── Counts ────────────────────────────────────────────────────────
    total_agents: int = (
        await db.execute(select(func.count()).select_from(Agent))
    ).scalar_one()

    # Run counts in one GROUP BY query
    run_count_rows = (
        await db.execute(
            select(AgentRun.status, func.count().label("n"))
            .group_by(AgentRun.status)
        )
    ).all()
    run_counts     = {row.status: row.n for row in run_count_rows}
    total_runs     = sum(run_counts.values())
    completed_runs = run_counts.get("completed", 0)
    failed_runs    = run_counts.get("failed", 0)

    # Event counts in one GROUP BY query
    evt_count_rows = (
        await db.execute(
            select(AgentEvent.event_type, func.count().label("n"))
            .group_by(AgentEvent.event_type)
        )
    ).all()
    evt_counts       = {row.event_type: row.n for row in evt_count_rows}
    total_events     = sum(evt_counts.values())
    total_tool_calls = evt_counts.get("tool_call", 0)
    total_violations = evt_counts.get("violation", 0)
    violation_rate   = (
        round(total_violations / total_tool_calls * 100, 1)
        if total_tool_calls > 0 else 0.0
    )

    # ── Tool latency ──────────────────────────────────────────────────
    tool_latency = await get_tool_latency_stats(db)

    # ── Interaction counts (Phase 2) ─────────────────────────────────
    # Imported inside the function to avoid circular imports between
    # audit_service and interaction_service.
    from app.services.interaction_service import get_interaction_counts
    interaction_counts = await get_interaction_counts(db)

    # ── Policy counts (Phase 3) ───────────────────────────────────
    from app.services.policy_service import get_policy_counts
    policy_counts = await get_policy_counts(db)

    # ── Trust score aggregates (Phase 4) ────────────────────────────
    from app.services.trust_service import calculate_system_trust_score
    trust_data = await calculate_system_trust_score(db)

    # ── Risk score aggregates (Phase 5) ────────────────────────────
    from app.services.risk_service import calculate_system_risk_score
    risk_data = await calculate_system_risk_score(db)

    return SystemStats(
        total_agents=total_agents,
        total_runs=total_runs,
        total_events=total_events,
        total_tool_calls=total_tool_calls,
        total_violations=total_violations,
        violation_rate=violation_rate,
        completed_runs=completed_runs,
        failed_runs=failed_runs,
        tool_latency=tool_latency,
        total_interactions=interaction_counts["total_interactions"],
        interactions_by_type=interaction_counts["interactions_by_type"],
        interactions_by_agent=interaction_counts["interactions_by_agent"],
        total_policies=policy_counts["total_policies"],
        active_policies=policy_counts["active_policies"],
        total_policy_violations=policy_counts["total_policy_violations"],
        violations_by_severity=policy_counts["violations_by_severity"],
        average_trust_score=trust_data["average_trust_score"],
        trust_distribution=trust_data["trust_distribution"],
        average_risk_score=risk_data["average_risk_score"],
        risk_distribution=risk_data["risk_distribution"],
    )















# """
# app/services/audit_service.py
# ──────────────────────────────
# All read-only database queries for the audit, governance, and analytics
# endpoints.  No business logic lives in the routers — every SQL statement
# is here so it can be tested independently.

# Query design principles:
#   1. Single async query per operation where possible.
#      COUNT + paginated SELECT are always two separate queries because
#      SQLAlchemy's async API does not support combined count+fetch natively.
#      We accept the two round-trips; SQLite latency is sub-millisecond.

#   2. All joins use explicit ON clauses (not implicit foreign-key magic)
#      for clarity when reading the code without an ORM reference open.

#   3. Pagination is OFFSET/LIMIT.  For very large tables a keyset approach
#      (WHERE id > last_seen_id) would be more efficient, but for an MVP
#      with SQLite and expected row counts in the thousands, OFFSET is fine.

#   4. P95 latency approximation for SQLite:
#      SQLite has no PERCENTILE_CONT aggregate.  We approximate by:
#        a. Fetching all latency_ms values for the tool in sorted order.
#        b. Taking the row at index floor(N * 0.95).
#      This is O(N) in the number of tool_end rows per tool.  With expected
#      call counts in the hundreds this is fast enough.  If you migrate to
#      PostgreSQL, replace with:
#        SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)

#   5. All timestamps from SQLite are timezone-naive.  The service returns
#      them as-is; the Pydantic schema serialises to ISO-8601 strings in
#      UTC (by convention — the storage layer is naive).
# """

# import logging
# from datetime import datetime

# from sqlalchemy import select, func, and_, desc, asc
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.models.agent import Agent, AgentRun, AgentEvent
# # Interaction counts are fetched lazily to avoid circular imports.
# # interaction_service is imported inside get_system_stats() below.
# from app.schemas.audit import (
#     AuditEventSchema,
#     AuditLogResponse,
#     ViolationSchema,
#     ViolationListResponse,
#     RunSummary,
#     RunListResponse,
#     ToolLatencyStat,
#     AgentStats,
#     SystemStats,
# )

# logger = logging.getLogger(__name__)


# # ─────────────────────────────────────────────────────────────────────────────
# # Internal helpers
# # ─────────────────────────────────────────────────────────────────────────────

# def _clamp_limit(limit: int, maximum: int = 200) -> int:
#     """Prevent accidental huge payloads by enforcing a per-endpoint ceiling."""
#     return min(max(1, limit), maximum)


# async def _agent_name_map(db: AsyncSession, agent_ids: set[str]) -> dict[str, str]:
#     """
#     Return {agent_id: agent_name} for a set of agent UUIDs.

#     Used to enrich event/run responses without N+1 queries: we collect
#     all unique agent_ids from the result set first, then load names in
#     a single IN query.

#     Args:
#         db:        Async DB session.
#         agent_ids: Set of agent UUID strings.

#     Returns:
#         Dict mapping agent_id → name.  Missing agents map to None.
#     """
#     if not agent_ids:
#         return {}

#     result = await db.execute(
#         select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids))
#     )
#     return {row.id: row.name for row in result.all()}


# # ─────────────────────────────────────────────────────────────────────────────
# # Audit log queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_audit_logs(
#     db: AsyncSession,
#     *,
#     agent_id: str | None = None,
#     event_type: str | None = None,
#     run_id: str | None = None,
#     skip: int = 0,
#     limit: int = 50,
# ) -> AuditLogResponse:
#     """
#     Return a paginated, filtered list of AgentEvent rows.

#     All filter parameters are optional and additive (AND logic).
#     Results are ordered by timestamp DESC (newest first) so the
#     caller always sees the most recent activity at the top.

#     Args:
#         db:         Async DB session.
#         agent_id:   Filter to events for a specific agent UUID.
#         event_type: Filter to a specific event type string
#                     (run_start | tool_call | tool_end | violation | run_end).
#         run_id:     Filter to events for a specific run UUID.
#         skip:       Pagination offset.
#         limit:      Page size (clamped to 200).

#     Returns:
#         AuditLogResponse with events list and total count.
#     """
#     limit = _clamp_limit(limit)

#     # Build the shared WHERE clause once — reused for COUNT and SELECT.
#     filters = []
#     if agent_id:
#         filters.append(AgentEvent.agent_id == agent_id)
#     if event_type:
#         filters.append(AgentEvent.event_type == event_type)
#     if run_id:
#         filters.append(AgentEvent.run_id == run_id)

#     where = and_(*filters) if filters else True

#     # ── Total count ───────────────────────────────────────────────────
#     count_q = select(func.count()).select_from(AgentEvent).where(where)
#     total: int = (await db.execute(count_q)).scalar_one()

#     # ── Paginated rows ────────────────────────────────────────────────
#     rows_q = (
#         select(AgentEvent)
#         .where(where)
#         .order_by(desc(AgentEvent.timestamp))
#         .offset(skip)
#         .limit(limit)
#     )
#     rows = (await db.execute(rows_q)).scalars().all()

#     # ── Enrich with agent names (single IN query) ──────────────────────
#     agent_ids = {e.agent_id for e in rows}
#     name_map  = await _agent_name_map(db, agent_ids)

#     events = [
#         AuditEventSchema(
#             id=e.id,
#             run_id=e.run_id,
#             agent_id=e.agent_id,
#             trace_id=e.trace_id,
#             event_type=e.event_type,
#             tool_name=e.tool_name,
#             input_data=e.input_data,
#             output_data=e.output_data,
#             permitted=e.permitted,
#             latency_ms=e.latency_ms,
#             timestamp=e.timestamp,
#             agent_name=name_map.get(e.agent_id),
#         )
#         for e in rows
#     ]

#     return AuditLogResponse(events=events, total=total, skip=skip, limit=limit)


# async def get_audit_logs_for_agent(
#     db: AsyncSession,
#     agent_id: str,
#     *,
#     skip: int = 0,
#     limit: int = 50,
# ) -> AuditLogResponse:
#     """
#     Convenience wrapper — audit log filtered to a single agent.

#     Equivalent to get_audit_logs(db, agent_id=agent_id, ...) but
#     provides a cleaner call site for the GET /audit/logs/{agent_id} router.
#     """
#     return await get_audit_logs(
#         db, agent_id=agent_id, skip=skip, limit=limit
#     )


# # ─────────────────────────────────────────────────────────────────────────────
# # Governance / violations queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_violations(
#     db: AsyncSession,
#     *,
#     agent_id: str | None = None,
#     skip: int = 0,
#     limit: int = 50,
# ) -> ViolationListResponse:
#     """
#     Return a paginated list of governance violation events.

#     A violation event is an AgentEvent where:
#       event_type = 'violation'  AND  permitted = False

#     The denial_message is extracted from event.output_data["denial_message"]
#     and surfaced as a top-level field so callers don't need to parse JSON.

#     Args:
#         db:       Async DB session.
#         agent_id: Optional filter to one agent's violations.
#         skip:     Pagination offset.
#         limit:    Page size (clamped to 200).

#     Returns:
#         ViolationListResponse with violations list and total count.
#     """
#     limit = _clamp_limit(limit)

#     filters = [
#         AgentEvent.event_type == "violation",
#         AgentEvent.permitted  == False,  # noqa: E712 — SQLAlchemy needs == False
#     ]
#     if agent_id:
#         filters.append(AgentEvent.agent_id == agent_id)

#     where = and_(*filters)

#     # ── Total count ───────────────────────────────────────────────────
#     total: int = (
#         await db.execute(select(func.count()).select_from(AgentEvent).where(where))
#     ).scalar_one()

#     # ── Paginated rows ─────────────────────────────────────────────────
#     rows = (
#         await db.execute(
#             select(AgentEvent)
#             .where(where)
#             .order_by(desc(AgentEvent.timestamp))
#             .offset(skip)
#             .limit(limit)
#         )
#     ).scalars().all()

#     # ── Enrich with agent names ────────────────────────────────────────
#     agent_ids = {e.agent_id for e in rows}
#     name_map  = await _agent_name_map(db, agent_ids)

#     violations = [
#         ViolationSchema(
#             id=e.id,
#             run_id=e.run_id,
#             agent_id=e.agent_id,
#             agent_name=name_map.get(e.agent_id),
#             trace_id=e.trace_id,
#             tool_name=e.tool_name,
#             # input_data IS the attempted input captured by the governance proxy
#             attempted_input=e.input_data,
#             # denial_message is nested in output_data JSON
#             denial_message=(e.output_data or {}).get("denial_message"),
#             timestamp=e.timestamp,
#         )
#         for e in rows
#     ]

#     return ViolationListResponse(
#         violations=violations, total=total, skip=skip, limit=limit
#     )


# # ─────────────────────────────────────────────────────────────────────────────
# # Run history queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_runs(
#     db: AsyncSession,
#     *,
#     agent_id: str | None = None,
#     status: str | None = None,
#     skip: int = 0,
#     limit: int = 50,
# ) -> RunListResponse:
#     """
#     Return a paginated list of AgentRun rows with summary fields.

#     Each RunSummary includes a violation_count computed from the
#     agent_events table (one sub-count query per run would be N+1;
#     instead we aggregate in Python after loading both result sets).

#     Args:
#         db:       Async DB session.
#         agent_id: Filter to runs for a specific agent.
#         status:   Filter by status ('completed', 'failed', 'running').
#         skip:     Pagination offset.
#         limit:    Page size (clamped to 200).

#     Returns:
#         RunListResponse with runs list and total count.
#     """
#     limit = _clamp_limit(limit)

#     filters = []
#     if agent_id:
#         filters.append(AgentRun.agent_id == agent_id)
#     if status:
#         filters.append(AgentRun.status == status)

#     where = and_(*filters) if filters else True

#     # ── Total count ────────────────────────────────────────────────────
#     total: int = (
#         await db.execute(select(func.count()).select_from(AgentRun).where(where))
#     ).scalar_one()

#     # ── Paginated runs ─────────────────────────────────────────────────
#     run_rows = (
#         await db.execute(
#             select(AgentRun)
#             .where(where)
#             .order_by(desc(AgentRun.started_at))
#             .offset(skip)
#             .limit(limit)
#         )
#     ).scalars().all()

#     if not run_rows:
#         return RunListResponse(runs=[], total=total, skip=skip, limit=limit)

#     # ── Violation counts per run (single query, not N+1) ────────────────
#     # GROUP BY run_id to count violation events for each run in one shot.
#     run_ids = [r.id for r in run_rows]
#     viol_q = await db.execute(
#         select(
#             AgentEvent.run_id,
#             func.count().label("vcount"),
#         )
#         .where(
#             and_(
#                 AgentEvent.run_id.in_(run_ids),
#                 AgentEvent.event_type == "violation",
#             )
#         )
#         .group_by(AgentEvent.run_id)
#     )
#     violation_counts: dict[str, int] = {row.run_id: row.vcount for row in viol_q.all()}

#     # ── Agent names ───────────────────────────────────────────────────
#     agent_ids = {r.agent_id for r in run_rows}
#     name_map  = await _agent_name_map(db, agent_ids)

#     # ── Build summaries ───────────────────────────────────────────────
#     summaries = []
#     for r in run_rows:
#         # Compute latency_ms from stored timestamps (same logic as execution_service)
#         latency_ms: float | None = None
#         if r.ended_at and r.started_at:
#             latency_ms = round(
#                 (r.ended_at - r.started_at).total_seconds() * 1000, 2
#             )

#         summaries.append(
#             RunSummary(
#                 id=r.id,
#                 agent_id=r.agent_id,
#                 agent_name=name_map.get(r.agent_id),
#                 prompt=r.prompt,
#                 status=r.status,
#                 result=r.result,
#                 trace_id=r.trace_id,
#                 started_at=r.started_at,
#                 ended_at=r.ended_at,
#                 latency_ms=latency_ms,
#                 violation_count=violation_counts.get(r.id, 0),
#             )
#         )

#     return RunListResponse(runs=summaries, total=total, skip=skip, limit=limit)


# # ─────────────────────────────────────────────────────────────────────────────
# # Analytics queries
# # ─────────────────────────────────────────────────────────────────────────────

# async def get_tool_latency_stats(db: AsyncSession) -> list[ToolLatencyStat]:
#     """
#     Return avg and approximate P95 latency for every tool.

#     Two-step process:
#       1. Fetch avg + count per tool in a single GROUP BY query.
#       2. For each tool, fetch sorted latency_ms values to compute P95.

#     The P95 step is O(N) per tool.  With expected call counts in the
#     hundreds, total time is well under 10 ms on SQLite.

#     PostgreSQL equivalent for step 2 (much more efficient at scale):
#         SELECT tool_name,
#                AVG(latency_ms),
#                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)
#         FROM agent_events
#         WHERE event_type = 'tool_end' AND latency_ms IS NOT NULL
#         GROUP BY tool_name

#     Returns:
#         List of ToolLatencyStat, one per distinct tool name, sorted
#         by call_count descending (most-used tools first).
#     """
#     # ── Step 1: avg + count per tool ──────────────────────────────────
#     agg_rows = (
#         await db.execute(
#             select(
#                 AgentEvent.tool_name,
#                 func.avg(AgentEvent.latency_ms).label("avg_ms"),
#                 func.count().label("call_count"),
#             )
#             .where(
#                 and_(
#                     AgentEvent.event_type == "tool_end",
#                     AgentEvent.latency_ms.is_not(None),
#                 )
#             )
#             .group_by(AgentEvent.tool_name)
#             .order_by(desc("call_count"))
#         )
#     ).all()

#     if not agg_rows:
#         return []

#     stats: list[ToolLatencyStat] = []

#     # ── Step 2: P95 per tool ──────────────────────────────────────────
#     for row in agg_rows:
#         tool_name  = row.tool_name
#         avg_ms     = round(row.avg_ms, 2) if row.avg_ms is not None else None
#         call_count = row.call_count

#         # Fetch sorted latency values for this tool to approximate P95.
#         lat_rows = (
#             await db.execute(
#                 select(AgentEvent.latency_ms)
#                 .where(
#                     and_(
#                         AgentEvent.event_type == "tool_end",
#                         AgentEvent.tool_name  == tool_name,
#                         AgentEvent.latency_ms.is_not(None),
#                     )
#                 )
#                 .order_by(asc(AgentEvent.latency_ms))
#             )
#         ).all()

#         latencies = [r[0] for r in lat_rows]
#         p95_ms: float | None = None
#         if latencies:
#             # Index of the 95th-percentile element (0-based, clamped to last).
#             # floor(N * 0.95) gives us the element just below the 95% mark
#             # which is the standard "nearest rank" method.
#             idx   = max(0, int(len(latencies) * 0.95) - 1)
#             p95_ms = round(latencies[idx], 2)

#         stats.append(
#             ToolLatencyStat(
#                 tool_name=tool_name,
#                 avg_ms=avg_ms,
#                 p95_ms=p95_ms,
#                 call_count=call_count,
#             )
#         )

#     return stats


# async def get_agent_stats(db: AsyncSession, agent_id: str) -> AgentStats:
#     """
#     Compute aggregate statistics for a single agent.

#     Executes five queries:
#       1. Agent name lookup (+ existence check — raises if not found).
#       2. Run counts grouped by status.
#       3. Event counts grouped by event_type.
#       4. Average run latency across completed runs.
#       5. Distinct tool names from permitted tool_end events.

#     Args:
#         db:       Async DB session.
#         agent_id: UUID of the agent to analyse.

#     Returns:
#         AgentStats with all counters populated.

#     Raises:
#         ValueError: Agent not found (router converts to 404).
#     """
#     # ── 1. Agent lookup ───────────────────────────────────────────────
#     agent_row = (
#         await db.execute(select(Agent).where(Agent.id == agent_id))
#     ).scalar_one_or_none()

#     if agent_row is None:
#         raise ValueError(f"Agent '{agent_id}' not found.")

#     # ── 2. Run counts by status ────────────────────────────────────────
#     run_counts_rows = (
#         await db.execute(
#             select(AgentRun.status, func.count().label("n"))
#             .where(AgentRun.agent_id == agent_id)
#             .group_by(AgentRun.status)
#         )
#     ).all()

#     run_counts: dict[str, int] = {row.status: row.n for row in run_counts_rows}
#     total_runs     = sum(run_counts.values())
#     completed_runs = run_counts.get("completed", 0)
#     failed_runs    = run_counts.get("failed", 0)

#     # ── 3. Event counts by type ────────────────────────────────────────
#     evt_counts_rows = (
#         await db.execute(
#             select(AgentEvent.event_type, func.count().label("n"))
#             .where(AgentEvent.agent_id == agent_id)
#             .group_by(AgentEvent.event_type)
#         )
#     ).all()

#     evt_counts: dict[str, int] = {row.event_type: row.n for row in evt_counts_rows}
#     total_events     = sum(evt_counts.values())
#     total_tool_calls = evt_counts.get("tool_call", 0)
#     total_violations = evt_counts.get("violation", 0)
#     violation_rate   = (
#         round(total_violations / total_tool_calls * 100, 1)
#         if total_tool_calls > 0 else 0.0
#     )

#     # ── 4. Average run latency (completed runs only) ───────────────────
#     # julianday() converts a datetime to a fractional day number.
#     # Subtracting start from end gives days; multiply by 86_400_000 for ms.
#     avg_lat_row = (
#         await db.execute(
#             select(
#                 func.avg(
#                     (
#                         func.julianday(AgentRun.ended_at)
#                         - func.julianday(AgentRun.started_at)
#                     ) * 86_400_000
#                 ).label("avg_ms")
#             )
#             .where(
#                 and_(
#                     AgentRun.agent_id == agent_id,
#                     AgentRun.status   == "completed",
#                     AgentRun.ended_at.is_not(None),
#                 )
#             )
#         )
#     ).one()

#     avg_run_latency_ms = (
#         round(avg_lat_row.avg_ms, 2) if avg_lat_row.avg_ms is not None else None
#     )

#     # ── 5. Distinct tools successfully used ───────────────────────────
#     tools_rows = (
#         await db.execute(
#             select(AgentEvent.tool_name)
#             .where(
#                 and_(
#                     AgentEvent.agent_id   == agent_id,
#                     AgentEvent.event_type == "tool_end",
#                     AgentEvent.permitted  == True,  # noqa: E712
#                     AgentEvent.tool_name.is_not(None),
#                 )
#             )
#             .distinct()
#         )
#     ).all()

#     tools_used = sorted([row[0] for row in tools_rows])

#     # ── 6. Trust score (Phase 4) ─────────────────────────────────────
#     from app.services.trust_service import calculate_agent_trust_score
#     trust_calc = await calculate_agent_trust_score(db, agent_id)

#     return AgentStats(
#         agent_id=agent_id,
#         agent_name=agent_row.name,
#         total_runs=total_runs,
#         completed_runs=completed_runs,
#         failed_runs=failed_runs,
#         total_events=total_events,
#         total_tool_calls=total_tool_calls,
#         total_violations=total_violations,
#         violation_rate=violation_rate,
#         avg_run_latency_ms=avg_run_latency_ms,
#         tools_used=tools_used,
#         trust_score=trust_calc.final_score,
#         trust_level=trust_calc.trust_level,
#     )


# async def get_system_stats(db: AsyncSession) -> SystemStats:
#     """
#     Compute platform-wide aggregate counters for the dashboard header.

#     Executes three queries:
#       1. Scalar counts: agents, runs (with status breakdown), events, tool_calls, violations.
#       2. Tool latency stats (delegates to get_tool_latency_stats).

#     Returns:
#         SystemStats with all counters and tool latency data.
#     """
#     # ── Counts ────────────────────────────────────────────────────────
#     total_agents: int = (
#         await db.execute(select(func.count()).select_from(Agent))
#     ).scalar_one()

#     # Run counts in one GROUP BY query
#     run_count_rows = (
#         await db.execute(
#             select(AgentRun.status, func.count().label("n"))
#             .group_by(AgentRun.status)
#         )
#     ).all()
#     run_counts     = {row.status: row.n for row in run_count_rows}
#     total_runs     = sum(run_counts.values())
#     completed_runs = run_counts.get("completed", 0)
#     failed_runs    = run_counts.get("failed", 0)

#     # Event counts in one GROUP BY query
#     evt_count_rows = (
#         await db.execute(
#             select(AgentEvent.event_type, func.count().label("n"))
#             .group_by(AgentEvent.event_type)
#         )
#     ).all()
#     evt_counts       = {row.event_type: row.n for row in evt_count_rows}
#     total_events     = sum(evt_counts.values())
#     total_tool_calls = evt_counts.get("tool_call", 0)
#     total_violations = evt_counts.get("violation", 0)
#     violation_rate   = (
#         round(total_violations / total_tool_calls * 100, 1)
#         if total_tool_calls > 0 else 0.0
#     )

#     # ── Tool latency ──────────────────────────────────────────────────
#     tool_latency = await get_tool_latency_stats(db)

#     # ── Interaction counts (Phase 2) ─────────────────────────────────
#     # Imported inside the function to avoid circular imports between
#     # audit_service and interaction_service.
#     from app.services.interaction_service import get_interaction_counts
#     interaction_counts = await get_interaction_counts(db)

#     # ── Policy counts (Phase 3) ───────────────────────────────────
#     from app.services.policy_service import get_policy_counts
#     policy_counts = await get_policy_counts(db)

#     # ── Trust score aggregates (Phase 4) ────────────────────────────
#     from app.services.trust_service import calculate_system_trust_score
#     trust_data = await calculate_system_trust_score(db)

#     return SystemStats(
#         total_agents=total_agents,
#         total_runs=total_runs,
#         total_events=total_events,
#         total_tool_calls=total_tool_calls,
#         total_violations=total_violations,
#         violation_rate=violation_rate,
#         completed_runs=completed_runs,
#         failed_runs=failed_runs,
#         tool_latency=tool_latency,
#         total_interactions=interaction_counts["total_interactions"],
#         interactions_by_type=interaction_counts["interactions_by_type"],
#         interactions_by_agent=interaction_counts["interactions_by_agent"],
#         total_policies=policy_counts["total_policies"],
#         active_policies=policy_counts["active_policies"],
#         total_policy_violations=policy_counts["total_policy_violations"],
#         violations_by_severity=policy_counts["violations_by_severity"],
#         average_trust_score=trust_data["average_trust_score"],
#         trust_distribution=trust_data["trust_distribution"],
#     )