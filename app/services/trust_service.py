"""
app/services/trust_service.py
───────────────────────────────
Trust Score system for AgentWatch.

Evaluates each registered agent's historical behaviour and produces a
0–100 score that reflects how trustworthy the agent has been.  The score
is fully explainable: every contributing factor is returned in a breakdown
dict so the caller can understand exactly why an agent scored as it did.

─────────────────────────────
Trust Score Formula
─────────────────────────────
Start:  100 points

Deductions:
  -5   per governance violation  (event_type='violation')
  -8   per policy violation      (event_type='policy_violation')
  -2   per failed run            (status='failed')
  -5   additional per HIGH-severity policy violation
  -10  additional per CRITICAL-severity policy violation

Additions:
  +0.2 per completed run         (status='completed')
  +0.1 per permitted tool call   (event_type='tool_end', permitted=True)
  +0.1 per positive interaction  (source or target in agent_interactions)

Clamp: min 0, max 100, rounded to 1 decimal place.

─────────────────────────────
Trust Levels
─────────────────────────────
  90–100 → TRUSTED
  70–89  → MONITORED
  50–69  → WARNING
   0–49  → HIGH_RISK

─────────────────────────────
Design notes
─────────────────────────────
• All queries follow the same async SQLAlchemy 2.0 pattern as
  audit_service.py and policy_service.py.
• Policy violation severity is read from the input_data JSON column
  of the AgentEvent row (same approach as policy_service.get_policy_counts).
• The service is read-only — it never writes to the database.
• calculate_system_trust_score() loads all agent IDs and calls
  calculate_agent_trust_score() for each.  With O(agents) queries this
  is acceptable for expected agent counts (< 1000).  For production at
  scale, pre-compute and cache scores in a separate table.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent  import Agent, AgentRun, AgentEvent, AgentInteraction

logger = logging.getLogger(__name__)

# ── Trust level thresholds ────────────────────────────────────────────────────

TRUST_LEVELS = (
    (90, "TRUSTED"),
    (70, "MONITORED"),
    (50, "WARNING"),
    ( 0, "HIGH_RISK"),
)


def score_to_level(score: float) -> str:
    """Map a numeric trust score to its trust level label."""
    for threshold, label in TRUST_LEVELS:
        if score >= threshold:
            return label
    return "HIGH_RISK"


# ── Score data container ──────────────────────────────────────────────────────

@dataclass
class TrustCalculation:
    """
    Intermediate working values for a single agent's trust score.

    All raw counts are gathered from the database first, then the
    formula is applied in one deterministic pass.
    """
    # Raw counts from DB
    completed_runs:           int   = 0
    failed_runs:              int   = 0
    governance_violations:    int   = 0
    policy_violations:        int   = 0
    high_severity_violations: int   = 0
    critical_violations:      int   = 0
    permitted_tool_calls:     int   = 0
    interactions:             int   = 0

    # Computed score components (populated by _apply_formula)
    base_score:              float = 100.0
    deduction_gov:           float = 0.0
    deduction_policy:        float = 0.0
    deduction_failed:        float = 0.0
    deduction_high_sev:      float = 0.0
    deduction_critical:      float = 0.0
    addition_completed:      float = 0.0
    addition_tools:          float = 0.0
    addition_interactions:   float = 0.0
    final_score:             float = 100.0
    trust_level:             str   = "TRUSTED"

    def apply_formula(self) -> None:
        """
        Apply the trust score formula and populate all computed fields.

        Called after all DB queries have populated the raw count fields.
        Modifies self in-place.
        """
        self.deduction_gov      = self.governance_violations    * 5.0
        self.deduction_policy   = self.policy_violations        * 8.0
        self.deduction_failed   = self.failed_runs              * 2.0
        self.deduction_high_sev = self.high_severity_violations * 5.0
        self.deduction_critical = self.critical_violations      * 10.0

        self.addition_completed    = self.completed_runs       * 0.2
        self.addition_tools        = self.permitted_tool_calls * 0.1
        self.addition_interactions = self.interactions         * 0.1

        raw = (
            self.base_score
            - self.deduction_gov
            - self.deduction_policy
            - self.deduction_failed
            - self.deduction_high_sev
            - self.deduction_critical
            + self.addition_completed
            + self.addition_tools
            + self.addition_interactions
        )
        self.final_score = round(max(0.0, min(100.0, raw)), 1)
        self.trust_level = score_to_level(self.final_score)

    def to_breakdown(self) -> dict:
        """
        Return a human-readable breakdown of how the score was computed.

        Every factor is listed so the caller can explain the score to a user.
        """
        return {
            "base_score":                  self.base_score,
            # Raw counts
            "governance_violations":       self.governance_violations,
            "policy_violations":           self.policy_violations,
            "high_severity_violations":    self.high_severity_violations,
            "critical_violations":         self.critical_violations,
            "failed_runs":                 self.failed_runs,
            "completed_runs":              self.completed_runs,
            "permitted_tool_calls":        self.permitted_tool_calls,
            "interactions":                self.interactions,
            # Point contributions
            "deductions": {
                "governance_violations":   -self.deduction_gov,
                "policy_violations":       -self.deduction_policy,
                "failed_runs":             -self.deduction_failed,
                "high_severity_policy":    -self.deduction_high_sev,
                "critical_policy":         -self.deduction_critical,
            },
            "additions": {
                "completed_runs":          self.addition_completed,
                "permitted_tool_calls":    self.addition_tools,
                "interactions":            self.addition_interactions,
            },
            "total_deductions": -(
                self.deduction_gov
                + self.deduction_policy
                + self.deduction_failed
                + self.deduction_high_sev
                + self.deduction_critical
            ),
            "total_additions": (
                self.addition_completed
                + self.addition_tools
                + self.addition_interactions
            ),
            "final_score": self.final_score,
            "trust_level": self.trust_level,
        }


# ── Core calculation ──────────────────────────────────────────────────────────

async def calculate_agent_trust_score(
    db: AsyncSession,
    agent_id: str,
) -> TrustCalculation:
    """
    Gather all raw counts for one agent and apply the trust formula.

    Executes 5 async queries:
      1. Run counts by status (completed, failed).
      2. Governance violation count (event_type='violation').
      3. Policy violation count + severity breakdown from input_data JSON.
      4. Permitted tool call count (event_type='tool_end', permitted=True).
      5. Interaction count (source OR target).

    Args:
        db:       Async SQLAlchemy session.
        agent_id: UUID of the agent to score.

    Returns:
        TrustCalculation with final_score and trust_level populated.
        Does NOT raise if agent does not exist — returns a default score.
    """
    calc = TrustCalculation()

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
    calc.failed_runs    = run_counts.get("failed", 0)

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
    # Policy violation severity is stored in AgentEvent.input_data as
    # {"policy_id": ..., "severity": "HIGH", ...}.
    # We load the input_data column for all policy_violation events and
    # count severity values in Python (same strategy as policy_service).
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

    # ── 4. Permitted tool calls ────────────────────────────────────────
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

    # ── 5. Interactions (source or target) ────────────────────────────
    calc.interactions = (
        await db.execute(
            select(func.count())
            .select_from(AgentInteraction)
            .where(
                or_(
                    AgentInteraction.source_agent_id == agent_id,
                    AgentInteraction.target_agent_id == agent_id,
                )
            )
        )
    ).scalar_one()

    calc.apply_formula()

    logger.debug(
        "Trust score | agent=%s score=%.1f level=%s",
        agent_id[:8], calc.final_score, calc.trust_level,
    )
    return calc


# ── Agent trust response builder ──────────────────────────────────────────────

async def get_agent_trust_breakdown(
    db: AsyncSession,
    agent_id: str,
) -> dict:
    """
    Return a full trust breakdown for one agent, including agent name.

    Args:
        db:       Async DB session.
        agent_id: UUID of the agent.

    Returns:
        Dict with agent_id, agent_name, trust_score, trust_level,
        and a detailed breakdown dict.

    Raises:
        ValueError: Agent not found.
    """
    agent_row = (
        await db.execute(select(Agent).where(Agent.id == agent_id))
    ).scalar_one_or_none()

    if agent_row is None:
        raise ValueError(f"Agent '{agent_id}' not found.")

    calc = await calculate_agent_trust_score(db, agent_id)

    return {
        "agent_id":    agent_id,
        "agent_name":  agent_row.name,
        "trust_score": calc.final_score,
        "trust_level": calc.trust_level,
        "breakdown":   calc.to_breakdown(),
    }


# ── System-wide trust aggregates ──────────────────────────────────────────────

async def calculate_system_trust_score(db: AsyncSession) -> dict:
    """
    Compute average trust score and trust level distribution across all agents.

    Loads all agent IDs, scores each one, and aggregates.

    Returns:
        {
          "average_trust_score": float,
          "trust_distribution": {
              "TRUSTED":   int,
              "MONITORED": int,
              "WARNING":   int,
              "HIGH_RISK": int,
          }
        }
    """
    agent_ids = (
        await db.execute(select(Agent.id))
    ).scalars().all()

    distribution: dict[str, int] = {
        "TRUSTED":   0,
        "MONITORED": 0,
        "WARNING":   0,
        "HIGH_RISK": 0,
    }

    if not agent_ids:
        return {
            "average_trust_score": 0.0,
            "trust_distribution":  distribution,
        }

    scores: list[float] = []
    for agent_id in agent_ids:
        calc = await calculate_agent_trust_score(db, agent_id)
        scores.append(calc.final_score)
        level = calc.trust_level
        if level in distribution:
            distribution[level] += 1
        else:
            distribution["HIGH_RISK"] += 1

    average = round(sum(scores) / len(scores), 1) if scores else 0.0

    return {
        "average_trust_score": average,
        "trust_distribution":  distribution,
    }


async def get_trust_distribution(db: AsyncSession) -> dict:
    """
    Convenience wrapper — returns the full system trust payload.

    Called directly by GET /analytics/trust.
    """
    return await calculate_system_trust_score(db)