"""
app/schemas/policy.py
──────────────────────
Pydantic v2 schemas for the Policy Engine API.

Schema hierarchy:
  PolicyCreate          — POST /policies request body
  PolicyResponse        — single policy row in API responses
  PolicyListResponse    — paginated list of policies
  AgentPolicyResponse   — one assignment (agent + policy)
  PolicyViolationDetail — embedded in RunResponse when a policy blocks execution

Follows the same patterns as app/schemas/interaction.py:
  • from_attributes=True on response schemas.
  • field_validator for enum-like string fields.
  • Nullable fields use T | None.
  • ConfigDict(json_schema_extra=...) for OpenAPI examples.
"""

from __future__ import annotations
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field, field_validator, ConfigDict

from app.models.policy import VALID_RULE_TYPES, VALID_SEVERITIES, SEVERITY_ORDER


# ── rule_config validators per rule_type ─────────────────────────────────────

def _validate_rule_config(rule_type: str, config: dict) -> dict:
    """
    Validate that rule_config contains the required keys for the given rule_type.

    Called from PolicyCreate.validate_rule_config_shape.

    Raises:
        ValueError: Missing required keys or wrong value types.
    """
    if rule_type == "tool_allow" or rule_type == "tool_deny":
        if "tool" not in config or not isinstance(config["tool"], str):
            raise ValueError(
                f"rule_type='{rule_type}' requires rule_config={{'tool': '<tool_name>'}}."
            )

    elif rule_type == "rate_limit":
        mcp = config.get("max_calls_per_run")
        if not isinstance(mcp, int) or mcp < 1:
            raise ValueError(
                "rule_type='rate_limit' requires "
                "rule_config={'max_calls_per_run': <positive int>}."
            )

    elif rule_type == "prompt_guard":
        kw = config.get("blocked_keywords")
        if not isinstance(kw, list) or not kw or not all(isinstance(k, str) for k in kw):
            raise ValueError(
                "rule_type='prompt_guard' requires "
                "rule_config={'blocked_keywords': ['word1', 'word2', ...]}."
            )

    elif rule_type == "time_window":
        sh = config.get("start_hour")
        eh = config.get("end_hour")
        if (
            not isinstance(sh, int) or not isinstance(eh, int)
            or not (0 <= sh <= 23) or not (0 <= eh <= 23)
            or sh >= eh
        ):
            raise ValueError(
                "rule_type='time_window' requires "
                "rule_config={'start_hour': 0-23, 'end_hour': 0-23} "
                "with start_hour < end_hour."
            )

    return config


# ── Request schemas ───────────────────────────────────────────────────────────

class PolicyCreate(BaseModel):
    """Request body for POST /policies."""

    name: str = Field(
        min_length=2,
        max_length=120,
        description="Unique policy name. Used in denial messages and logs.",
        examples=["no-weather-tool", "rate-limit-3", "business-hours-only"],
    )
    description: str | None = Field(
        default=None,
        max_length=500,
        description="Optional human-readable explanation of the policy.",
    )
    rule_type: str = Field(
        description=(
            "Policy enforcement mechanism. "
            "One of: tool_allow, tool_deny, rate_limit, prompt_guard, time_window."
        ),
        examples=["tool_deny"],
    )
    rule_config: dict[str, Any] = Field(
        description=(
            "Rule-specific parameters. Shape depends on rule_type:\n"
            "  tool_allow/tool_deny: {'tool': 'calculator'}\n"
            "  rate_limit: {'max_calls_per_run': 3}\n"
            "  prompt_guard: {'blocked_keywords': ['password', 'secret']}\n"
            "  time_window: {'start_hour': 9, 'end_hour': 18}"
        ),
        examples=[{"tool": "weather"}],
    )
    severity: str = Field(
        default="MEDIUM",
        description="Impact level: LOW | MEDIUM | HIGH | CRITICAL.",
        examples=["HIGH"],
    )
    is_active: bool = Field(
        default=True,
        description="Whether this policy is evaluated during runs.",
    )

    @field_validator("rule_type")
    @classmethod
    def rule_type_must_be_valid(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in VALID_RULE_TYPES:
            raise ValueError(
                f"Invalid rule_type '{v}'. "
                f"Must be one of: {sorted(VALID_RULE_TYPES)}"
            )
        return v

    @field_validator("severity")
    @classmethod
    def severity_must_be_valid(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in VALID_SEVERITIES:
            raise ValueError(
                f"Invalid severity '{v}'. "
                f"Must be one of: {SEVERITY_ORDER}"
            )
        return v

    def validate_rule_config_shape(self) -> None:
        """
        Cross-field validation: checks rule_config against rule_type.
        Call explicitly after model construction (Pydantic v2 doesn't
        support cross-field validators on individual fields cleanly).
        """
        _validate_rule_config(self.rule_type, self.rule_config)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "no-weather-tool",
                "description": "Prevent agents from calling the weather tool.",
                "rule_type": "tool_deny",
                "rule_config": {"tool": "weather"},
                "severity": "HIGH",
                "is_active": True,
            }
        }
    )


# ── Response schemas ──────────────────────────────────────────────────────────

class PolicyResponse(BaseModel):
    """Single Policy row as returned by the API."""

    id:          str
    name:        str
    description: str | None = None
    rule_type:   str
    rule_config: dict[str, Any]
    severity:    str
    is_active:   bool
    created_at:  datetime

    # Populated by the service layer — number of agents this policy is attached to.
    agent_count: int = Field(default=0, description="Number of agents using this policy.")

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "aabb1122-...",
                "name": "no-weather-tool",
                "description": "Block weather tool.",
                "rule_type": "tool_deny",
                "rule_config": {"tool": "weather"},
                "severity": "HIGH",
                "is_active": True,
                "created_at": "2024-01-15T10:00:00Z",
                "agent_count": 2,
            }
        },
    )


class PolicyListResponse(BaseModel):
    """Paginated list of policies."""

    policies: list[PolicyResponse]
    total:    int
    skip:     int
    limit:    int


class AgentPolicyResponse(BaseModel):
    """One agent↔policy assignment."""

    id:         str
    agent_id:   str
    policy_id:  str
    created_at: datetime

    # Joined fields populated by the service layer
    agent_name:  str | None = None
    policy_name: str | None = None
    rule_type:   str | None = None
    severity:    str | None = None

    model_config = ConfigDict(from_attributes=True)


class AgentPolicyListResponse(BaseModel):
    """Paginated list of policy assignments for an agent."""

    policies: list[PolicyResponse]
    agent_id: str
    total:    int


class PolicyViolationDetail(BaseModel):
    """
    Structured detail for a policy-level violation.

    Embedded in the run response and audit events when a policy blocks
    execution before the tool governance layer is even reached.
    """

    policy_id:   str
    policy_name: str
    rule_type:   str
    severity:    str
    reason:      str   # human-readable denial message