"""
app/models/policy.py
─────────────────────
SQLAlchemy ORM models for the Policy Engine.

Two tables:
  policies       — named, reusable governance rules with typed configs
  agent_policies — many-to-many association between agents and policies

Design follows the same conventions as app/models/agent.py:
  • UUID string primary keys generated in Python via new_uuid().
  • JSON column for rule_config — flexible, schema-less rule parameters.
  • server_default=func.now() for all timestamps.
  • Explicit FK constraints with ondelete="CASCADE".

Policy rule types and their rule_config shapes:
  tool_allow    {"tool": "calculator"}
                Explicitly permit a tool (used to override broader denials).

  tool_deny     {"tool": "weather"}
                Block a specific tool regardless of allowed_tools list.

  rate_limit    {"max_calls_per_run": 3}
                Cap the total number of tool calls within a single run.

  prompt_guard  {"blocked_keywords": ["password", "secret", "key"]}
                Block runs whose prompt contains any of the listed words.
                Case-insensitive substring match.

  time_window   {"start_hour": 9, "end_hour": 18}
                Restrict agent execution to UTC hours [start_hour, end_hour).
                Values are 0-23 integers.

Severity levels (stored as strings for readability):
  LOW | MEDIUM | HIGH | CRITICAL

Evaluation order in policy_service:
  Policies are evaluated in severity order (CRITICAL first).
  The first matching blocking policy wins.  tool_allow rules are
  checked before tool_deny rules at execution time.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def new_uuid() -> str:
    """Generate a fresh UUID4 string."""
    return str(uuid.uuid4())


# Valid rule type strings — checked by the schema validator.
VALID_RULE_TYPES = frozenset({
    "tool_allow",
    "tool_deny",
    "rate_limit",
    "prompt_guard",
    "time_window",
})

# Valid severity strings — ordered from least to most critical.
SEVERITY_ORDER = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
VALID_SEVERITIES = frozenset(SEVERITY_ORDER)


class Policy(Base):
    """
    A named, reusable governance rule that can be attached to agents.

    Fields:
      name        — unique display name, used in denial messages and logs.
      description — optional human-readable explanation of the policy.
      rule_type   — one of VALID_RULE_TYPES (determines rule_config shape).
      rule_config — JSON dict with rule-specific parameters.
      severity    — impact level: LOW | MEDIUM | HIGH | CRITICAL.
      is_active   — soft-disable a policy without deleting it.
      created_at  — UTC creation timestamp.
    """

    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_uuid
    )
    name: Mapped[str] = mapped_column(
        String(120), unique=True, nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Rule classification — determines how rule_config is interpreted.
    rule_type: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True
    )

    # JSON dict with rule-specific parameters.
    # Schema varies by rule_type; validated in policy_service.
    rule_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Impact level — used for analytics grouping and log severity.
    severity: Mapped[str] = mapped_column(
        String(10), nullable=False, default="MEDIUM", index=True
    )

    # Soft-disable: inactive policies are loaded but never evaluated.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ── Relationship ──────────────────────────────────────────────────
    agent_policies: Mapped[list["AgentPolicy"]] = relationship(
        "AgentPolicy", back_populates="policy", lazy="selectin",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Policy id={self.id!r} name={self.name!r} "
            f"type={self.rule_type!r} severity={self.severity!r} "
            f"active={self.is_active!r}>"
        )


class AgentPolicy(Base):
    """
    Association table linking agents to policies (many-to-many).

    An agent can have multiple policies; a policy can be applied to
    multiple agents.  created_at records when the assignment was made.

    Unique constraint on (agent_id, policy_id) prevents duplicate
    assignments from creating redundant evaluation overhead.
    """

    __tablename__ = "agent_policies"
    __table_args__ = (
        UniqueConstraint("agent_id", "policy_id", name="uq_agent_policy"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_uuid
    )
    agent_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ── Relationships ──────────────────────────────────────────────────
    policy: Mapped["Policy"] = relationship(
        "Policy", back_populates="agent_policies", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<AgentPolicy agent={self.agent_id!r} "
            f"policy={self.policy_id!r}>"
        )