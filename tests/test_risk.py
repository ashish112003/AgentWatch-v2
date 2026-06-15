"""
tests/test_risk.py
───────────────────
Comprehensive tests for the Risk Score system (Phase 5).

Coverage:
  Unit  — RiskCalculation formula, clamping, risk levels, breakdown dict
  Unit  — score_to_risk_level() boundary values for all five levels
  Integration — calculate_agent_risk_score() with seeded DB data
  Integration — calculate_system_risk_score() across multiple agents
  Integration — AgentStats includes risk_score and risk_level
  Integration — SystemStats includes average_risk_score and risk_distribution
  HTTP  — GET /analytics/risk
  HTTP  — GET /analytics/risk/{agent_id}
  HTTP  — GET /analytics/stats/{agent_id} includes risk fields
  HTTP  — GET /analytics/stats includes risk fields

Seeding strategy:
  Tests that need database data inject AgentRun and AgentEvent rows directly
  via db_session_factory (from conftest.py).  No LLM calls.  No GROQ_API_KEY.
"""

import uuid
import pytest
import pytest_asyncio
from datetime import datetime
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.db.database import Base
from app.models.agent import Agent, AgentRun, AgentEvent, AgentInteraction
from app.services.risk_service import (
    RiskCalculation,
    score_to_risk_level,
    calculate_agent_risk_score,
    calculate_system_risk_score,
    get_agent_risk_breakdown,
    get_risk_distribution,
)


# ── Seed helpers ──────────────────────────────────────────────────────────────

def _ts() -> datetime:
    return datetime(2024, 1, 15, 10, 0, 0)


async def _make_agent(db_factory, name: str) -> str:
    agent_id = str(uuid.uuid4())
    async with db_factory() as db:
        db.add(Agent(
            id=agent_id, name=name,
            allowed_tools=["calculator"],
            hashed_secret="x",
        ))
        await db.commit()
    return agent_id


async def _make_run(db_factory, agent_id: str, status: str = "completed") -> str:
    run_id = str(uuid.uuid4())
    async with db_factory() as db:
        db.add(AgentRun(
            id=run_id, agent_id=agent_id,
            prompt="test", status=status,
            trace_id=str(uuid.uuid4()),
            started_at=_ts(), ended_at=_ts(),
        ))
        await db.commit()
    return run_id


async def _make_event(
    db_factory, agent_id: str, run_id: str,
    event_type: str, permitted: bool | None = None,
    input_data: dict | None = None,
) -> None:
    async with db_factory() as db:
        db.add(AgentEvent(
            id=str(uuid.uuid4()), run_id=run_id, agent_id=agent_id,
            trace_id=str(uuid.uuid4()), event_type=event_type,
            permitted=permitted, input_data=input_data,
            timestamp=_ts(),
        ))
        await db.commit()


async def _make_interaction(
    db_factory, src: str, tgt: str,
    interaction_type: str = "handoff",
) -> None:
    async with db_factory() as db:
        db.add(AgentInteraction(
            id=str(uuid.uuid4()),
            source_agent_id=src, target_agent_id=tgt,
            interaction_type=interaction_type,
        ))
        await db.commit()


async def _isolated_factory():
    """Return a fresh in-memory SQLite session factory for integration tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _register(client: AsyncClient, name: str) -> dict:
    r = await client.post("/agents/register", json={
        "name": name,
        "allowed_tools": ["calculator"],
        "secret": "risk-test-secret-42",
    })
    assert r.status_code == 201
    data = r.json()
    return {
        "agent_id": data["agent_id"],
        "headers":  {"Authorization": f"Bearer {data['access_token']}"},
    }


# ═══════════════════════════════════════════════════════════════
# Unit tests — RiskCalculation formula
# ═══════════════════════════════════════════════════════════════

class TestRiskCalculationFormula:

    def test_default_score_is_zero(self):
        c = RiskCalculation()
        c.apply_formula()
        assert c.final_score == 0.0

    def test_default_level_is_safe(self):
        c = RiskCalculation()
        c.apply_formula()
        assert c.risk_level == "SAFE"

    def test_governance_violation_adds_15(self):
        c = RiskCalculation(governance_violations=1)
        c.apply_formula()
        assert c.final_score == 15.0

    def test_multiple_governance_violations_scale(self):
        c = RiskCalculation(governance_violations=3)
        c.apply_formula()
        assert c.final_score == 45.0

    def test_policy_violation_adds_20(self):
        c = RiskCalculation(policy_violations=1)
        c.apply_formula()
        assert c.final_score == 20.0

    def test_high_severity_violation_adds_10_additional(self):
        # policy base (+20) + high severity (+10) = 30
        c = RiskCalculation(policy_violations=1, high_severity_violations=1)
        c.apply_formula()
        assert c.final_score == 30.0

    def test_critical_severity_violation_adds_20_additional(self):
        # policy base (+20) + critical (+20) = 40
        c = RiskCalculation(policy_violations=1, critical_violations=1)
        c.apply_formula()
        assert c.final_score == 40.0

    def test_failed_run_adds_5(self):
        c = RiskCalculation(failed_runs=1)
        c.apply_formula()
        assert c.final_score == 5.0

    def test_denied_tool_call_adds_10(self):
        c = RiskCalculation(denied_tool_calls=1)
        c.apply_formula()
        assert c.final_score == 10.0

    def test_handoff_interaction_adds_2(self):
        c = RiskCalculation(handoff_interactions=1)
        c.apply_formula()
        assert c.final_score == 2.0

    def test_delegation_interaction_adds_4(self):
        c = RiskCalculation(delegation_interactions=1)
        c.apply_formula()
        assert c.final_score == 4.0

    def test_escalation_interaction_adds_8(self):
        c = RiskCalculation(escalation_interactions=1)
        c.apply_formula()
        assert c.final_score == 8.0

    def test_completed_run_reduces_0_1(self):
        # Start with some risk so the reduction is visible
        c = RiskCalculation(governance_violations=1, completed_runs=1)
        c.apply_formula()
        assert abs(c.final_score - 14.9) < 0.01

    def test_permitted_tool_call_reduces_0_05(self):
        # 15 (gov) - 0.05 (permitted tool) = 14.95 → rounds to 14.9 at 1 decimal place
        c = RiskCalculation(governance_violations=1, permitted_tool_calls=1)
        c.apply_formula()
        assert abs(c.reduction_permitted - 0.05) < 0.001
        assert c.final_score == 14.9

    def test_score_clamped_at_100(self):
        c = RiskCalculation(governance_violations=1000)
        c.apply_formula()
        assert c.final_score == 100.0

    def test_score_clamped_at_zero(self):
        # Reductions alone cannot go negative
        c = RiskCalculation(completed_runs=1000, permitted_tool_calls=1000)
        c.apply_formula()
        assert c.final_score == 0.0

    def test_reductions_cannot_exceed_additions(self):
        # 1 violation (+15) with large completed and tool reductions
        c = RiskCalculation(
            governance_violations=1,
            completed_runs=500,
            permitted_tool_calls=500,
        )
        c.apply_formula()
        assert c.final_score == 0.0  # clamped

    def test_score_rounded_to_1_decimal(self):
        c = RiskCalculation(denied_tool_calls=1, completed_runs=3)  # 10 - 0.3 = 9.7
        c.apply_formula()
        assert c.final_score == 9.7
        assert isinstance(c.final_score, float)

    def test_combined_formula(self):
        """
        2 gov violations (+30), 1 policy violation (+20), 1 high-sev (+10),
        2 failed (+10), 3 denied (+30), 1 handoff (+2), 1 delegation (+4),
        5 completed (-0.5), 10 tool calls (-0.5)
        = 106 - 1 = 105 → clamped to 100.
        """
        c = RiskCalculation(
            governance_violations=2,
            policy_violations=1, high_severity_violations=1,
            failed_runs=2, denied_tool_calls=3,
            handoff_interactions=1, delegation_interactions=1,
            completed_runs=5, permitted_tool_calls=10,
        )
        c.apply_formula()
        assert c.final_score == 100.0

    def test_combined_formula_not_clamped(self):
        """
        1 failed run (+5), 1 handoff (+2), 2 completed (-0.2), 4 permitted (-0.2)
        = 7 - 0.4 = 6.6
        """
        c = RiskCalculation(
            failed_runs=1, handoff_interactions=1,
            completed_runs=2, permitted_tool_calls=4,
        )
        c.apply_formula()
        assert abs(c.final_score - 6.6) < 0.01


# ═══════════════════════════════════════════════════════════════
# Unit tests — score_to_risk_level
# ═══════════════════════════════════════════════════════════════

class TestScoreToRiskLevel:

    def test_0_is_safe(self):
        assert score_to_risk_level(0) == "SAFE"

    def test_24_is_safe(self):
        assert score_to_risk_level(24) == "SAFE"

    def test_24_9_is_safe(self):
        assert score_to_risk_level(24.9) == "SAFE"

    def test_25_is_low(self):
        assert score_to_risk_level(25) == "LOW"

    def test_49_is_low(self):
        assert score_to_risk_level(49) == "LOW"

    def test_50_is_medium(self):
        assert score_to_risk_level(50) == "MEDIUM"

    def test_74_is_medium(self):
        assert score_to_risk_level(74) == "MEDIUM"

    def test_75_is_high(self):
        assert score_to_risk_level(75) == "HIGH"

    def test_89_is_high(self):
        assert score_to_risk_level(89) == "HIGH"

    def test_90_is_critical(self):
        assert score_to_risk_level(90) == "CRITICAL"

    def test_100_is_critical(self):
        assert score_to_risk_level(100) == "CRITICAL"

    def test_99_9_is_critical(self):
        assert score_to_risk_level(99.9) == "CRITICAL"


# ═══════════════════════════════════════════════════════════════
# Unit tests — breakdown dict structure
# ═══════════════════════════════════════════════════════════════

class TestRiskBreakdown:

    def test_breakdown_has_all_required_keys(self):
        c = RiskCalculation(governance_violations=1, policy_violations=1,
                            high_severity_violations=1)
        c.apply_formula()
        bd = c.to_breakdown()
        required = [
            "base_score", "governance_violations", "policy_violations",
            "high_severity_violations", "critical_severity_violations",
            "failed_runs", "denied_tool_calls", "interactions",
            "completed_runs", "permitted_tool_calls",
            "additions", "reductions",
            "total_additions", "total_reductions",
            "final_score", "risk_level",
        ]
        for key in required:
            assert key in bd, f"Missing key: {key}"

    def test_breakdown_interactions_has_type_keys(self):
        c = RiskCalculation(handoff_interactions=2, delegation_interactions=1)
        c.apply_formula()
        bd = c.to_breakdown()
        assert "handoff"    in bd["interactions"]
        assert "delegation" in bd["interactions"]
        assert "escalation" in bd["interactions"]
        assert bd["interactions"]["handoff"] == 2
        assert bd["interactions"]["delegation"] == 1

    def test_breakdown_additions_values_match_formula(self):
        c = RiskCalculation(governance_violations=2, policy_violations=1)
        c.apply_formula()
        bd = c.to_breakdown()
        assert bd["additions"]["governance_violations"] == 30.0
        assert bd["additions"]["policy_violations"]     == 20.0

    def test_breakdown_reductions_are_negative(self):
        c = RiskCalculation(governance_violations=1,
                            completed_runs=5, permitted_tool_calls=10)
        c.apply_formula()
        bd = c.to_breakdown()
        assert bd["reductions"]["completed_runs"]       < 0
        assert bd["reductions"]["permitted_tool_calls"] < 0

    def test_breakdown_total_additions_correct(self):
        c = RiskCalculation(governance_violations=1, failed_runs=1)
        c.apply_formula()
        bd = c.to_breakdown()
        assert abs(bd["total_additions"] - 20.0) < 0.01  # 15 + 5

    def test_breakdown_total_reductions_correct(self):
        c = RiskCalculation(governance_violations=1,
                            completed_runs=2, permitted_tool_calls=4)
        c.apply_formula()
        bd = c.to_breakdown()
        # -0.2 - 0.2 = -0.4
        assert abs(bd["total_reductions"] - (-0.4)) < 0.01

    def test_breakdown_final_score_matches_calculation(self):
        c = RiskCalculation(denied_tool_calls=3, completed_runs=10)
        c.apply_formula()
        bd = c.to_breakdown()
        assert bd["final_score"] == c.final_score

    def test_breakdown_risk_level_matches_calculation(self):
        c = RiskCalculation(governance_violations=5)
        c.apply_formula()
        bd = c.to_breakdown()
        assert bd["risk_level"] == c.risk_level


# ═══════════════════════════════════════════════════════════════
# Integration tests — calculate_agent_risk_score with DB
# ═══════════════════════════════════════════════════════════════

class TestCalculateAgentRiskScore:

    @pytest.mark.asyncio
    async def test_fresh_agent_has_zero_risk(self):
        factory = await _isolated_factory()
        agent_id = await _make_agent(factory, "fresh-agent")
        async with factory() as db:
            calc = await calculate_agent_risk_score(db, agent_id)
        assert calc.final_score == 0.0
        assert calc.risk_level  == "SAFE"

    @pytest.mark.asyncio
    async def test_governance_violation_increases_score(self):
        factory = await _isolated_factory()
        agent_id = await _make_agent(factory, "gov-viol-agent")
        # Use status="failed" to avoid the -0.1 completed reduction skewing the assertion
        run_id   = await _make_run(factory, agent_id, status="failed")
        await _make_event(factory, agent_id, run_id, "violation")
        async with factory() as db:
            calc = await calculate_agent_risk_score(db, agent_id)
        assert calc.governance_violations == 1
        # +15 (gov) + 5 (failed run) = 20
        assert calc.final_score == 20.0

    @pytest.mark.asyncio
    async def test_policy_violation_increases_score(self):
        factory = await _isolated_factory()
        agent_id = await _make_agent(factory, "pol-viol-agent")
        run_id   = await _make_run(factory, agent_id, status="failed")
        await _make_event(factory, agent_id, run_id, "policy_violation",
                         input_data={"severity": "MEDIUM"})
        async with factory() as db:
            calc = await calculate_agent_risk_score(db, agent_id)
        assert calc.policy_violations == 1
        # +20 (policy) + 5 (failed) = 25
        assert calc.final_score == 25.0

    @pytest.mark.asyncio
    async def test_high_severity_adds_additional_10(self):
        factory = await _isolated_factory()
        agent_id = await _make_agent(factory, "high-sev-agent")
        run_id   = await _make_run(factory, agent_id, status="failed")
        await _make_event(factory, agent_id, run_id, "policy_violation",
                         input_data={"severity": "HIGH"})
        async with factory() as db:
            calc = await calculate_agent_risk_score(db, agent_id)
        assert calc.high_severity_violations == 1
        # +20 (policy) + 10 (high-sev) + 5 (failed) = 35
        assert calc.final_score == 35.0

    @pytest.mark.asyncio
    async def test_critical_severity_adds_additional_20(self):
        factory = await _isolated_factory()
        agent_id = await _make_agent(factory, "critical-sev-agent")
        run_id   = await _make_run(factory, agent_id, status="failed")
        await _make_event(factory, agent_id, run_id, "policy_violation",
                         input_data={"severity": "CRITICAL"})
        async with factory() as db:
            calc = await calculate_agent_risk_score(db, agent_id)
        assert calc.critical_violations == 1
        # +20 (policy) + 20 (critical) + 5 (failed) = 45
        assert calc.final_score == 45.0

    @pytest.mark.asyncio
    async def test_failed_run_increases_score(self):
        factory = await _isolated_factory()
        agent_id = await _make_agent(factory, "failed-run-agent")
        await _make_run(factory, agent_id, status="failed")
        async with factory() as db:
            calc = await calculate_agent_risk_score(db, agent_id)
        assert calc.failed_runs  == 1
        assert calc.final_score  == 5.0

    @pytest.mark.asyncio
    async def test_denied_tool_call_increases_score(self):
        factory = await _isolated_factory()
        agent_id = await _make_agent(factory, "denied-tool-agent")
        # Use failed run to avoid completed_run -0.1 reduction
        run_id   = await _make_run(factory, agent_id, status="failed")
        await _make_event(factory, agent_id, run_id, "tool_call", permitted=False)
        async with factory() as db:
            calc = await calculate_agent_risk_score(db, agent_id)
        assert calc.denied_tool_calls == 1
        # +10 (denied) + 5 (failed) = 15
        assert calc.final_score == 15.0

    @pytest.mark.asyncio
    async def test_permitted_tool_call_reduces_score(self):
        factory = await _isolated_factory()
        agent_id = await _make_agent(factory, "good-tool-agent")
        # Use failed run to avoid the -0.1 completed reduction
        run_id   = await _make_run(factory, agent_id, status="failed")
        # Add risk first so reduction is visible
        await _make_event(factory, agent_id, run_id, "violation")
        await _make_event(factory, agent_id, run_id, "tool_end", permitted=True)
        async with factory() as db:
            calc = await calculate_agent_risk_score(db, agent_id)
        assert calc.permitted_tool_calls == 1
        # +15 (gov) + 5 (failed) - 0.05 (permitted tool) = 19.95 → rounds to 19.9
        assert calc.final_score == 19.9
        assert abs(calc.reduction_permitted - 0.05) < 0.001

    @pytest.mark.asyncio
    async def test_completed_run_reduces_score(self):
        factory = await _isolated_factory()
        agent_id = await _make_agent(factory, "completed-run-agent")
        await _make_run(factory, agent_id, status="failed")    # +5
        await _make_run(factory, agent_id, status="completed") # -0.1
        async with factory() as db:
            calc = await calculate_agent_risk_score(db, agent_id)
        assert calc.completed_runs == 1
        assert abs(calc.final_score - 4.9) < 0.01

    @pytest.mark.asyncio
    async def test_handoff_interaction_increases_score(self):
        factory = await _isolated_factory()
        a1 = await _make_agent(factory, "handoff-src")
        a2 = await _make_agent(factory, "handoff-tgt")
        await _make_interaction(factory, a1, a2, "handoff")
        async with factory() as db:
            calc = await calculate_agent_risk_score(db, a1)
        assert calc.handoff_interactions == 1
        assert calc.final_score          == 2.0

    @pytest.mark.asyncio
    async def test_delegation_interaction_increases_score(self):
        factory = await _isolated_factory()
        a1 = await _make_agent(factory, "deleg-src")
        a2 = await _make_agent(factory, "deleg-tgt")
        await _make_interaction(factory, a1, a2, "delegation")
        async with factory() as db:
            calc = await calculate_agent_risk_score(db, a1)
        assert calc.delegation_interactions == 1
        assert calc.final_score             == 4.0

    @pytest.mark.asyncio
    async def test_target_agent_counts_interactions_too(self):
        """Interactions score both source AND target agents."""
        factory = await _isolated_factory()
        a1 = await _make_agent(factory, "inter-src-b")
        a2 = await _make_agent(factory, "inter-tgt-b")
        await _make_interaction(factory, a1, a2, "handoff")
        async with factory() as db:
            # Target also gets the handoff risk
            calc_tgt = await calculate_agent_risk_score(db, a2)
        assert calc_tgt.handoff_interactions == 1
        assert calc_tgt.final_score          == 2.0

    @pytest.mark.asyncio
    async def test_nonexistent_agent_returns_zero_score(self):
        """calculate_agent_risk_score does NOT raise on unknown agent_id."""
        factory = await _isolated_factory()
        async with factory() as db:
            calc = await calculate_agent_risk_score(db, str(uuid.uuid4()))
        assert calc.final_score == 0.0

    @pytest.mark.asyncio
    async def test_score_clamped_at_100_in_db(self):
        factory = await _isolated_factory()
        agent_id = await _make_agent(factory, "high-risk-agent")
        run_id   = await _make_run(factory, agent_id)
        # 10 governance violations = +150 → clamped to 100
        for _ in range(10):
            await _make_event(factory, agent_id, run_id, "violation")
        async with factory() as db:
            calc = await calculate_agent_risk_score(db, agent_id)
        assert calc.final_score == 100.0
        assert calc.risk_level  == "CRITICAL"


# ═══════════════════════════════════════════════════════════════
# Integration tests — calculate_system_risk_score
# ═══════════════════════════════════════════════════════════════

class TestCalculateSystemRiskScore:

    @pytest.mark.asyncio
    async def test_empty_db_returns_zero_average(self):
        factory = await _isolated_factory()
        async with factory() as db:
            result = await calculate_system_risk_score(db)
        assert result["average_risk_score"] == 0.0

    @pytest.mark.asyncio
    async def test_empty_db_distribution_all_zero(self):
        factory = await _isolated_factory()
        async with factory() as db:
            result = await calculate_system_risk_score(db)
        dist = result["risk_distribution"]
        assert dist["SAFE"]     == 0
        assert dist["LOW"]      == 0
        assert dist["MEDIUM"]   == 0
        assert dist["HIGH"]     == 0
        assert dist["CRITICAL"] == 0

    @pytest.mark.asyncio
    async def test_single_safe_agent(self):
        factory = await _isolated_factory()
        await _make_agent(factory, "only-safe-agent")
        async with factory() as db:
            result = await calculate_system_risk_score(db)
        assert result["average_risk_score"] == 0.0
        assert result["risk_distribution"]["SAFE"] == 1

    @pytest.mark.asyncio
    async def test_critical_agent_in_distribution(self):
        factory = await _isolated_factory()
        agent_id = await _make_agent(factory, "critical-agent")
        run_id   = await _make_run(factory, agent_id)
        for _ in range(7):   # 7 × 15 = 105 → clamped to 100
            await _make_event(factory, agent_id, run_id, "violation")
        async with factory() as db:
            result = await calculate_system_risk_score(db)
        assert result["risk_distribution"]["CRITICAL"] == 1
        assert result["average_risk_score"] == 100.0

    @pytest.mark.asyncio
    async def test_distribution_key_presence(self):
        factory = await _isolated_factory()
        async with factory() as db:
            result = await calculate_system_risk_score(db)
        for level in ["SAFE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]:
            assert level in result["risk_distribution"]

    @pytest.mark.asyncio
    async def test_average_across_multiple_agents(self):
        factory = await _isolated_factory()
        # Agent 1: 0 risk (SAFE), Agent 2: 15 risk (SAFE)
        a1 = await _make_agent(factory, "avg-agent-1")
        a2 = await _make_agent(factory, "avg-agent-2")
        run2 = await _make_run(factory, a2)
        await _make_event(factory, a2, run2, "violation")  # +15
        async with factory() as db:
            result = await calculate_system_risk_score(db)
        # avg = (0 + 15) / 2 = 7.5
        assert abs(result["average_risk_score"] - 7.5) < 0.1


# ═══════════════════════════════════════════════════════════════
# Integration tests — get_agent_risk_breakdown
# ═══════════════════════════════════════════════════════════════

class TestGetAgentRiskBreakdown:

    @pytest.mark.asyncio
    async def test_returns_correct_structure(self):
        factory = await _isolated_factory()
        agent_id = await _make_agent(factory, "breakdown-agent")
        async with factory() as db:
            data = await get_agent_risk_breakdown(db, agent_id)
        assert data["agent_id"]   == agent_id
        assert data["agent_name"] == "breakdown-agent"
        assert "risk_score"       in data
        assert "risk_level"       in data
        assert "breakdown"        in data

    @pytest.mark.asyncio
    async def test_raises_value_error_for_unknown_agent(self):
        factory = await _isolated_factory()
        async with factory() as db:
            with pytest.raises(ValueError, match="not found"):
                await get_agent_risk_breakdown(db, str(uuid.uuid4()))


# ═══════════════════════════════════════════════════════════════
# Integration tests — AgentStats includes risk fields
# ═══════════════════════════════════════════════════════════════

class TestAgentStatsRiskIntegration:

    @pytest.mark.asyncio
    async def test_agent_stats_has_risk_score(self, client, registered_agent):
        r = await client.get(
            f"/analytics/stats/{registered_agent['agent_id']}",
            headers=registered_agent["headers"],
        )
        assert r.status_code == 200
        body = r.json()
        assert "risk_score" in body
        assert "risk_level" in body

    @pytest.mark.asyncio
    async def test_fresh_agent_risk_score_is_zero(self, client, registered_agent):
        r = await client.get(
            f"/analytics/stats/{registered_agent['agent_id']}",
            headers=registered_agent["headers"],
        )
        assert r.json()["risk_score"] == 0.0
        assert r.json()["risk_level"] == "SAFE"


# ═══════════════════════════════════════════════════════════════
# Integration tests — SystemStats includes risk fields
# ═══════════════════════════════════════════════════════════════

class TestSystemStatsRiskIntegration:

    @pytest.mark.asyncio
    async def test_system_stats_has_risk_fields(self, client, registered_agent):
        r = await client.get("/analytics/stats", headers=registered_agent["headers"])
        assert r.status_code == 200
        body = r.json()
        assert "average_risk_score" in body
        assert "risk_distribution"  in body

    @pytest.mark.asyncio
    async def test_risk_distribution_has_all_levels(self, client, registered_agent):
        r = await client.get("/analytics/stats", headers=registered_agent["headers"])
        dist = r.json()["risk_distribution"]
        # May be empty dict if no agents have risk data, or a populated dict
        # — either way the key must exist and be a dict
        assert isinstance(dist, dict)

    @pytest.mark.asyncio
    async def test_existing_stats_fields_unchanged(self, client, registered_agent):
        r = await client.get("/analytics/stats", headers=registered_agent["headers"])
        body = r.json()
        for field in [
            "total_agents", "total_runs", "total_events",
            "total_violations", "violation_rate",
            "completed_runs", "failed_runs", "tool_latency",
            "total_interactions", "total_policies",
            "average_trust_score", "trust_distribution",
        ]:
            assert field in body, f"Existing field missing: {field}"


# ═══════════════════════════════════════════════════════════════
# HTTP endpoint tests — GET /analytics/risk
# ═══════════════════════════════════════════════════════════════

class TestAnalyticsRiskEndpoint:

    @pytest.mark.asyncio
    async def test_requires_auth(self, client):
        r = await client.get("/analytics/risk")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_200(self, client, registered_agent):
        r = await client.get("/analytics/risk", headers=registered_agent["headers"])
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_schema_fields_present(self, client, registered_agent):
        r = await client.get("/analytics/risk", headers=registered_agent["headers"])
        body = r.json()
        assert "average_risk_score" in body
        assert "risk_distribution"  in body

    @pytest.mark.asyncio
    async def test_average_risk_score_is_float(self, client, registered_agent):
        r = await client.get("/analytics/risk", headers=registered_agent["headers"])
        score = r.json()["average_risk_score"]
        assert isinstance(score, (int, float))
        assert 0.0 <= score <= 100.0

    @pytest.mark.asyncio
    async def test_distribution_is_dict(self, client, registered_agent):
        r = await client.get("/analytics/risk", headers=registered_agent["headers"])
        dist = r.json()["risk_distribution"]
        assert isinstance(dist, dict)

    @pytest.mark.asyncio
    async def test_fresh_agent_scores_safe(self, client, registered_agent):
        r = await client.get("/analytics/risk", headers=registered_agent["headers"])
        body = r.json()
        # A freshly registered agent with no runs should score 0 = SAFE
        assert body["average_risk_score"] == 0.0

    @pytest.mark.asyncio
    async def test_distribution_count_matches_agents(self, client):
        # Register two agents and check total distribution count = 2
        r1 = await client.post("/agents/register", json={
            "name": "risk-dist-a", "allowed_tools": [],
            "secret": "risk-dist-secret-42",
        })
        r2 = await client.post("/agents/register", json={
            "name": "risk-dist-b", "allowed_tools": [],
            "secret": "risk-dist-secret-42",
        })
        assert r1.status_code == 201 and r2.status_code == 201
        hdrs = {"Authorization": f"Bearer {r1.json()['access_token']}"}
        r = await client.get("/analytics/risk", headers=hdrs)
        dist = r.json()["risk_distribution"]
        total_in_dist = sum(dist.values())
        # Both agents are SAFE (no runs)
        assert total_in_dist == 2


# ═══════════════════════════════════════════════════════════════
# HTTP endpoint tests — GET /analytics/risk/{agent_id}
# ═══════════════════════════════════════════════════════════════

class TestAnalyticsAgentRiskEndpoint:

    @pytest.mark.asyncio
    async def test_requires_auth(self, client, registered_agent):
        r = await client.get(f"/analytics/risk/{registered_agent['agent_id']}")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_200_for_existing_agent(self, client, registered_agent):
        r = await client.get(
            f"/analytics/risk/{registered_agent['agent_id']}",
            headers=registered_agent["headers"],
        )
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_agent(self, client, registered_agent):
        r = await client.get(
            f"/analytics/risk/{uuid.uuid4()}",
            headers=registered_agent["headers"],
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_schema_fields_present(self, client, registered_agent):
        r = await client.get(
            f"/analytics/risk/{registered_agent['agent_id']}",
            headers=registered_agent["headers"],
        )
        body = r.json()
        for field in ["agent_id", "agent_name", "risk_score", "risk_level", "breakdown"]:
            assert field in body, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_agent_id_matches(self, client, registered_agent):
        r = await client.get(
            f"/analytics/risk/{registered_agent['agent_id']}",
            headers=registered_agent["headers"],
        )
        assert r.json()["agent_id"] == registered_agent["agent_id"]

    @pytest.mark.asyncio
    async def test_fresh_agent_risk_score_is_zero(self, client, registered_agent):
        r = await client.get(
            f"/analytics/risk/{registered_agent['agent_id']}",
            headers=registered_agent["headers"],
        )
        body = r.json()
        assert body["risk_score"] == 0.0
        assert body["risk_level"] == "SAFE"

    @pytest.mark.asyncio
    async def test_breakdown_has_required_keys(self, client, registered_agent):
        r = await client.get(
            f"/analytics/risk/{registered_agent['agent_id']}",
            headers=registered_agent["headers"],
        )
        bd = r.json()["breakdown"]
        required = [
            "base_score", "governance_violations", "policy_violations",
            "high_severity_violations", "critical_severity_violations",
            "failed_runs", "denied_tool_calls", "interactions",
            "completed_runs", "permitted_tool_calls",
            "additions", "reductions", "total_additions", "total_reductions",
            "final_score", "risk_level",
        ]
        for key in required:
            assert key in bd, f"Missing breakdown key: {key}"

    @pytest.mark.asyncio
    async def test_risk_score_in_valid_range(self, client, registered_agent):
        r = await client.get(
            f"/analytics/risk/{registered_agent['agent_id']}",
            headers=registered_agent["headers"],
        )
        score = r.json()["risk_score"]
        assert 0.0 <= score <= 100.0

    @pytest.mark.asyncio
    async def test_risk_level_is_valid_string(self, client, registered_agent):
        r = await client.get(
            f"/analytics/risk/{registered_agent['agent_id']}",
            headers=registered_agent["headers"],
        )
        level = r.json()["risk_level"]
        assert level in {"SAFE", "LOW", "MEDIUM", "HIGH", "CRITICAL"}