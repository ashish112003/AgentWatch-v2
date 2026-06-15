"""
app/schemas/agent.py
─────────────────────
Pydantic v2 request and response schemas for Agent resources.

Schema layering pattern used throughout AgentWatch:
  ┌─────────────────────────────────────────────────────────────┐
  │  AgentBase          shared field definitions                │
  │    ↑                                                        │
  │  AgentCreate        inbound POST body (adds secret field)   │
  │  AgentResponse      outbound GET body  (adds id, timestamps)│
  │  AgentSummary       lightweight list item (no hashed_secret)│
  └─────────────────────────────────────────────────────────────┘

Why NOT expose the ORM model directly?
  SQLAlchemy ORM models are mutable, tied to a DB session, and may
  include internal fields (e.g. hashed_secret) that must never be
  serialised to HTTP responses.  Pydantic schemas act as an
  explicit contract between the database layer and the API surface.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, ConfigDict


# ── Shared base ───────────────────────────────────────────────────────────────

class AgentBase(BaseModel):
    """
    Fields shared by every Agent schema variant.
    Inherit from this to avoid repeating field definitions.
    """

    name: Annotated[
        str,
        Field(
            min_length=2,
            max_length=120,
            description="Unique human-readable name for this agent.",
            examples=["finance-bot", "weather-agent"],
        ),
    ]

    description: str | None = Field(
        default=None,
        max_length=500,
        description="Optional free-text description of what this agent does.",
        examples=["Handles financial Q&A using calculator and data tools."],
    )

    allowed_tools: list[str] = Field(
        default_factory=list,
        description=(
            "List of tool names this agent is permitted to call. "
            "Any tool call outside this list will trigger a governance violation. "
            "Valid values: 'calculator', 'weather', 'file_reader'."
        ),
        examples=[["calculator", "weather"]],
    )

    # ── Field-level validation ────────────────────────────────────────
    @field_validator("name")
    @classmethod
    def name_must_be_slug(cls, v: str) -> str:
        """
        Enforce URL-safe agent names.

        Names are used as identifiers in audit logs and JWT claims,
        so we restrict them to lowercase alphanumerics and hyphens.
        This also prevents SQL-injection-style surprises in log queries.
        """
        import re
        if not re.match(r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$", v):
            raise ValueError(
                "Agent name must be lowercase alphanumeric with hyphens only "
                "(e.g. 'my-agent').  Must start and end with alphanumeric."
            )
        return v

    @field_validator("allowed_tools")
    @classmethod
    def tools_must_be_known(cls, v: list[str]) -> list[str]:
        """
        Validate that only recognised tool names are in the allow-list.

        This catches typos at registration time rather than silently
        creating an agent that can never actually call any tools.
        """
        valid_tools = {
            "calculator", "weather", "file_reader",
            "datetime_tool", "currency_converter", "wikipedia_search",
            "text_summarizer", "word_counter", "json_formatter",
            "uuid_generator",
        }
        unknown = set(v) - valid_tools
        if unknown:
            raise ValueError(
                f"Unknown tool(s): {unknown}. "
                f"Valid tools are: {valid_tools}"
            )
        # Deduplicate while preserving order
        seen: set[str] = set()
        return [t for t in v if not (t in seen or seen.add(t))]  # type: ignore[func-returns-value]


# ── Request schemas ───────────────────────────────────────────────────────────

class AgentCreate(AgentBase):
    """
    Request body for POST /agents/register.

    `secret` is the plain-text credential the agent will use.
    It is hashed with bcrypt before storage — never stored in plain text.

    Think of it like a service-account password:
      • The registering client supplies it once.
      • We store only the bcrypt hash.
      • The JWT issued at registration IS the ongoing credential;
        the secret is not needed again unless re-authenticating.
    """

    secret: Annotated[
        str,
        Field(
            min_length=12,
            description=(
                "Plain-text secret for this agent.  "
                "Min 12 characters.  Stored as bcrypt hash only."
            ),
            examples=["super-secret-agent-key-42"],
        ),
    ]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "finance-bot",
                "description": "Handles financial calculations.",
                "allowed_tools": ["calculator"],
                "secret": "super-secret-agent-key-42",
            }
        }
    )


# ── Response schemas ──────────────────────────────────────────────────────────

class AgentResponse(AgentBase):
    """
    Full agent detail returned by GET /agents/{agent_id}.

    `from_attributes = True` (formerly orm_mode) allows Pydantic to
    read values from SQLAlchemy ORM model attributes directly, so we
    can do AgentResponse.model_validate(orm_obj) in service code.

    Note: hashed_secret is intentionally ABSENT — never returned via API.
    """

    id: str = Field(description="UUID of the agent.")
    created_at: datetime = Field(description="UTC timestamp of registration.")

    model_config = ConfigDict(
        from_attributes=True,  # enables .model_validate(orm_instance)
        json_schema_extra={
            "example": {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "name": "finance-bot",
                "description": "Handles financial calculations.",
                "allowed_tools": ["calculator"],
                "created_at": "2024-01-15T10:30:00Z",
            }
        },
    )


class AgentSummary(BaseModel):
    """
    Lightweight agent representation used in list responses.

    Returned by GET /agents — only essential fields to keep
    the payload small when there are many registered agents.
    """

    id: str
    name: str
    description: str | None
    allowed_tools: list[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AgentListResponse(BaseModel):
    """Wrapper for paginated agent list results."""

    agents: list[AgentSummary]
    total: int = Field(description="Total number of registered agents.")