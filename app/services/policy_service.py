"""
app/services/policy_service.py
────────────────────────────────
Policy Engine: CRUD operations and pre-run policy evaluation.

Architecture:
  Policy evaluation sits BEFORE the GovernanceEnforcer in the run pipeline:

    Agent → Policy Evaluation → GovernanceEnforcer → Tool Execution → Audit

  The two systems are complementary:
    • Policies are named, reusable rules attached to agents individually.
    • GovernanceEnforcer enforces the agent's allowed_tools list globally.
  A tool call is blocked if EITHER system says so.

Evaluation flow (called by execution_service before building the agent):
  1. Load all active policies attached to the agent (single JOIN query).
  2. Evaluate each policy type in order:
       a. prompt_guard  — checked once against the prompt string.
       b. time_window   — checked once against the current UTC hour.
       c. tool_deny     — adds tool names to a blocked-tools set.
       d. tool_allow    — adds tool names to an explicit-permit set.
       e. rate_limit    — stored for runtime enforcement (see below).
  3. If any prompt_guard or time_window policy triggers, return a
     PolicyViolationDetail immediately — the run is blocked before
     the LLM is even invoked.
  4. Return a PolicyEvaluationResult containing:
       - blocked_tools:     set of tool names blocked by tool_deny policies
       - explicitly_allowed: set of tool names permitted by tool_allow policies
       - rate_limit:        max tool calls per run (None = unlimited)
       - violation:         populated if the run itself is blocked

Rate limiting enforcement:
  The rate_limit check cannot be done before the run (we don't know how
  many tool calls will happen).  Instead the PolicyEvaluationResult carries
  the max_calls_per_run value.  execution_service checks it after the run
  completes: if callback.records > max_calls, it emits a policy_violation
  event for each call over the limit (those calls already executed, which
  is a known limitation of post-hoc rate limiting without streaming).
  A tighter approach (interrupt mid-run) requires LangGraph streaming hooks
  and is deferred to a future phase.

Audit events:
  Policy violations are written as AgentEvent rows with:
    event_type = "policy_violation"
    input_data = {"policy_id": ..., "rule_type": ..., "severity": ...}
    output_data = {"reason": ...}
    permitted  = False
  This reuses the existing audit infrastructure with no schema changes.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, func, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent  import Agent, AgentEvent
from app.models.policy import Policy, AgentPolicy, SEVERITY_ORDER
from app.schemas.policy import (
    PolicyCreate,
    PolicyResponse,
    PolicyListResponse,
    AgentPolicyResponse,
    AgentPolicyListResponse,
    PolicyViolationDetail,
)

logger = logging.getLogger(__name__)

# Severity evaluation order — CRITICAL evaluated first.
_SEVERITY_PRIORITY = {s: i for i, s in enumerate(reversed(SEVERITY_ORDER))}


def _clamp_limit(limit: int, maximum: int = 200) -> int:
    return min(max(1, limit), maximum)


# ── Policy evaluation result ──────────────────────────────────────────────────

@dataclass
class PolicyEvaluationResult:
    """
    Output of evaluate_policies_for_agent().

    Consumed by execution_service.run_agent() before invoking the LLM.

    Fields:
      run_blocked:       True if a prompt_guard or time_window policy fired.
      violation:         Details of the blocking policy (if run_blocked).
      blocked_tools:     Tool names blocked by tool_deny policies.
      explicitly_allowed: Tool names explicitly permitted by tool_allow policies.
      rate_limit:        Max tool calls per run (None = no policy limit).
      active_policies:   All active policies evaluated (for logging).
    """
    run_blocked:         bool                          = False
    violation:           PolicyViolationDetail | None  = None
    blocked_tools:       set[str]                      = field(default_factory=set)
    explicitly_allowed:  set[str]                      = field(default_factory=set)
    rate_limit:          int | None                    = None
    active_policies:     list[Policy]                  = field(default_factory=list)


# ── Policy evaluation engine ──────────────────────────────────────────────────

async def evaluate_policies_for_agent(
    db: AsyncSession,
    agent_id: str,
    prompt: str,
) -> PolicyEvaluationResult:
    """
    Load and evaluate all active policies attached to the given agent.

    Called by execution_service.run_agent() before building the LangGraph
    agent.  If the result has run_blocked=True, the run must be aborted
    immediately (before any LLM call) with a policy_violation audit event.

    Args:
        db:       Async DB session.
        agent_id: UUID of the agent being run.
        prompt:   The user's prompt string (used by prompt_guard evaluation).

    Returns:
        PolicyEvaluationResult with all blocking and permitting decisions.
    """
    result = PolicyEvaluationResult()

    # ── Load active policies for this agent (single JOIN query) ──────
    rows = (
        await db.execute(
            select(Policy)
            .join(AgentPolicy, AgentPolicy.policy_id == Policy.id)
            .where(
                and_(
                    AgentPolicy.agent_id == agent_id,
                    Policy.is_active == True,  # noqa: E712
                )
            )
            .order_by(
                # Evaluate highest severity first so critical policies
                # are reported when multiple would fire simultaneously.
                desc(Policy.severity)
            )
        )
    ).scalars().all()

    result.active_policies = list(rows)

    if not rows:
        return result

    current_hour = datetime.now(tz=timezone.utc).hour
    prompt_lower = prompt.lower()

    for policy in rows:
        cfg = policy.rule_config or {}

        # ── prompt_guard ──────────────────────────────────────────────
        if policy.rule_type == "prompt_guard":
            keywords: list[str] = cfg.get("blocked_keywords", [])
            hit = next((kw for kw in keywords if kw.lower() in prompt_lower), None)
            if hit:
                reason = (
                    f"Policy '{policy.name}' blocked this run: "
                    f"prompt contains restricted keyword '{hit}'."
                )
                result.run_blocked = True
                result.violation   = PolicyViolationDetail(
                    policy_id=policy.id,
                    policy_name=policy.name,
                    rule_type=policy.rule_type,
                    severity=policy.severity,
                    reason=reason,
                )
                logger.warning(
                    "POLICY BLOCK [prompt_guard] agent=%s policy='%s' keyword='%s'",
                    agent_id[:8], policy.name, hit,
                )
                # Stop on first blocker — highest severity already evaluated first
                return result

        # ── time_window ───────────────────────────────────────────────
        elif policy.rule_type == "time_window":
            start: int = cfg.get("start_hour", 0)
            end:   int = cfg.get("end_hour",   24)
            if not (start <= current_hour < end):
                reason = (
                    f"Policy '{policy.name}' blocked this run: "
                    f"agent is only permitted between {start:02d}:00 and "
                    f"{end:02d}:00 UTC (current hour: {current_hour:02d}:00 UTC)."
                )
                result.run_blocked = True
                result.violation   = PolicyViolationDetail(
                    policy_id=policy.id,
                    policy_name=policy.name,
                    rule_type=policy.rule_type,
                    severity=policy.severity,
                    reason=reason,
                )
                logger.warning(
                    "POLICY BLOCK [time_window] agent=%s policy='%s' hour=%d outside [%d,%d)",
                    agent_id[:8], policy.name, current_hour, start, end,
                )
                return result

        # ── tool_deny ─────────────────────────────────────────────────
        elif policy.rule_type == "tool_deny":
            tool_name = cfg.get("tool", "")
            if tool_name:
                result.blocked_tools.add(tool_name)
                logger.debug(
                    "Policy '%s' → tool_deny: %s", policy.name, tool_name
                )

        # ── tool_allow ────────────────────────────────────────────────
        elif policy.rule_type == "tool_allow":
            tool_name = cfg.get("tool", "")
            if tool_name:
                result.explicitly_allowed.add(tool_name)
                logger.debug(
                    "Policy '%s' → tool_allow: %s", policy.name, tool_name
                )

        # ── rate_limit ────────────────────────────────────────────────
        elif policy.rule_type == "rate_limit":
            max_calls = cfg.get("max_calls_per_run")
            if isinstance(max_calls, int) and max_calls > 0:
                # Take the most restrictive limit across all rate_limit policies
                if result.rate_limit is None or max_calls < result.rate_limit:
                    result.rate_limit = max_calls
                    logger.debug(
                        "Policy '%s' → rate_limit: max %d calls/run",
                        policy.name, max_calls,
                    )

    return result


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def create_policy(
    payload: PolicyCreate,
    db: AsyncSession,
) -> PolicyResponse:
    """
    Validate and persist a new Policy.

    Steps:
      1. Cross-field validate rule_config shape for the given rule_type.
      2. Check name uniqueness → 409 if taken.
      3. Persist the Policy row.
      4. Return PolicyResponse.

    Raises:
        HTTPException 409: Policy name already exists.
        HTTPException 422: rule_config shape is invalid.
    """
    # Cross-field validation (rule_type + rule_config compatibility)
    try:
        payload.validate_rule_config_shape()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # Name uniqueness check
    existing = (
        await db.execute(select(Policy).where(Policy.name == payload.name))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A policy named '{payload.name}' already exists.",
        )

    policy = Policy(
        name=payload.name,
        description=payload.description,
        rule_type=payload.rule_type,
        rule_config=payload.rule_config,
        severity=payload.severity,
        is_active=payload.is_active,
    )
    db.add(policy)
    await db.flush()
    await db.refresh(policy)

    logger.info(
        "Policy created | id=%s name='%s' type=%s severity=%s",
        policy.id[:8], policy.name, policy.rule_type, policy.severity,
    )

    return PolicyResponse(
        id=policy.id,
        name=policy.name,
        description=policy.description,
        rule_type=policy.rule_type,
        rule_config=policy.rule_config,
        severity=policy.severity,
        is_active=policy.is_active,
        created_at=policy.created_at,
        agent_count=0,
    )


async def list_policies(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
) -> PolicyListResponse:
    """Return all policies with agent_count, newest first."""
    limit = _clamp_limit(limit)

    total: int = (
        await db.execute(select(func.count()).select_from(Policy))
    ).scalar_one()

    rows = (
        await db.execute(
            select(Policy)
            .order_by(desc(Policy.created_at))
            .offset(skip)
            .limit(limit)
        )
    ).scalars().all()

    # Count agents per policy in one GROUP BY query
    count_rows = (
        await db.execute(
            select(AgentPolicy.policy_id, func.count().label("n"))
            .group_by(AgentPolicy.policy_id)
        )
    ).all()
    agent_counts = {row.policy_id: row.n for row in count_rows}

    return PolicyListResponse(
        policies=[
            PolicyResponse(
                id=p.id,
                name=p.name,
                description=p.description,
                rule_type=p.rule_type,
                rule_config=p.rule_config,
                severity=p.severity,
                is_active=p.is_active,
                created_at=p.created_at,
                agent_count=agent_counts.get(p.id, 0),
            )
            for p in rows
        ],
        total=total,
        skip=skip,
        limit=limit,
    )


async def get_policy_by_id(
    db: AsyncSession,
    policy_id: str,
) -> PolicyResponse:
    """
    Fetch a single policy by UUID.

    Raises:
        HTTPException 404: Policy not found.
    """
    row = (
        await db.execute(select(Policy).where(Policy.id == policy_id))
    ).scalar_one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy '{policy_id}' not found.",
        )

    count: int = (
        await db.execute(
            select(func.count()).select_from(AgentPolicy)
            .where(AgentPolicy.policy_id == policy_id)
        )
    ).scalar_one()

    return PolicyResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        rule_type=row.rule_type,
        rule_config=row.rule_config,
        severity=row.severity,
        is_active=row.is_active,
        created_at=row.created_at,
        agent_count=count,
    )


# ── Agent↔Policy assignment ───────────────────────────────────────────────────

async def assign_policy_to_agent(
    db: AsyncSession,
    policy_id: str,
    agent_id: str,
) -> AgentPolicyResponse:
    """
    Attach a policy to an agent.

    Raises:
        HTTPException 404: Policy or agent not found.
        HTTPException 409: Assignment already exists.
    """
    # Validate policy exists
    policy = (
        await db.execute(select(Policy).where(Policy.id == policy_id))
    ).scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=404, detail=f"Policy '{policy_id}' not found.")

    # Validate agent exists
    agent = (
        await db.execute(select(Agent).where(Agent.id == agent_id))
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")

    # Check for duplicate
    existing = (
        await db.execute(
            select(AgentPolicy).where(
                and_(
                    AgentPolicy.agent_id == agent_id,
                    AgentPolicy.policy_id == policy_id,
                )
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Policy '{policy.name}' is already assigned to agent '{agent.name}'.",
        )

    ap = AgentPolicy(agent_id=agent_id, policy_id=policy_id)
    db.add(ap)
    await db.flush()
    await db.refresh(ap)

    logger.info(
        "Policy assigned | policy='%s' agent='%s'", policy.name, agent.name
    )

    return AgentPolicyResponse(
        id=ap.id,
        agent_id=ap.agent_id,
        policy_id=ap.policy_id,
        created_at=ap.created_at,
        agent_name=agent.name,
        policy_name=policy.name,
        rule_type=policy.rule_type,
        severity=policy.severity,
    )


async def remove_policy_from_agent(
    db: AsyncSession,
    policy_id: str,
    agent_id: str,
) -> None:
    """
    Detach a policy from an agent.

    Raises:
        HTTPException 404: Assignment not found.
    """
    ap = (
        await db.execute(
            select(AgentPolicy).where(
                and_(
                    AgentPolicy.agent_id == agent_id,
                    AgentPolicy.policy_id == policy_id,
                )
            )
        )
    ).scalar_one_or_none()

    if ap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No assignment found for policy '{policy_id}' and agent '{agent_id}'.",
        )

    await db.delete(ap)
    await db.flush()
    logger.info("Policy detached | policy=%s agent=%s", policy_id[:8], agent_id[:8])


async def list_policies_for_agent(
    db: AsyncSession,
    agent_id: str,
) -> AgentPolicyListResponse:
    """
    Return all active policies assigned to a specific agent.

    Raises:
        HTTPException 404: Agent not found.
    """
    agent = (
        await db.execute(select(Agent).where(Agent.id == agent_id))
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")

    rows = (
        await db.execute(
            select(Policy)
            .join(AgentPolicy, AgentPolicy.policy_id == Policy.id)
            .where(AgentPolicy.agent_id == agent_id)
            .order_by(desc(Policy.created_at))
        )
    ).scalars().all()

    return AgentPolicyListResponse(
        policies=[
            PolicyResponse(
                id=p.id,
                name=p.name,
                description=p.description,
                rule_type=p.rule_type,
                rule_config=p.rule_config,
                severity=p.severity,
                is_active=p.is_active,
                created_at=p.created_at,
                agent_count=0,  # not needed in this context
            )
            for p in rows
        ],
        agent_id=agent_id,
        total=len(rows),
    )


# ── Analytics ─────────────────────────────────────────────────────────────────

async def get_policy_counts(db: AsyncSession) -> dict:
    """
    Return policy aggregate counts for GET /analytics/stats.

    Returns:
        {
          "total_policies":         int,
          "active_policies":        int,
          "total_policy_violations": int,
          "violations_by_severity": {severity: count},
        }
    """
    total_policies: int = (
        await db.execute(select(func.count()).select_from(Policy))
    ).scalar_one()

    active_policies: int = (
        await db.execute(
            select(func.count()).select_from(Policy).where(Policy.is_active == True)  # noqa: E712
        )
    ).scalar_one()

    # Policy violations are stored as AgentEvent rows with
    # event_type="policy_violation".  Count them from the existing table.
    total_policy_violations: int = (
        await db.execute(
            select(func.count()).select_from(AgentEvent)
            .where(AgentEvent.event_type == "policy_violation")
        )
    ).scalar_one()

    # Group violations by severity stored in input_data JSON.
    # SQLite doesn't support JSON_EXTRACT in SQLAlchemy without the
    # JSON function extension, so we load the rows and count in Python.
    # With expected counts in the hundreds this is fast.
    violation_rows = (
        await db.execute(
            select(AgentEvent.input_data)
            .where(AgentEvent.event_type == "policy_violation")
        )
    ).scalars().all()

    violations_by_severity: dict[str, int] = {}
    for row in violation_rows:
        if isinstance(row, dict):
            sev = row.get("severity", "UNKNOWN")
            violations_by_severity[sev] = violations_by_severity.get(sev, 0) + 1

    return {
        "total_policies":          total_policies,
        "active_policies":         active_policies,
        "total_policy_violations": total_policy_violations,
        "violations_by_severity":  violations_by_severity,
    }