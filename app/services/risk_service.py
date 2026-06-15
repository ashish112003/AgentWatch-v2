"""
app/services/risk_service.py
──────────────────────────────
Risk Score system for AgentWatch.

While Trust Score measures an agent's historical trustworthiness (high
Trust = reliably well-behaved over its lifetime), Risk Score measures its
current operational danger level (high Risk = behaving dangerously right
now, regardless of past record).

An agent can have Trust Score = 95 and Risk Score = 80 because it was
historically excellent but is currently triggering many violations.

──────────────────────────────
Risk Score Formula
──────────────────────────────
Start:  0  (safe by default)

Add risk for:
  +15  per governance violation    (event_type='violation')
  +20  per policy violation        (event_type='policy_violation')
  +10  additional per HIGH-severity policy violation
  +20  additional per CRITICAL-severity policy violation
  +5   per failed run              (status='failed')
  +10  per denied tool call        (event_type='tool_call', permitted=False)
  +2   per handoff interaction     (interaction_type='handoff')
  +4   per delegation interaction  (interaction_type='delegation')
  +8   per escalation interaction  (interaction_type='escalation')
      [escalation not a current valid type, score 0 if none found]

Reduce risk for:
  -0.1 per completed run           (status='completed')
  -0.05 per permitted tool call    (event_type='tool_end', permitted=True)

Clamp: min 0, max 100, rounded to 1 decimal place.

──────────────────────────────
Risk Levels
──────────────────────────────
  0–24    → SAFE
  25–49   → LOW
  50–74   → MEDIUM
  75–89   → HIGH
  90–100  → CRITICAL

──────────────────────────────
Design notes
──────────────────────────────
• All queries follow the same async SQLAlchemy 2.0 pattern as
  trust_service.py, audit_service.py, and policy_service.py.
• Policy violation severity is read from AgentEvent.input_data JSON
  (same approach as trust_service.py and policy_service.get_policy_counts).
• Interaction type counts are loaded from AgentInteraction directly and
  classified per the spec weight table.
• 'escalation' is included in the spec but is not a current VALID_INTERACTION_TYPES
  value.  It scores 0 on existing data; future phases can add it.
• The service is read-only — it never writes to the database.
• calculate_system_risk_score() scores every agent individually.
  For production scale, pre-compute and cache in a separate table.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentRun, AgentEvent, AgentInteraction

logger = logging.getLogger(__name__)

# ── Risk level thresholds ─────────────────────────────────────────────────────
# Listed from highest threshold to lowest so the first match wins.

RISK_LEVELS = (
    (90, "CRITICAL"),
    (75, "HIGH"),
    (50, "MEDIUM"),
    (25, "LOW"),
    ( 0, "SAFE"),
)


def score_to_risk_level(score: float) -> str:
    """Map a numeric risk score to its risk level label."""
    for threshold, label in RISK_LEVELS:
        if score >= threshold:
            return label
    return "SAFE"


# ── Score data container ──────────────────────────────────────────────────────

@dataclass
class RiskCalculation:
    """
    Intermediate working values for a single agent's risk score.

    All raw counts are gathered from the database first, then the
    formula is applied in one deterministic pass via apply_formula().
    """
    # ── Raw counts from DB ────────────────────────────────────────────
    governance_violations:     int   = 0
    policy_violations:         int   = 0
    high_severity_violations:  int   = 0
    critical_violations:       int   = 0
    failed_runs:               int   = 0
    denied_tool_calls:         int   = 0

    # Interaction counts broken out by type (risk weight differs per type)
    handoff_interactions:     int   = 0
    delegation_interactions:  int   = 0
    escalation_interactions:  int   = 0

    completed_runs:           int   = 0
    permitted_tool_calls:     int   = 0

    # ── Computed score components (populated by apply_formula) ─────────
    base_score:                float = 0.0
    addition_gov:              float = 0.0
    addition_policy:           float = 0.0
    addition_high_sev:         float = 0.0
    addition_critical:         float = 0.0
    addition_failed:           float = 0.0
    addition_denied:           float = 0.0
    addition_handoff:          float = 0.0
    addition_delegation:       float = 0.0
    addition_escalation:       float = 0.0
    reduction_completed:       float = 0.0
    reduction_permitted:       float = 0.0
    final_score:               float = 0.0
    risk_level:                str   = "SAFE"

    def apply_formula(self) -> None:
        """
        Apply the risk score formula and populate all computed fields.

        Called after all DB queries have populated the raw count fields.
        Modifies self in-place.
        """
        # ── Risk additions ────────────────────────────────────────────
        self.addition_gov        = self.governance_violations    * 15.0
        self.addition_policy     = self.policy_violations        * 20.0
        self.addition_high_sev   = self.high_severity_violations * 10.0
        self.addition_critical   = self.critical_violations      * 20.0
        self.addition_failed     = self.failed_runs              * 5.0
        self.addition_denied     = self.denied_tool_calls        * 10.0
        self.addition_handoff    = self.handoff_interactions     * 2.0
        self.addition_delegation = self.delegation_interactions  * 4.0
        self.addition_escalation = self.escalation_interactions  * 8.0

        # ── Risk reductions ───────────────────────────────────────────
        self.reduction_completed = self.completed_runs       * 0.1
        self.reduction_permitted = self.permitted_tool_calls * 0.05

        raw = (
            self.base_score
            + self.addition_gov
            + self.addition_policy
            + self.addition_high_sev
            + self.addition_critical
            + self.addition_failed
            + self.addition_denied
            + self.addition_handoff
            + self.addition_delegation
            + self.addition_escalation
            - self.reduction_completed
            - self.reduction_permitted
        )

        self.final_score = round(max(0.0, min(100.0, raw)), 1)
        self.risk_level  = score_to_risk_level(self.final_score)

    def to_breakdown(self) -> dict:
        """
        Return a fully explainable breakdown of how the risk score was computed.

        Every contributing factor is listed with its raw count and point
        contribution so callers can explain the score to a user or dashboard.
        """
        return {
            "base_score": self.base_score,

            # ── Raw counts ─────────────────────────────────────────
            "governance_violations":      self.governance_violations,
            "policy_violations":          self.policy_violations,
            "high_severity_violations":   self.high_severity_violations,
            "critical_severity_violations": self.critical_violations,
            "failed_runs":                self.failed_runs,
            "denied_tool_calls":          self.denied_tool_calls,
            "interactions": {
                "handoff":    self.handoff_interactions,
                "delegation": self.delegation_interactions,
                "escalation": self.escalation_interactions,
            },
            "completed_runs":       self.completed_runs,
            "permitted_tool_calls": self.permitted_tool_calls,

            # ── Point contributions ────────────────────────────────
            "additions": {
                "governance_violations":    self.addition_gov,
                "policy_violations":        self.addition_policy,
                "high_severity_policy":     self.addition_high_sev,
                "critical_policy":          self.addition_critical,
                "failed_runs":              self.addition_failed,
                "denied_tool_calls":        self.addition_denied,
                "handoff_interactions":     self.addition_handoff,
                "delegation_interactions":  self.addition_delegation,
                "escalation_interactions":  self.addition_escalation,
            },
            "reductions": {
                "completed_runs":       -self.reduction_completed,
                "permitted_tool_calls": -self.reduction_permitted,
            },
            "total_additions": (
                self.addition_gov
                + self.addition_policy
                + self.addition_high_sev
                + self.addition_critical
                + self.addition_failed
                + self.addition_denied
                + self.addition_handoff
                + self.addition_delegation
                + self.addition_escalation
            ),
            "total_reductions": -(self.reduction_completed + self.reduction_permitted),
            "final_score": self.final_score,
            "risk_level":  self.risk_level,
        }


# ── Core calculation ──────────────────────────────────────────────────────────

async def calculate_agent_risk_score(
    db: AsyncSession,
    agent_id: str,
) -> RiskCalculation:
    """
    Gather all raw counts for one agent and apply the risk formula.

    Executes 6 async queries:
      1. Run counts by status (completed, failed).
      2. Governance violation count (event_type='violation').
      3. Policy violation count + severity breakdown from input_data JSON.
      4. Denied tool call count (event_type='tool_call', permitted=False).
      5. Permitted tool call count (event_type='tool_end', permitted=True).
      6. Interaction counts by type (handoff, delegation, escalation).

    Args:
        db:       Async SQLAlchemy session.
        agent_id: UUID of the agent to score.

    Returns:
        RiskCalculation with final_score and risk_level populated.
        Does NOT raise if agent does not exist — returns a default score.
    """
    calc = RiskCalculation()

    # ── 1. Run counts ──────────────────────────────────────────────────
    run_rows = (
        await db.execute(
            select(AgentRun.status, func.count().label("n"))
            .where(AgentRun.agent_id == agent_id)
            .group_by(AgentRun.status)
        )
    ).all()
    run_counts          = {row.status: row.n for row in run_rows}
    calc.completed_runs = run_counts.get("completed", 0)
    calc.failed_runs    = run_counts.get("failed",    0)

    # ── 2. Governance violations ───────────────────────────────────────
    calc.governance_violations = (
        await db.execute(
            select(func.count())
            .select_from(AgentEvent)
            .where(
                and_(
                    AgentEvent.agent_id   == agent_id,
                    AgentEvent.event_type == "violation",
                )
            )
        )
    ).scalar_one()

    # ── 3. Policy violations + severity breakdown ──────────────────────
    # Severity is stored in AgentEvent.input_data as {"severity": "HIGH", ...}.
    # We load the column and count in Python (same strategy as trust_service).
    policy_viol_rows = (
        await db.execute(
            select(AgentEvent.input_data)
            .where(
                and_(
                    AgentEvent.agent_id   == agent_id,
                    AgentEvent.event_type == "policy_violation",
                )
            )
        )
    ).scalars().all()

    calc.policy_violations = len(policy_viol_rows)
    for row in policy_viol_rows:
        if isinstance(row, dict):
            sev = row.get("severity", "")
            if sev == "HIGH":
                calc.high_severity_violations += 1
            elif sev == "CRITICAL":
                calc.critical_violations += 1

    # ── 4. Denied tool calls ───────────────────────────────────────────
    # A denied call is a tool_call event with permitted=False.
    # This includes both governance-blocked and policy-blocked calls.
    calc.denied_tool_calls = (
        await db.execute(
            select(func.count())
            .select_from(AgentEvent)
            .where(
                and_(
                    AgentEvent.agent_id   == agent_id,
                    AgentEvent.event_type == "tool_call",
                    AgentEvent.permitted  == False,  # noqa: E712
                )
            )
        )
    ).scalar_one()

    # ── 5. Permitted tool calls ────────────────────────────────────────
    calc.permitted_tool_calls = (
        await db.execute(
            select(func.count())
            .select_from(AgentEvent)
            .where(
                and_(
                    AgentEvent.agent_id   == agent_id,
                    AgentEvent.event_type == "tool_end",
                    AgentEvent.permitted  == True,  # noqa: E712
                )
            )
        )
    ).scalar_one()

    # ── 6. Interaction counts by type ──────────────────────────────────
    # An agent's interactions are those where it is source OR target.
    # Risk weight varies by type: handoff(+2), delegation(+4), escalation(+8).
    interaction_rows = (
        await db.execute(
            select(AgentInteraction.interaction_type, func.count().label("n"))
            .where(
                or_(
                    AgentInteraction.source_agent_id == agent_id,
                    AgentInteraction.target_agent_id == agent_id,
                )
            )
            .group_by(AgentInteraction.interaction_type)
        )
    ).all()

    type_counts = {row.interaction_type: row.n for row in interaction_rows}
    calc.handoff_interactions    = type_counts.get("handoff",    0)
    calc.delegation_interactions = type_counts.get("delegation", 0)
    calc.escalation_interactions = type_counts.get("escalation", 0)

    calc.apply_formula()

    logger.debug(
        "Risk score | agent=%s score=%.1f level=%s",
        agent_id[:8], calc.final_score, calc.risk_level,
    )
    return calc


# ── Agent risk response builder ───────────────────────────────────────────────

async def get_agent_risk_breakdown(
    db: AsyncSession,
    agent_id: str,
) -> dict:
    """
    Return a full risk breakdown for one agent, including agent name.

    Args:
        db:       Async DB session.
        agent_id: UUID of the agent.

    Returns:
        Dict with agent_id, agent_name, risk_score, risk_level,
        and a detailed breakdown dict.

    Raises:
        ValueError: Agent not found.
    """
    agent_row = (
        await db.execute(select(Agent).where(Agent.id == agent_id))
    ).scalar_one_or_none()

    if agent_row is None:
        raise ValueError(f"Agent '{agent_id}' not found.")

    calc = await calculate_agent_risk_score(db, agent_id)

    return {
        "agent_id":   agent_id,
        "agent_name": agent_row.name,
        "risk_score": calc.final_score,
        "risk_level": calc.risk_level,
        "breakdown":  calc.to_breakdown(),
    }


# ── System-wide risk aggregates ───────────────────────────────────────────────

async def calculate_system_risk_score(db: AsyncSession) -> dict:
    """
    Compute average risk score and risk level distribution across all agents.

    Loads all agent IDs, scores each one, and aggregates.

    Returns:
        {
          "average_risk_score": float,
          "risk_distribution": {
              "SAFE":     int,
              "LOW":      int,
              "MEDIUM":   int,
              "HIGH":     int,
              "CRITICAL": int,
          }
        }
    """
    agent_ids = (
        await db.execute(select(Agent.id))
    ).scalars().all()

    distribution: dict[str, int] = {
        "SAFE":     0,
        "LOW":      0,
        "MEDIUM":   0,
        "HIGH":     0,
        "CRITICAL": 0,
    }

    if not agent_ids:
        return {
            "average_risk_score": 0.0,
            "risk_distribution":  distribution,
        }

    scores: list[float] = []
    for agent_id in agent_ids:
        calc = await calculate_agent_risk_score(db, agent_id)
        scores.append(calc.final_score)
        level = calc.risk_level
        if level in distribution:
            distribution[level] += 1
        else:
            distribution["SAFE"] += 1

    average = round(sum(scores) / len(scores), 1) if scores else 0.0

    return {
        "average_risk_score": average,
        "risk_distribution":  distribution,
    }


async def get_risk_distribution(db: AsyncSession) -> dict:
    """
    Convenience wrapper — returns the full system risk payload.

    Called directly by GET /analytics/risk.
    """
    return await calculate_system_risk_score(db)