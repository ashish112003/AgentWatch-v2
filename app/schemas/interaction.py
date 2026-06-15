"""
app/schemas/interaction.py
───────────────────────────
Pydantic v2 schemas for the AgentInteraction resource.

Schema hierarchy:
  InteractionCreate       — POST /agent-interactions request body
  InteractionResponse     — single interaction in API responses
  InteractionListResponse — paginated list of interactions

Design follows the same patterns as app/schemas/agent.py and
app/schemas/audit.py:
  • from_attributes=True on response schemas for direct ORM → Pydantic mapping.
  • Nullable fields use T | None (Python 3.10+ style).
  • Timestamps as datetime objects; FastAPI serialises to ISO-8601.
  • Joined fields (source_agent_name, target_agent_name) populated by
    the service layer so callers don't need follow-up lookups.

Valid interaction_type values mirror the ORM model:
  handoff    — source transfers control to target
  delegation — source assigns a sub-task, retains control
  request    — source asks target for information
  response   — source replies to a prior request
"""

from __future__ import annotations
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, ConfigDict

# Valid interaction types — single source of truth for validator and docs.
VALID_INTERACTION_TYPES = frozenset({"handoff", "delegation", "request", "response"})


# ── Request schema ────────────────────────────────────────────────────────────

class InteractionCreate(BaseModel):
    """
    Request body for POST /agent-interactions.

    Both agent IDs must be UUIDs of agents already registered in the system.
    The service layer validates they exist before persisting the record.
    """

    source_agent_id: Annotated[
        str,
        Field(
            description="UUID of the agent initiating the interaction.",
            examples=["3fa85f64-5717-4562-b3fc-2c963f66afa6"],
        ),
    ]

    target_agent_id: Annotated[
        str,
        Field(
            description="UUID of the agent receiving the interaction.",
            examples=["7c9e6679-7425-40de-944b-e07fc1f90ae7"],
        ),
    ]

    interaction_type: Annotated[
        str,
        Field(
            description=(
                "Nature of the interaction. "
                "One of: handoff, delegation, request, response."
            ),
            examples=["handoff"],
        ),
    ]

    message: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Optional natural-language message from source to target. "
            "E.g. 'Research climate change impacts for the Q3 report.'"
        ),
        examples=["Research climate change impacts"],
    )

    @field_validator("interaction_type")
    @classmethod
    def interaction_type_must_be_valid(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in VALID_INTERACTION_TYPES:
            raise ValueError(
                f"Invalid interaction_type '{v}'. "
                f"Must be one of: {sorted(VALID_INTERACTION_TYPES)}"
            )
        return v

    @field_validator("source_agent_id", "target_agent_id")
    @classmethod
    def agent_id_must_be_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Agent ID cannot be empty.")
        return v.strip()

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source_agent_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "target_agent_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
                "interaction_type": "handoff",
                "message": "Research climate change impacts for the Q3 report.",
            }
        }
    )


# ── Response schemas ──────────────────────────────────────────────────────────

class InteractionResponse(BaseModel):
    """
    Single AgentInteraction row as returned by the API.

    Includes joined agent names (source_agent_name, target_agent_name)
    so callers can render human-readable output without extra lookups.
    """

    id:               str
    source_agent_id:  str
    target_agent_id:  str
    interaction_type: str
    message:          str | None = None
    created_at:       datetime

    # Populated by the service layer via agent name lookups.
    source_agent_name: str | None = Field(
        default=None,
        description="Display name of the source agent.",
    )
    target_agent_name: str | None = Field(
        default=None,
        description="Display name of the target agent.",
    )

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "aabb1122-...",
                "source_agent_id": "3fa85f64-...",
                "target_agent_id": "7c9e6679-...",
                "interaction_type": "handoff",
                "message": "Research climate change impacts",
                "created_at": "2024-01-15T10:30:00Z",
                "source_agent_name": "planner-bot",
                "target_agent_name": "researcher-bot",
            }
        },
    )


class InteractionListResponse(BaseModel):
    """Paginated list of agent interactions."""

    interactions: list[InteractionResponse]
    total:        int = Field(description="Total matching interactions before pagination.")
    skip:         int = Field(description="Pagination offset applied.")
    limit:        int = Field(description="Page size applied.")