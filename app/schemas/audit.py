# """
# app/schemas/audit.py
# ─────────────────────
# Pydantic v2 response schemas for audit, governance, and analytics endpoints.

# Schema hierarchy:
#   Audit layer
#     AuditEventSchema       — one AgentEvent row, enriched with agent name
#     AuditLogResponse       — paginated list of AuditEventSchema

#   Governance layer
#     ViolationSchema        — one violation event, denial message surfaced
#     ViolationListResponse  — paginated list of ViolationSchema

#   Run history layer
#     RunSummary             — lightweight AgentRun for list views
#     RunListResponse        — paginated list of RunSummary

#   Analytics layer
#     ToolLatencyStat        — avg + p95 latency for a single tool
#     AgentStats             — per-agent aggregate counters
#     SystemStats            — platform-wide counters for dashboard header

# Design notes:
#   • All response schemas use from_attributes=True so they can be built
#     directly from ORM instances via model_validate().
#   • Nullable fields use T | None rather than Optional[T] (Python 3.10+ style).
#   • Timestamps are datetime objects — FastAPI serialises them to ISO-8601.
#   • violation_rate is a float 0–100 representing the percentage of tool_call
#     events that were blocked.  Computed in audit_service, not here.
# """

# from __future__ import annotations
# from datetime import datetime
# from pydantic import BaseModel, Field, ConfigDict


# # ─────────────────────────────────────────────────────────────────────────────
# # Audit log schemas
# # ─────────────────────────────────────────────────────────────────────────────

# class AuditEventSchema(BaseModel):
#     """
#     A single AgentEvent row enriched with the owning agent's name.

#     Used in GET /audit/logs responses.  Includes every column from
#     AgentEvent plus agent_name (joined from the agents table) so
#     the caller does not need a separate lookup.
#     """

#     # AgentEvent primary fields
#     id:          str
#     run_id:      str
#     agent_id:    str
#     trace_id:    str
#     event_type:  str
#     tool_name:   str | None  = None
#     input_data:  dict | None = None
#     output_data: dict | None = None
#     permitted:   bool | None = None
#     latency_ms:  float | None = None
#     timestamp:   datetime

#     # Joined field — populated by the service layer
#     agent_name:  str | None = Field(
#         default=None,
#         description="Display name of the agent that produced this event.",
#     )

#     model_config = ConfigDict(
#         from_attributes=True,
#         json_schema_extra={
#             "example": {
#                 "id": "evt-uuid",
#                 "run_id": "run-uuid",
#                 "agent_id": "agent-uuid",
#                 "trace_id": "trace-uuid",
#                 "event_type": "tool_call",
#                 "tool_name": "calculator",
#                 "input_data": {"expression": "2+2"},
#                 "output_data": None,
#                 "permitted": True,
#                 "latency_ms": None,
#                 "timestamp": "2024-01-15T10:30:01Z",
#                 "agent_name": "finance-bot",
#             }
#         },
#     )


# class AuditLogResponse(BaseModel):
#     """Paginated audit event list."""

#     events: list[AuditEventSchema] = Field(
#         description="Ordered list of audit events (newest first)."
#     )
#     total: int = Field(
#         description="Total number of events matching the filter (before pagination)."
#     )
#     skip:  int = Field(description="Pagination offset applied to this response.")
#     limit: int = Field(description="Page size applied to this response.")


# # ─────────────────────────────────────────────────────────────────────────────
# # Governance / violation schemas
# # ─────────────────────────────────────────────────────────────────────────────

# class ViolationSchema(BaseModel):
#     """
#     A single governance violation event.

#     Surfaces the denial_message from output_data directly as a top-level
#     field so callers don't need to dig into the JSON blob.
#     """

#     id:             str
#     run_id:         str
#     agent_id:       str
#     agent_name:     str | None = None
#     trace_id:       str
#     tool_name:      str | None = None

#     # The input the LLM attempted to pass to the blocked tool
#     attempted_input: dict | None = Field(
#         default=None,
#         description="The arguments the agent tried to pass to the blocked tool.",
#     )

#     # Extracted from output_data.denial_message by the service layer
#     denial_message: str | None = Field(
#         default=None,
#         description="The governance denial message returned to the agent.",
#     )

#     timestamp: datetime

#     model_config = ConfigDict(
#         from_attributes=True,
#         json_schema_extra={
#             "example": {
#                 "id": "evt-uuid",
#                 "run_id": "run-uuid",
#                 "agent_id": "agent-uuid",
#                 "agent_name": "calc-only-bot",
#                 "trace_id": "trace-uuid",
#                 "tool_name": "weather",
#                 "attempted_input": {"city": "London"},
#                 "denial_message": "Access denied: tool 'weather' is not permitted for this agent.",
#                 "timestamp": "2024-01-15T10:30:01Z",
#             }
#         },
#     )


# class ViolationListResponse(BaseModel):
#     """Paginated governance violation list."""

#     violations: list[ViolationSchema]
#     total:      int
#     skip:       int
#     limit:      int


# # ─────────────────────────────────────────────────────────────────────────────
# # Run history schemas
# # ─────────────────────────────────────────────────────────────────────────────

# class RunSummary(BaseModel):
#     """
#     Lightweight AgentRun row for list views.

#     Omits the full event list (which is heavy) — callers wanting
#     the full event trace should use GET /audit/logs?run_id={id}.
#     """

#     id:              str
#     agent_id:        str
#     agent_name:      str | None = None
#     prompt:          str
#     status:          str
#     result:          str | None = None
#     trace_id:        str
#     started_at:      datetime
#     ended_at:        datetime | None = None
#     latency_ms:      float | None = None
#     violation_count: int = Field(default=0)

#     model_config = ConfigDict(from_attributes=True)


# class RunListResponse(BaseModel):
#     """Paginated run history list."""

#     runs:  list[RunSummary]
#     total: int
#     skip:  int
#     limit: int


# # ─────────────────────────────────────────────────────────────────────────────
# # Analytics schemas
# # ─────────────────────────────────────────────────────────────────────────────

# class ToolLatencyStat(BaseModel):
#     """
#     Latency statistics for a single tool across all runs.

#     avg_ms:  arithmetic mean of all tool_end latency_ms values for this tool.
#     p95_ms:  95th-percentile latency, approximated by sorting and indexing.
#              (SQLite has no native PERCENTILE_CONT; see audit_service for
#              the approximation strategy and its PostgreSQL equivalent.)
#     call_count: total number of successful (permitted) executions.
#     """

#     tool_name:  str
#     avg_ms:     float | None = Field(
#         default=None,
#         description="Mean execution time in milliseconds.",
#     )
#     p95_ms:     float | None = Field(
#         default=None,
#         description="Approximate 95th-percentile execution time in milliseconds.",
#     )
#     call_count: int = Field(
#         description="Number of permitted executions recorded.",
#     )


# class AgentStats(BaseModel):
#     """
#     Aggregate statistics for a single registered agent.

#     Returned by GET /analytics/stats/{agent_id}.
#     All counters reflect the agent's entire history.
#     """

#     agent_id:        str
#     agent_name:      str
#     total_runs:      int = Field(description="Total AgentRun rows for this agent.")
#     completed_runs:  int = Field(description="Runs with status='completed'.")
#     failed_runs:     int = Field(description="Runs with status='failed'.")
#     total_events:    int = Field(description="Total AgentEvent rows for this agent.")
#     total_tool_calls: int = Field(description="tool_call events (permitted + blocked).")
#     total_violations: int = Field(description="violation events (permitted=False).")
#     violation_rate:  float = Field(
#         description="Percentage of tool_call events that were blocked (0–100)."
#     )
#     avg_run_latency_ms: float | None = Field(
#         default=None,
#         description="Mean run duration in milliseconds across completed runs.",
#     )
#     tools_used: list[str] = Field(
#         default_factory=list,
#         description="Distinct tool names this agent has successfully called.",
#     )


# class SystemStats(BaseModel):
#     """
#     Platform-wide aggregate counters for the dashboard header cards.

#     Returned by GET /analytics/stats.
#     Reflects the state of the entire AgentWatch database.
#     """

#     total_agents: int
#     total_runs: int
#     total_events: int
#     total_tool_calls: int
#     total_violations: int

#     violation_rate: float = Field(
#         description="Percentage of tool_call events that were blocked (0–100)."
#     )

#     completed_runs: int
#     failed_runs: int

#     tool_latency: list[ToolLatencyStat] = Field(
#         default_factory=list,
#         description="Per-tool latency stats (same data as GET /analytics/tool-latency).",
#     )

#     total_interactions: int = Field(
#         default=0,
#         description="Total agent-to-agent interactions recorded.",
#     )

#     interactions_by_type: dict[str, int] = Field(
#         default_factory=dict,
#         description="Interaction count grouped by type.",
#     )

#     interactions_by_agent: dict[str, int] = Field(
#         default_factory=dict,
#         description="Outbound interaction count grouped by source agent name.",
#     )


# """
# app/schemas/audit.py
# ─────────────────────
# Pydantic v2 response schemas for audit, governance, and analytics endpoints.

# Schema hierarchy:
#   Audit layer
#     AuditEventSchema       — one AgentEvent row, enriched with agent name
#     AuditLogResponse       — paginated list of AuditEventSchema

#   Governance layer
#     ViolationSchema        — one violation event, denial message surfaced
#     ViolationListResponse  — paginated list of ViolationSchema

#   Run history layer
#     RunSummary             — lightweight AgentRun for list views
#     RunListResponse        — paginated list of RunSummary

#   Analytics layer
#     ToolLatencyStat        — avg + p95 latency for a single tool
#     AgentStats             — per-agent aggregate counters
#     SystemStats            — platform-wide counters for dashboard header

# Design notes:
#   • All response schemas use from_attributes=True so they can be built
#     directly from ORM instances via model_validate().
#   • Nullable fields use T | None rather than Optional[T] (Python 3.10+ style).
#   • Timestamps are datetime objects — FastAPI serialises them to ISO-8601.
#   • violation_rate is a float 0–100 representing the percentage of tool_call
#     events that were blocked.  Computed in audit_service, not here.
# """

# from __future__ import annotations
# from datetime import datetime
# from pydantic import BaseModel, Field, ConfigDict


# # ─────────────────────────────────────────────────────────────────────────────
# # Audit log schemas
# # ─────────────────────────────────────────────────────────────────────────────

# class AuditEventSchema(BaseModel):
#     """
#     A single AgentEvent row enriched with the owning agent's name.

#     Used in GET /audit/logs responses.  Includes every column from
#     AgentEvent plus agent_name (joined from the agents table) so
#     the caller does not need a separate lookup.
#     """

#     # AgentEvent primary fields
#     id:          str
#     run_id:      str
#     agent_id:    str
#     trace_id:    str
#     event_type:  str
#     tool_name:   str | None  = None
#     input_data:  dict | None = None
#     output_data: dict | None = None
#     permitted:   bool | None = None
#     latency_ms:  float | None = None
#     timestamp:   datetime

#     # Joined field — populated by the service layer
#     agent_name:  str | None = Field(
#         default=None,
#         description="Display name of the agent that produced this event.",
#     )

#     model_config = ConfigDict(
#         from_attributes=True,
#         json_schema_extra={
#             "example": {
#                 "id": "evt-uuid",
#                 "run_id": "run-uuid",
#                 "agent_id": "agent-uuid",
#                 "trace_id": "trace-uuid",
#                 "event_type": "tool_call",
#                 "tool_name": "calculator",
#                 "input_data": {"expression": "2+2"},
#                 "output_data": None,
#                 "permitted": True,
#                 "latency_ms": None,
#                 "timestamp": "2024-01-15T10:30:01Z",
#                 "agent_name": "finance-bot",
#             }
#         },
#     )


# class AuditLogResponse(BaseModel):
#     """Paginated audit event list."""

#     events: list[AuditEventSchema] = Field(
#         description="Ordered list of audit events (newest first)."
#     )
#     total: int = Field(
#         description="Total number of events matching the filter (before pagination)."
#     )
#     skip:  int = Field(description="Pagination offset applied to this response.")
#     limit: int = Field(description="Page size applied to this response.")


# # ─────────────────────────────────────────────────────────────────────────────
# # Governance / violation schemas
# # ─────────────────────────────────────────────────────────────────────────────

# class ViolationSchema(BaseModel):
#     """
#     A single governance violation event.

#     Surfaces the denial_message from output_data directly as a top-level
#     field so callers don't need to dig into the JSON blob.
#     """

#     id:             str
#     run_id:         str
#     agent_id:       str
#     agent_name:     str | None = None
#     trace_id:       str
#     tool_name:      str | None = None

#     # The input the LLM attempted to pass to the blocked tool
#     attempted_input: dict | None = Field(
#         default=None,
#         description="The arguments the agent tried to pass to the blocked tool.",
#     )

#     # Extracted from output_data.denial_message by the service layer
#     denial_message: str | None = Field(
#         default=None,
#         description="The governance denial message returned to the agent.",
#     )

#     timestamp: datetime

#     model_config = ConfigDict(
#         from_attributes=True,
#         json_schema_extra={
#             "example": {
#                 "id": "evt-uuid",
#                 "run_id": "run-uuid",
#                 "agent_id": "agent-uuid",
#                 "agent_name": "calc-only-bot",
#                 "trace_id": "trace-uuid",
#                 "tool_name": "weather",
#                 "attempted_input": {"city": "London"},
#                 "denial_message": "Access denied: tool 'weather' is not permitted for this agent.",
#                 "timestamp": "2024-01-15T10:30:01Z",
#             }
#         },
#     )


# class ViolationListResponse(BaseModel):
#     """Paginated governance violation list."""

#     violations: list[ViolationSchema]
#     total:      int
#     skip:       int
#     limit:      int


# # ─────────────────────────────────────────────────────────────────────────────
# # Run history schemas
# # ─────────────────────────────────────────────────────────────────────────────

# class RunSummary(BaseModel):
#     """
#     Lightweight AgentRun row for list views.

#     Omits the full event list (which is heavy) — callers wanting
#     the full event trace should use GET /audit/logs?run_id={id}.
#     """

#     id:              str
#     agent_id:        str
#     agent_name:      str | None = None
#     prompt:          str
#     status:          str
#     result:          str | None = None
#     trace_id:        str
#     started_at:      datetime
#     ended_at:        datetime | None = None
#     latency_ms:      float | None = None
#     violation_count: int = Field(default=0)

#     model_config = ConfigDict(from_attributes=True)


# class RunListResponse(BaseModel):
#     """Paginated run history list."""

#     runs:  list[RunSummary]
#     total: int
#     skip:  int
#     limit: int


# # ─────────────────────────────────────────────────────────────────────────────
# # Analytics schemas
# # ─────────────────────────────────────────────────────────────────────────────

# class ToolLatencyStat(BaseModel):
#     """
#     Latency statistics for a single tool across all runs.

#     avg_ms:  arithmetic mean of all tool_end latency_ms values for this tool.
#     p95_ms:  95th-percentile latency, approximated by sorting and indexing.
#              (SQLite has no native PERCENTILE_CONT; see audit_service for
#              the approximation strategy and its PostgreSQL equivalent.)
#     call_count: total number of successful (permitted) executions.
#     """

#     tool_name:  str
#     avg_ms:     float | None = Field(
#         default=None,
#         description="Mean execution time in milliseconds.",
#     )
#     p95_ms:     float | None = Field(
#         default=None,
#         description="Approximate 95th-percentile execution time in milliseconds.",
#     )
#     call_count: int = Field(
#         description="Number of permitted executions recorded.",
#     )


# class AgentStats(BaseModel):
#     """
#     Aggregate statistics for a single registered agent.

#     Returned by GET /analytics/stats/{agent_id}.
#     All counters reflect the agent's entire history.
#     """

#     agent_id:        str
#     agent_name:      str
#     total_runs:      int = Field(description="Total AgentRun rows for this agent.")
#     completed_runs:  int = Field(description="Runs with status='completed'.")
#     failed_runs:     int = Field(description="Runs with status='failed'.")
#     total_events:    int = Field(description="Total AgentEvent rows for this agent.")
#     total_tool_calls: int = Field(description="tool_call events (permitted + blocked).")
#     total_violations: int = Field(description="violation events (permitted=False).")
#     violation_rate:  float = Field(
#         description="Percentage of tool_call events that were blocked (0–100)."
#     )
#     avg_run_latency_ms: float | None = Field(
#         default=None,
#         description="Mean run duration in milliseconds across completed runs.",
#     )
#     tools_used: list[str] = Field(
#         default_factory=list,
#         description="Distinct tool names this agent has successfully called.",
#     )


# class SystemStats(BaseModel):
#     """
#     Platform-wide aggregate counters for the dashboard header cards.

#     Returned by GET /analytics/stats.
#     Reflects the state of the entire AgentWatch database.
#     """

#     total_agents:      int
#     total_runs:        int
#     total_events:      int
#     total_tool_calls:  int
#     total_violations:  int
#     violation_rate:    float = Field(
#         description="Percentage of tool_call events that were blocked (0–100)."
#     )
#     completed_runs:    int
#     failed_runs:       int
#     tool_latency:      list[ToolLatencyStat] = Field(
#         default_factory=list,
#         description="Per-tool latency stats (same data as GET /analytics/tool-latency).",
#     )

#     # ── Agent interaction counters (Phase 2) ──────────────────────────
#     total_interactions:    int = Field(
#         default=0,
#         description="Total agent-to-agent interactions recorded.",
#     )
#     interactions_by_type:  dict[str, int] = Field(
#         default_factory=dict,
#         description="Interaction count grouped by type (handoff, delegation, request, response).",
#     )
#     interactions_by_agent: dict[str, int] = Field(
#         default_factory=dict,
#         description="Outbound interaction count grouped by source agent name.",
#     )

#     # ── Policy engine counters (Phase 3) ───────────────────────────────
#     total_policies: int = Field(
#         default=0,
#         description="Total governance policies defined.",
#     )
#     active_policies: int = Field(
#         default=0,
#         description="Policies currently active (is_active=True).",
#     )
#     total_policy_violations: int = Field(
#         default=0,
#         description="Total policy_violation events across all runs.",
#     )
#     violations_by_severity: dict[str, int] = Field(
#         default_factory=dict,
#         description="Policy violation count grouped by severity (LOW/MEDIUM/HIGH/CRITICAL).",
#     )












# """
# app/schemas/audit.py
# ─────────────────────
# Pydantic v2 response schemas for audit, governance, and analytics endpoints.

# Schema hierarchy:
#   Audit layer
#     AuditEventSchema       — one AgentEvent row, enriched with agent name
#     AuditLogResponse       — paginated list of AuditEventSchema

#   Governance layer
#     ViolationSchema        — one violation event, denial message surfaced
#     ViolationListResponse  — paginated list of ViolationSchema

#   Run history layer
#     RunSummary             — lightweight AgentRun for list views
#     RunListResponse        — paginated list of RunSummary

#   Analytics layer
#     ToolLatencyStat        — avg + p95 latency for a single tool
#     AgentStats             — per-agent aggregate counters
#     SystemStats            — platform-wide counters for dashboard header

# Design notes:
#   • All response schemas use from_attributes=True so they can be built
#     directly from ORM instances via model_validate().
#   • Nullable fields use T | None rather than Optional[T] (Python 3.10+ style).
#   • Timestamps are datetime objects — FastAPI serialises them to ISO-8601.
#   • violation_rate is a float 0–100 representing the percentage of tool_call
#     events that were blocked.  Computed in audit_service, not here.
# """

# from __future__ import annotations
# from datetime import datetime
# from pydantic import BaseModel, Field, ConfigDict


# # ─────────────────────────────────────────────────────────────────────────────
# # Audit log schemas
# # ─────────────────────────────────────────────────────────────────────────────

# class AuditEventSchema(BaseModel):
#     """
#     A single AgentEvent row enriched with the owning agent's name.

#     Used in GET /audit/logs responses.  Includes every column from
#     AgentEvent plus agent_name (joined from the agents table) so
#     the caller does not need a separate lookup.
#     """

#     # AgentEvent primary fields
#     id:          str
#     run_id:      str
#     agent_id:    str
#     trace_id:    str
#     event_type:  str
#     tool_name:   str | None  = None
#     input_data:  dict | None = None
#     output_data: dict | None = None
#     permitted:   bool | None = None
#     latency_ms:  float | None = None
#     timestamp:   datetime

#     # Joined field — populated by the service layer
#     agent_name:  str | None = Field(
#         default=None,
#         description="Display name of the agent that produced this event.",
#     )

#     model_config = ConfigDict(
#         from_attributes=True,
#         json_schema_extra={
#             "example": {
#                 "id": "evt-uuid",
#                 "run_id": "run-uuid",
#                 "agent_id": "agent-uuid",
#                 "trace_id": "trace-uuid",
#                 "event_type": "tool_call",
#                 "tool_name": "calculator",
#                 "input_data": {"expression": "2+2"},
#                 "output_data": None,
#                 "permitted": True,
#                 "latency_ms": None,
#                 "timestamp": "2024-01-15T10:30:01Z",
#                 "agent_name": "finance-bot",
#             }
#         },
#     )


# class AuditLogResponse(BaseModel):
#     """Paginated audit event list."""

#     events: list[AuditEventSchema] = Field(
#         description="Ordered list of audit events (newest first)."
#     )
#     total: int = Field(
#         description="Total number of events matching the filter (before pagination)."
#     )
#     skip:  int = Field(description="Pagination offset applied to this response.")
#     limit: int = Field(description="Page size applied to this response.")


# # ─────────────────────────────────────────────────────────────────────────────
# # Governance / violation schemas
# # ─────────────────────────────────────────────────────────────────────────────

# class ViolationSchema(BaseModel):
#     """
#     A single governance violation event.

#     Surfaces the denial_message from output_data directly as a top-level
#     field so callers don't need to dig into the JSON blob.
#     """

#     id:             str
#     run_id:         str
#     agent_id:       str
#     agent_name:     str | None = None
#     trace_id:       str
#     tool_name:      str | None = None

#     # The input the LLM attempted to pass to the blocked tool
#     attempted_input: dict | None = Field(
#         default=None,
#         description="The arguments the agent tried to pass to the blocked tool.",
#     )

#     # Extracted from output_data.denial_message by the service layer
#     denial_message: str | None = Field(
#         default=None,
#         description="The governance denial message returned to the agent.",
#     )

#     timestamp: datetime

#     model_config = ConfigDict(
#         from_attributes=True,
#         json_schema_extra={
#             "example": {
#                 "id": "evt-uuid",
#                 "run_id": "run-uuid",
#                 "agent_id": "agent-uuid",
#                 "agent_name": "calc-only-bot",
#                 "trace_id": "trace-uuid",
#                 "tool_name": "weather",
#                 "attempted_input": {"city": "London"},
#                 "denial_message": "Access denied: tool 'weather' is not permitted for this agent.",
#                 "timestamp": "2024-01-15T10:30:01Z",
#             }
#         },
#     )


# class ViolationListResponse(BaseModel):
#     """Paginated governance violation list."""

#     violations: list[ViolationSchema]
#     total:      int
#     skip:       int
#     limit:      int


# # ─────────────────────────────────────────────────────────────────────────────
# # Run history schemas
# # ─────────────────────────────────────────────────────────────────────────────

# class RunSummary(BaseModel):
#     """
#     Lightweight AgentRun row for list views.

#     Omits the full event list (which is heavy) — callers wanting
#     the full event trace should use GET /audit/logs?run_id={id}.
#     """

#     id:              str
#     agent_id:        str
#     agent_name:      str | None = None
#     prompt:          str
#     status:          str
#     result:          str | None = None
#     trace_id:        str
#     started_at:      datetime
#     ended_at:        datetime | None = None
#     latency_ms:      float | None = None
#     violation_count: int = Field(default=0)

#     model_config = ConfigDict(from_attributes=True)


# class RunListResponse(BaseModel):
#     """Paginated run history list."""

#     runs:  list[RunSummary]
#     total: int
#     skip:  int
#     limit: int


# # ─────────────────────────────────────────────────────────────────────────────
# # Analytics schemas
# # ─────────────────────────────────────────────────────────────────────────────

# class ToolLatencyStat(BaseModel):
#     """
#     Latency statistics for a single tool across all runs.

#     avg_ms:  arithmetic mean of all tool_end latency_ms values for this tool.
#     p95_ms:  95th-percentile latency, approximated by sorting and indexing.
#              (SQLite has no native PERCENTILE_CONT; see audit_service for
#              the approximation strategy and its PostgreSQL equivalent.)
#     call_count: total number of successful (permitted) executions.
#     """

#     tool_name:  str
#     avg_ms:     float | None = Field(
#         default=None,
#         description="Mean execution time in milliseconds.",
#     )
#     p95_ms:     float | None = Field(
#         default=None,
#         description="Approximate 95th-percentile execution time in milliseconds.",
#     )
#     call_count: int = Field(
#         description="Number of permitted executions recorded.",
#     )



# # ─────────────────────────────────────────────────────────────────────────────
# # Trust Score schemas (Phase 4)
# # ─────────────────────────────────────────────────────────────────────────────

# class TrustBreakdown(BaseModel):
#     """
#     Fully explainable breakdown of how a trust score was computed.

#     Every contributing factor is listed with its raw count and point
#     contribution so callers can explain the score to a user.
#     """
#     base_score:                 float
#     governance_violations:      int
#     policy_violations:          int
#     high_severity_violations:   int
#     critical_violations:        int
#     failed_runs:                int
#     completed_runs:             int
#     permitted_tool_calls:       int
#     interactions:               int
#     deductions:                 dict[str, float]
#     additions:                  dict[str, float]
#     total_deductions:           float
#     total_additions:            float
#     final_score:                float
#     trust_level:                str


# class AgentTrustResponse(BaseModel):
#     """
#     Trust score response for a single agent.

#     Returned by GET /analytics/trust/{agent_id}.
#     """
#     agent_id:    str
#     agent_name:  str
#     trust_score: float = Field(description="0–100 trust score (100 = most trustworthy).")
#     trust_level: str   = Field(description="TRUSTED | MONITORED | WARNING | HIGH_RISK")
#     breakdown:   TrustBreakdown

#     model_config = ConfigDict(
#         json_schema_extra={
#             "example": {
#                 "agent_id":    "3fa85f64-...",
#                 "agent_name":  "finance-bot",
#                 "trust_score": 92.4,
#                 "trust_level": "TRUSTED",
#                 "breakdown": {},
#             }
#         }
#     )


# class SystemTrustResponse(BaseModel):
#     """
#     Platform-wide trust score aggregates.

#     Returned by GET /analytics/trust.
#     """
#     average_trust_score: float = Field(
#         description="Mean trust score across all registered agents."
#     )
#     trust_distribution:  dict[str, int] = Field(
#         description="Agent count per trust level (TRUSTED/MONITORED/WARNING/HIGH_RISK)."
#     )


# class AgentStats(BaseModel):
#     """
#     Aggregate statistics for a single registered agent.

#     Returned by GET /analytics/stats/{agent_id}.
#     All counters reflect the agent's entire history.
#     """

#     agent_id:        str
#     agent_name:      str
#     total_runs:      int = Field(description="Total AgentRun rows for this agent.")
#     completed_runs:  int = Field(description="Runs with status='completed'.")
#     failed_runs:     int = Field(description="Runs with status='failed'.")
#     total_events:    int = Field(description="Total AgentEvent rows for this agent.")
#     total_tool_calls: int = Field(description="tool_call events (permitted + blocked).")
#     total_violations: int = Field(description="violation events (permitted=False).")
#     violation_rate:  float = Field(
#         description="Percentage of tool_call events that were blocked (0–100)."
#     )
#     avg_run_latency_ms: float | None = Field(
#         default=None,
#         description="Mean run duration in milliseconds across completed runs.",
#     )
#     tools_used: list[str] = Field(
#         default_factory=list,
#         description="Distinct tool names this agent has successfully called.",
#     )

#     # ── Trust Score (Phase 4) ─────────────────────────────────────────
#     trust_score: float = Field(
#         default=100.0,
#         description="Computed trust score 0–100 (100 = most trustworthy).",
#     )
#     trust_level: str = Field(
#         default="TRUSTED",
#         description="Trust level: TRUSTED | MONITORED | WARNING | HIGH_RISK",
#     )


# class SystemStats(BaseModel):
#     """
#     Platform-wide aggregate counters for the dashboard header cards.

#     Returned by GET /analytics/stats.
#     Reflects the state of the entire AgentWatch database.
#     """

#     total_agents:      int
#     total_runs:        int
#     total_events:      int
#     total_tool_calls:  int
#     total_violations:  int
#     violation_rate:    float = Field(
#         description="Percentage of tool_call events that were blocked (0–100)."
#     )
#     completed_runs:    int
#     failed_runs:       int
#     tool_latency:      list[ToolLatencyStat] = Field(
#         default_factory=list,
#         description="Per-tool latency stats (same data as GET /analytics/tool-latency).",
#     )

#     # ── Agent interaction counters (Phase 2) ──────────────────────────
#     total_interactions:    int = Field(
#         default=0,
#         description="Total agent-to-agent interactions recorded.",
#     )
#     interactions_by_type:  dict[str, int] = Field(
#         default_factory=dict,
#         description="Interaction count grouped by type (handoff, delegation, request, response).",
#     )
#     interactions_by_agent: dict[str, int] = Field(
#         default_factory=dict,
#         description="Outbound interaction count grouped by source agent name.",
#     )

#     # ── Policy engine counters (Phase 3) ───────────────────────────────
#     total_policies: int = Field(
#         default=0,
#         description="Total governance policies defined.",
#     )
#     active_policies: int = Field(
#         default=0,
#         description="Policies currently active (is_active=True).",
#     )
#     total_policy_violations: int = Field(
#         default=0,
#         description="Total policy_violation events across all runs.",
#     )
#     violations_by_severity: dict[str, int] = Field(
#         default_factory=dict,
#         description="Policy violation count grouped by severity (LOW/MEDIUM/HIGH/CRITICAL).",
#     )

#     # ── Trust Score aggregates (Phase 4) ──────────────────────────────
#     average_trust_score: float = Field(
#         default=0.0,
#         description="Mean trust score across all registered agents (0–100).",
#     )
#     trust_distribution: dict[str, int] = Field(
#         default_factory=dict,
#         description="Agent count per trust level (TRUSTED/MONITORED/WARNING/HIGH_RISK).",
#     )





"""
app/schemas/audit.py
─────────────────────
Pydantic v2 response schemas for audit, governance, and analytics endpoints.

Schema hierarchy:
  Audit layer
    AuditEventSchema       — one AgentEvent row, enriched with agent name
    AuditLogResponse       — paginated list of AuditEventSchema

  Governance layer
    ViolationSchema        — one violation event, denial message surfaced
    ViolationListResponse  — paginated list of ViolationSchema

  Run history layer
    RunSummary             — lightweight AgentRun for list views
    RunListResponse        — paginated list of RunSummary

  Analytics layer
    ToolLatencyStat        — avg + p95 latency for a single tool
    AgentStats             — per-agent aggregate counters
    SystemStats            — platform-wide counters for dashboard header

Design notes:
  • All response schemas use from_attributes=True so they can be built
    directly from ORM instances via model_validate().
  • Nullable fields use T | None rather than Optional[T] (Python 3.10+ style).
  • Timestamps are datetime objects — FastAPI serialises them to ISO-8601.
  • violation_rate is a float 0–100 representing the percentage of tool_call
    events that were blocked.  Computed in audit_service, not here.
"""

from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


# ─────────────────────────────────────────────────────────────────────────────
# Audit log schemas
# ─────────────────────────────────────────────────────────────────────────────

class AuditEventSchema(BaseModel):
    """
    A single AgentEvent row enriched with the owning agent's name.

    Used in GET /audit/logs responses.  Includes every column from
    AgentEvent plus agent_name (joined from the agents table) so
    the caller does not need a separate lookup.
    """

    # AgentEvent primary fields
    id:          str
    run_id:      str
    agent_id:    str
    trace_id:    str
    event_type:  str
    tool_name:   str | None  = None
    input_data:  dict | None = None
    output_data: dict | None = None
    permitted:   bool | None = None
    latency_ms:  float | None = None
    timestamp:   datetime

    # Joined field — populated by the service layer
    agent_name:  str | None = Field(
        default=None,
        description="Display name of the agent that produced this event.",
    )

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "evt-uuid",
                "run_id": "run-uuid",
                "agent_id": "agent-uuid",
                "trace_id": "trace-uuid",
                "event_type": "tool_call",
                "tool_name": "calculator",
                "input_data": {"expression": "2+2"},
                "output_data": None,
                "permitted": True,
                "latency_ms": None,
                "timestamp": "2024-01-15T10:30:01Z",
                "agent_name": "finance-bot",
            }
        },
    )


class AuditLogResponse(BaseModel):
    """Paginated audit event list."""

    events: list[AuditEventSchema] = Field(
        description="Ordered list of audit events (newest first)."
    )
    total: int = Field(
        description="Total number of events matching the filter (before pagination)."
    )
    skip:  int = Field(description="Pagination offset applied to this response.")
    limit: int = Field(description="Page size applied to this response.")


# ─────────────────────────────────────────────────────────────────────────────
# Governance / violation schemas
# ─────────────────────────────────────────────────────────────────────────────

class ViolationSchema(BaseModel):
    """
    A single governance violation event.

    Surfaces the denial_message from output_data directly as a top-level
    field so callers don't need to dig into the JSON blob.
    """

    id:             str
    run_id:         str
    agent_id:       str
    agent_name:     str | None = None
    trace_id:       str
    tool_name:      str | None = None

    # The input the LLM attempted to pass to the blocked tool
    attempted_input: dict | None = Field(
        default=None,
        description="The arguments the agent tried to pass to the blocked tool.",
    )

    # Extracted from output_data.denial_message by the service layer
    denial_message: str | None = Field(
        default=None,
        description="The governance denial message returned to the agent.",
    )

    timestamp: datetime

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "evt-uuid",
                "run_id": "run-uuid",
                "agent_id": "agent-uuid",
                "agent_name": "calc-only-bot",
                "trace_id": "trace-uuid",
                "tool_name": "weather",
                "attempted_input": {"city": "London"},
                "denial_message": "Access denied: tool 'weather' is not permitted for this agent.",
                "timestamp": "2024-01-15T10:30:01Z",
            }
        },
    )


class ViolationListResponse(BaseModel):
    """Paginated governance violation list."""

    violations: list[ViolationSchema]
    total:      int
    skip:       int
    limit:      int


# ─────────────────────────────────────────────────────────────────────────────
# Run history schemas
# ─────────────────────────────────────────────────────────────────────────────

class RunSummary(BaseModel):
    """
    Lightweight AgentRun row for list views.

    Omits the full event list (which is heavy) — callers wanting
    the full event trace should use GET /audit/logs?run_id={id}.
    """

    id:              str
    agent_id:        str
    agent_name:      str | None = None
    prompt:          str
    status:          str
    result:          str | None = None
    trace_id:        str
    started_at:      datetime
    ended_at:        datetime | None = None
    latency_ms:      float | None = None
    violation_count: int = Field(default=0)

    model_config = ConfigDict(from_attributes=True)


class RunListResponse(BaseModel):
    """Paginated run history list."""

    runs:  list[RunSummary]
    total: int
    skip:  int
    limit: int


# ─────────────────────────────────────────────────────────────────────────────
# Analytics schemas
# ─────────────────────────────────────────────────────────────────────────────

class ToolLatencyStat(BaseModel):
    """
    Latency statistics for a single tool across all runs.

    avg_ms:  arithmetic mean of all tool_end latency_ms values for this tool.
    p95_ms:  95th-percentile latency, approximated by sorting and indexing.
             (SQLite has no native PERCENTILE_CONT; see audit_service for
             the approximation strategy and its PostgreSQL equivalent.)
    call_count: total number of successful (permitted) executions.
    """

    tool_name:  str
    avg_ms:     float | None = Field(
        default=None,
        description="Mean execution time in milliseconds.",
    )
    p95_ms:     float | None = Field(
        default=None,
        description="Approximate 95th-percentile execution time in milliseconds.",
    )
    call_count: int = Field(
        description="Number of permitted executions recorded.",
    )



# ─────────────────────────────────────────────────────────────────────────────
# Trust Score schemas (Phase 4)
# ─────────────────────────────────────────────────────────────────────────────

class TrustBreakdown(BaseModel):
    """
    Fully explainable breakdown of how a trust score was computed.

    Every contributing factor is listed with its raw count and point
    contribution so callers can explain the score to a user.
    """
    base_score:                 float
    governance_violations:      int
    policy_violations:          int
    high_severity_violations:   int
    critical_violations:        int
    failed_runs:                int
    completed_runs:             int
    permitted_tool_calls:       int
    interactions:               int
    deductions:                 dict[str, float]
    additions:                  dict[str, float]
    total_deductions:           float
    total_additions:            float
    final_score:                float
    trust_level:                str


class AgentTrustResponse(BaseModel):
    """
    Trust score response for a single agent.

    Returned by GET /analytics/trust/{agent_id}.
    """
    agent_id:    str
    agent_name:  str
    trust_score: float = Field(description="0–100 trust score (100 = most trustworthy).")
    trust_level: str   = Field(description="TRUSTED | MONITORED | WARNING | HIGH_RISK")
    breakdown:   TrustBreakdown

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "agent_id":    "3fa85f64-...",
                "agent_name":  "finance-bot",
                "trust_score": 92.4,
                "trust_level": "TRUSTED",
                "breakdown": {},
            }
        }
    )


class SystemTrustResponse(BaseModel):
    """
    Platform-wide trust score aggregates.

    Returned by GET /analytics/trust.
    """
    average_trust_score: float = Field(
        description="Mean trust score across all registered agents."
    )
    trust_distribution:  dict[str, int] = Field(
        description="Agent count per trust level (TRUSTED/MONITORED/WARNING/HIGH_RISK)."
    )



# ─────────────────────────────────────────────────────────────────────────────
# Risk Score schemas (Phase 5)
# ─────────────────────────────────────────────────────────────────────────────

class RiskBreakdown(BaseModel):
    """
    Fully explainable breakdown of how a risk score was computed.

    Every contributing factor is listed with its raw count and point
    contribution so callers can explain the score to a user.
    """
    base_score:                    float
    governance_violations:         int
    policy_violations:             int
    high_severity_violations:      int
    critical_severity_violations:  int
    failed_runs:                   int
    denied_tool_calls:             int
    interactions:                  dict[str, int]
    completed_runs:                int
    permitted_tool_calls:          int
    additions:                     dict[str, float]
    reductions:                    dict[str, float]
    total_additions:               float
    total_reductions:              float
    final_score:                   float
    risk_level:                    str


class AgentRiskResponse(BaseModel):
    """
    Risk score response for a single agent.

    Returned by GET /analytics/risk/{agent_id}.
    """
    agent_id:   str
    agent_name: str
    risk_score: float = Field(description="0–100 risk score (0 = safest, 100 = most dangerous).")
    risk_level: str   = Field(description="SAFE | LOW | MEDIUM | HIGH | CRITICAL")
    breakdown:  RiskBreakdown

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "agent_id":   "3fa85f64-...",
                "agent_name": "finance-bot",
                "risk_score": 42.5,
                "risk_level": "LOW",
                "breakdown":  {},
            }
        }
    )


class SystemRiskResponse(BaseModel):
    """
    Platform-wide risk score aggregates.

    Returned by GET /analytics/risk.
    """
    average_risk_score: float = Field(
        description="Mean risk score across all registered agents."
    )
    risk_distribution:  dict[str, int] = Field(
        description="Agent count per risk level (SAFE/LOW/MEDIUM/HIGH/CRITICAL)."
    )


class AgentStats(BaseModel):
    """
    Aggregate statistics for a single registered agent.

    Returned by GET /analytics/stats/{agent_id}.
    All counters reflect the agent's entire history.
    """

    agent_id:        str
    agent_name:      str
    total_runs:      int = Field(description="Total AgentRun rows for this agent.")
    completed_runs:  int = Field(description="Runs with status='completed'.")
    failed_runs:     int = Field(description="Runs with status='failed'.")
    total_events:    int = Field(description="Total AgentEvent rows for this agent.")
    total_tool_calls: int = Field(description="tool_call events (permitted + blocked).")
    total_violations: int = Field(description="violation events (permitted=False).")
    violation_rate:  float = Field(
        description="Percentage of tool_call events that were blocked (0–100)."
    )
    avg_run_latency_ms: float | None = Field(
        default=None,
        description="Mean run duration in milliseconds across completed runs.",
    )
    tools_used: list[str] = Field(
        default_factory=list,
        description="Distinct tool names this agent has successfully called.",
    )

    # ── Trust Score (Phase 4) ─────────────────────────────────────────
    trust_score: float = Field(
        default=100.0,
        description="Computed trust score 0–100 (100 = most trustworthy).",
    )
    trust_level: str = Field(
        default="TRUSTED",
        description="Trust level: TRUSTED | MONITORED | WARNING | HIGH_RISK",
    )

    # ── Risk Score (Phase 5) ──────────────────────────────────────────
    risk_score: float = Field(
        default=0.0,
        description="Computed risk score 0–100 (0 = safest, 100 = most dangerous).",
    )
    risk_level: str = Field(
        default="SAFE",
        description="Risk level: SAFE | LOW | MEDIUM | HIGH | CRITICAL",
    )


class SystemStats(BaseModel):
    """
    Platform-wide aggregate counters for the dashboard header cards.

    Returned by GET /analytics/stats.
    Reflects the state of the entire AgentWatch database.
    """

    total_agents:      int
    total_runs:        int
    total_events:      int
    total_tool_calls:  int
    total_violations:  int
    violation_rate:    float = Field(
        description="Percentage of tool_call events that were blocked (0–100)."
    )
    completed_runs:    int
    failed_runs:       int
    tool_latency:      list[ToolLatencyStat] = Field(
        default_factory=list,
        description="Per-tool latency stats (same data as GET /analytics/tool-latency).",
    )

    # ── Agent interaction counters (Phase 2) ──────────────────────────
    total_interactions:    int = Field(
        default=0,
        description="Total agent-to-agent interactions recorded.",
    )
    interactions_by_type:  dict[str, int] = Field(
        default_factory=dict,
        description="Interaction count grouped by type (handoff, delegation, request, response).",
    )
    interactions_by_agent: dict[str, int] = Field(
        default_factory=dict,
        description="Outbound interaction count grouped by source agent name.",
    )

    # ── Policy engine counters (Phase 3) ───────────────────────────────
    total_policies: int = Field(
        default=0,
        description="Total governance policies defined.",
    )
    active_policies: int = Field(
        default=0,
        description="Policies currently active (is_active=True).",
    )
    total_policy_violations: int = Field(
        default=0,
        description="Total policy_violation events across all runs.",
    )
    violations_by_severity: dict[str, int] = Field(
        default_factory=dict,
        description="Policy violation count grouped by severity (LOW/MEDIUM/HIGH/CRITICAL).",
    )

    # ── Trust Score aggregates (Phase 4) ──────────────────────────────
    average_trust_score: float = Field(
        default=0.0,
        description="Mean trust score across all registered agents (0–100).",
    )
    trust_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="Agent count per trust level (TRUSTED/MONITORED/WARNING/HIGH_RISK).",
    )

    # ── Risk Score aggregates (Phase 5) ───────────────────────────────
    average_risk_score: float = Field(
        default=0.0,
        description="Mean risk score across all registered agents (0–100).",
    )
    risk_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="Agent count per risk level (SAFE/LOW/MEDIUM/HIGH/CRITICAL).",
    )