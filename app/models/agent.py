"""
app/models/agent.py
────────────────────
SQLAlchemy ORM models for the AgentWatch data layer.

Three core tables:
  • agents       — registered AI agents and their tool permissions
  • agent_runs   — individual execution sessions for an agent
  • agent_events — granular audit trail of everything that happened
                   inside a run (tool calls, violations, start/end)

Design notes:
  • All primary keys are string UUIDs generated in Python, not the DB.
    This makes IDs portable across environments and avoids auto-increment
    race conditions in distributed setups.
  • JSON columns (allowed_tools, input_data, output_data) give us
    flexible schema-less storage for tool payloads while keeping the
    rest of the schema strongly typed.
  • Timestamps use func.now() as server_default so they are set by the
    DB engine, making them consistent regardless of the app server's
    local clock.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def new_uuid() -> str:
    """Generate a fresh UUID4 string.  Used as default for PK columns."""
    return str(uuid.uuid4())


# ── Agent ─────────────────────────────────────────────────────────────────────

class Agent(Base):
    """
    Represents a registered AI agent.

    An agent has:
      • A unique identity (id + name).
      • A list of tools it is permitted to call (allowed_tools).
        Any tool call NOT in this list triggers a governance violation.
      • A hashed credential stored in `hashed_secret` for JWT issuance.
        (Think of it like a service-account password.)
    """

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_uuid
    )
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # JSON array of tool names this agent may call, e.g. ["calculator", "weather"]
    allowed_tools: Mapped[list] = mapped_column(JSON, default=list)

    # Bcrypt-hashed secret used to authenticate the agent and issue JWTs.
    # Never store raw secrets.
    hashed_secret: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ── Relationships ─────────────────────────────────────────────────
    # lazy="selectin" means SQLAlchemy will emit a SELECT IN query for
    # child records when the parent is loaded — efficient for async contexts.
    runs: Mapped[list["AgentRun"]] = relationship(
        "AgentRun", back_populates="agent", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Agent id={self.id!r} name={self.name!r}>"


# ── AgentRun ──────────────────────────────────────────────────────────────────

class AgentRun(Base):
    """
    Represents one complete execution of an agent against a prompt.

    Lifecycle: pending → running → completed | failed

    The trace_id ties together all AgentEvents belonging to this run,
    making it easy to reconstruct the full execution timeline.
    """

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_uuid
    )
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # The natural-language prompt that triggered this run.
    prompt: Mapped[str] = mapped_column(Text, nullable=False)

    # Run lifecycle status.
    # Possible values: "pending" | "running" | "completed" | "failed"
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)

    # The final response text produced by the agent (may be None if run failed).
    result: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Distributed tracing ID — shared with all AgentEvents in this run.
    trace_id: Mapped[str] = mapped_column(String(36), default=new_uuid, nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ─────────────────────────────────────────────────
    agent: Mapped["Agent"] = relationship("Agent", back_populates="runs")
    events: Mapped[list["AgentEvent"]] = relationship(
        "AgentEvent", back_populates="run", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<AgentRun id={self.id!r} agent_id={self.agent_id!r} status={self.status!r}>"


# ── AgentEvent ────────────────────────────────────────────────────────────────

class AgentEvent(Base):
    """
    Immutable audit record for a single event within an AgentRun.

    Event types:
      run_start   — emitted when the run begins
      tool_call   — emitted before a tool executes (permitted=True/False)
      tool_end    — emitted after a tool returns its result
      violation   — emitted when a tool call is BLOCKED by governance
      run_end     — emitted when the run finishes (success or failure)

    The `permitted` flag is the key governance signal:
      True  → tool was in agent.allowed_tools, execution proceeded
      False → tool was NOT in agent.allowed_tools, execution was blocked

    `latency_ms` is populated for tool_end events to track tool performance.
    """

    __tablename__ = "agent_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_uuid
    )

    # Foreign keys — every event belongs to both a run and an agent.
    # Storing agent_id directly (de-normalised) avoids a JOIN when
    # querying all events for a given agent.
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Distributed trace ID (same value as AgentRun.trace_id for easy correlation).
    trace_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # Event classification — see docstring above for valid values.
    event_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    # Tool name if this event is related to a tool call (None for run_start/run_end).
    tool_name: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Raw input sent to the tool (stored as JSON for flexibility).
    input_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Raw output returned by the tool (stored as JSON).
    output_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Governance gate result — True if execution was allowed, False if blocked.
    # None for non-tool events (run_start, run_end).
    permitted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Time taken for the tool to execute in milliseconds (tool_end events only).
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # ── Relationships ─────────────────────────────────────────────────
    run: Mapped["AgentRun"] = relationship("AgentRun", back_populates="events")

    def __repr__(self) -> str:
        return (
            f"<AgentEvent id={self.id!r} type={self.event_type!r} "
            f"tool={self.tool_name!r} permitted={self.permitted!r}>"
        )
    

# ── AgentInteraction ──────────────────────────────────────────────────────────

class AgentInteraction(Base):
    """
    Immutable record of a directed interaction between two registered agents.

    Tracks agent-to-agent communication for multi-agent observability.
    Every interaction has a source (initiating agent), a target (receiving
    agent), a typed relationship, and an optional message payload.

    Interaction types:
      handoff    — source transfers control to target completely
      delegation — source assigns a sub-task to target, retains control
      request    — source asks target for information or action
      response   — source replies to a prior request from target

    Design mirrors AgentEvent:
      • UUID string primary key (no auto-increment).
      • source_agent_id and target_agent_id are de-normalised FKs so
        queries like "all interactions for agent X" are a single WHERE
        clause with an OR, not a join chain.
      • created_at uses server_default so the DB clock is authoritative.
    """

    __tablename__ = "agent_interactions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_uuid
    )

    # The agent that initiates the interaction.
    source_agent_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The agent that receives the interaction.
    target_agent_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Typed relationship between the agents.
    # Allowed values: handoff | delegation | request | response
    interaction_type: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True
    )

    # Optional natural-language message from source to target.
    # E.g. "Research climate change impacts for Q3 report."
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # ── Relationships ─────────────────────────────────────────────────
    source_agent: Mapped["Agent"] = relationship(
        "Agent", foreign_keys=[source_agent_id], lazy="selectin"
    )
    target_agent: Mapped["Agent"] = relationship(
        "Agent", foreign_keys=[target_agent_id], lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<AgentInteraction id={self.id!r} "
            f"type={self.interaction_type!r} "
            f"src={self.source_agent_id!r} → tgt={self.target_agent_id!r}>"
        )