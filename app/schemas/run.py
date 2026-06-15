"""
app/schemas/run.py
───────────────────
Pydantic v2 schemas for AgentRun request and response.

Schema overview:
  RunRequest      — POST /agents/run body (prompt only for MVP)
  RunResponse     — response after a completed run
  RunEventSchema  — serialised AgentEvent for embedding in responses
  RunStatus       — string literal type for run lifecycle states

Design notes:
  • RunResponse embeds a flat list of RunEventSchema objects so the
    caller gets the full audit trail in a single HTTP response.
    This avoids a follow-up GET /audit/logs call for the common case.
  • All timestamps are returned as ISO-8601 strings (FastAPI handles
    datetime → string serialisation automatically via Pydantic).
  • The `permitted` field on RunEventSchema is the key governance
    signal — False means a tool call was blocked.
"""

from __future__ import annotations
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


# ── Status literals ────────────────────────────────────────────────────────────

RunStatus = Literal["pending", "running", "completed", "failed"]

EventType = Literal["run_start", "tool_call", "tool_end", "violation", "run_end"]


# ── Request ───────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    """
    POST /agents/run request body.

    Intentionally minimal for Phase 3 — just a prompt.
    Phase 4 will add: tool_override, max_iterations, timeout_seconds.
    """

    prompt: str = Field(
        min_length=1,
        max_length=4000,
        description="The natural-language prompt for the agent to process.",
        examples=[
            "What is 1234 * 5678?",
            "What's the weather like in Tokyo?",
            "Read the readme.txt file and summarise it.",
        ],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "prompt": "What is the square root of 1764, and what is the weather in London?"
            }
        }
    )


# ── Event schema ──────────────────────────────────────────────────────────────

class RunEventSchema(BaseModel):
    """
    Serialised representation of a single AgentEvent for API responses.

    The `permitted` field is the governance audit signal:
      True  → tool was in the agent's allowed_tools, call proceeded
      False → tool was NOT allowed, call was blocked (violation)
      None  → event is not tool-related (run_start, run_end)
    """

    id:          str
    event_type:  str
    tool_name:   str | None = None
    input_data:  dict | None = None
    output_data: dict | None = None
    permitted:   bool | None = None
    latency_ms:  float | None = None
    timestamp:   datetime

    model_config = ConfigDict(from_attributes=True)


# ── Response ──────────────────────────────────────────────────────────────────

class RunResponse(BaseModel):
    """
    POST /agents/run response body.

    Contains the run metadata, the agent's final answer, and the
    full event trace so the caller can inspect every tool call made.
    """

    run_id:     str         = Field(description="UUID of this AgentRun.")
    agent_id:   str         = Field(description="UUID of the agent that ran.")
    trace_id:   str         = Field(description="Distributed trace ID (shared with all events).")
    status:     RunStatus   = Field(description="Final lifecycle status of the run.")
    prompt:     str         = Field(description="The prompt that was submitted.")
    result:     str | None  = Field(default=None, description="The agent's final response text.")
    started_at: datetime    = Field(description="UTC timestamp when the run started.")
    ended_at:   datetime | None = Field(default=None, description="UTC timestamp when the run ended.")

    # Computed convenience fields
    latency_ms: float | None = Field(
        default=None,
        description="Total run duration in milliseconds (ended_at - started_at).",
    )
    violation_count: int = Field(
        default=0,
        description="Number of governance violations detected in this run.",
    )

    # Full event trace embedded in the response
    events: list[RunEventSchema] = Field(
        default_factory=list,
        description="Ordered list of all audit events from this run.",
    )

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "run_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "agent_id": "1a2b3c4d-...",
                "trace_id": "9z8y7x6w-...",
                "status": "completed",
                "prompt": "What is sqrt(1764)?",
                "result": "The square root of 1764 is 42.",
                "started_at": "2024-01-15T10:30:00Z",
                "ended_at":   "2024-01-15T10:30:02Z",
                "latency_ms": 2041.5,
                "violation_count": 0,
                "events": [],
            }
        },
    )