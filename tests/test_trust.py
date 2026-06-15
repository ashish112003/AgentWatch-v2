"""
tests/test_trust.py
────────────────────
Comprehensive tests for the Trust Score system (Phase 4).

Coverage:
  Unit — TrustCalculation formula, clamping, trust levels, breakdown dict
  Unit — score_to_level() boundary values
  Integration — calculate_agent_trust_score() with seeded DB data
  Integration — calculate_system_trust_score() across multiple agents
  Integration — AgentStats includes trust_score and trust_level
  Integration — SystemStats includes average_trust_score and trust_distribution
  HTTP — GET /analytics/trust
  HTTP — GET /analytics/trust/{agent_id}
  HTTP — GET /analytics/stats/{agent_id} includes trust fields
  HTTP — GET /analytics/stats includes trust fields

Seeding strategy:
  Each test that needs data injects AgentRun and AgentEvent rows directly
  into the in-memory SQLite DB via db_session_factory (from conftest.py).
  No LLM calls.  No GROQ_API_KEY required.
"""

import uuid
import pytest
import pytest_asyncio
from datetime import datetime
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.db.database import Base
from app.models.agent import Agent, AgentRun, AgentEvent, AgentInteraction
from app.services.trust_service import (
    TrustCalculation,
    score_to_level,
    calculate_agent_trust_score,
    calculate_system_trust_score,
    get_agent_trust_breakdown,
    get_trust_distribution,
)
pytestmark = pytest.mark.asyncio

# ── Seed helpers ──────────────────────────────────────────────────────────────

def _ts() -> datetime:
    return datetime(2024, 1, 15, 10, 0, 0)


async def _make_agent(db_factory, name: str) -> str:
    """Insert a minimal Agent row and return its id."""
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
    """Insert an AgentRun and return its id."""
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


async def _make_interaction(db_factory, src: str, tgt: str) -> None:
    async with db_factory() as db:
        db.add(AgentInteraction(
            id=str(uuid.uuid4()),
            source_agent_id=src, target_agent_id=tgt,
            interaction_type="handoff",
        ))
        await db.commit()


async def _register(client: AsyncClient, name: str) -> dict:
    r = await client.post("/agents/register", json={
        "name": name,
        "allowed_tools": ["calculator"],
        "secret": "trust-test-secret-42",
    })
    assert r.status_code == 201
    data = r.json()
    return {
        "agent_id": data["agent_id"],
        "headers":  {"Authorization": f"Bearer {data['access_token']}"},
    }


# ═══════════════════════════════════════════════════════════════
# Unit tests — TrustCalculation formula
# ═══════════════════════════════════════════════════════════════

class TestTrustCalculationFormula:

    def test_default_score_is_100(self):
        c = TrustCalculation()
        c.apply_formula()
        assert c.final_score == 100.0

    def test_governance_violation_deducts_5(self):
        c = TrustCalculation(governance_violations=1)
        c.apply_formula()
        assert c.final_score == 95.0

    def test_policy_violation_deducts_8(self):
        c = TrustCalculation(policy_violations=1)
        c.apply_formula()
        assert c.final_score == 92.0

    def test_failed_run_deducts_2(self):
        c = TrustCalculation(failed_runs=1)
        c.apply_formula()
        assert c.final_score == 98.0

    def test_high_severity_violation_deducts_additional_5(self):
        # policy_violation base (-8) + high severity (-5) = -13
        c = TrustCalculation(policy_violations=1, high_severity_violations=1)
        c.apply_formula()
        assert c.final_score == 87.0

    def test_critical_severity_violation_deducts_additional_10(self):
        # policy_violation base (-8) + critical (-10) = -18
        c = TrustCalculation(policy_violations=1, critical_violations=1)
        c.apply_formula()
        assert c.final_score == 82.0

    def test_completed_run_adds_0_2(self):
        # +0.2 per completed run, but clamped to 100.0 at the maximum
        c = TrustCalculation(completed_runs=1)
        c.apply_formula()
        assert c.final_score == 100.0  # 100 + 0.2 = 100.2, clamped to 100.0
        assert c.addition_completed == pytest.approx(0.2)

    def test_permitted_tool_call_adds_0_1(self):
        # One deduction to go below 100 first so addition is visible
        c = TrustCalculation(failed_runs=1, permitted_tool_calls=1)
        c.apply_formula()
        assert abs(c.final_score - 98.1) < 0.01

    def test_interaction_adds_0_1(self):
        c = TrustCalculation(failed_runs=1, interactions=1)
        c.apply_formula()
        assert abs(c.final_score - 98.1) < 0.01

    def test_combined_formula(self):
        """
        10 completed, 2 failed, 1 gov violation, 1 policy violation,
        1 high-sev, 20 tool calls, 5 interactions.
        100 - 5 - 8 - 4 - 5 + 2 + 2 + 0.5 = 82.5
        """
        c = TrustCalculation(
            completed_runs=10, failed_runs=2,
            governance_violations=1, policy_violations=1,
            high_severity_violations=1,
            permitted_tool_calls=20, interactions=5,
        )
        c.apply_formula()
        assert abs(c.final_score - 82.5) < 0.01

    def test_score_clamped_at_zero(self):
        c = TrustCalculation(governance_violations=1000)
        c.apply_formula()
        assert c.final_score == 0.0

    def test_score_clamped_at_100(self):
        c = TrustCalculation(completed_runs=1000, permitted_tool_calls=1000)
        c.apply_formula()
        assert c.final_score == 100.0

    def test_score_rounded_to_1_decimal(self):
        c = TrustCalculation(failed_runs=1, permitted_tool_calls=3)  # 98.3
        c.apply_formula()
        assert isinstance(c.final_score, float)
        # Check it has at most 1 decimal place
        assert c.final_score == round(c.final_score, 1)

    def test_deduction_fields_populated(self):
        c = TrustCalculation(governance_violations=2, policy_violations=1, failed_runs=3)
        c.apply_formula()
        assert c.deduction_gov    == 10.0
        assert c.deduction_policy == 8.0
        assert c.deduction_failed == 6.0

    def test_addition_fields_populated(self):
        c = TrustCalculation(failed_runs=5, completed_runs=5, permitted_tool_calls=10, interactions=10)
        c.apply_formula()
        assert abs(c.addition_completed    - 1.0) < 0.01
        assert abs(c.addition_tools        - 1.0) < 0.01
        assert abs(c.addition_interactions - 1.0) < 0.01


# ═══════════════════════════════════════════════════════════════
# Unit tests — score_to_level boundaries
# ═══════════════════════════════════════════════════════════════

class TestScoreToLevel:

    def test_100_is_trusted(self):
        assert score_to_level(100.0) == "TRUSTED"

    def test_90_is_trusted(self):
        assert score_to_level(90.0) == "TRUSTED"

    def test_89_is_monitored(self):
        assert score_to_level(89.9) == "MONITORED"

    def test_70_is_monitored(self):
        assert score_to_level(70.0) == "MONITORED"

    def test_69_is_warning(self):
        assert score_to_level(69.9) == "WARNING"

    def test_50_is_warning(self):
        assert score_to_level(50.0) == "WARNING"

    def test_49_is_high_risk(self):
        assert score_to_level(49.9) == "HIGH_RISK"

    def test_0_is_high_risk(self):
        assert score_to_level(0.0) == "HIGH_RISK"


# ═══════════════════════════════════════════════════════════════
# Unit tests — TrustCalculation.to_breakdown()
# ═══════════════════════════════════════════════════════════════

class TestTrustBreakdown:

    def test_breakdown_contains_all_required_keys(self):
        c = TrustCalculation(governance_violations=1)
        c.apply_formula()
        bd = c.to_breakdown()
        for key in [
            "base_score", "governance_violations", "policy_violations",
            "high_severity_violations", "critical_violations",
            "failed_runs", "completed_runs", "permitted_tool_calls",
            "interactions", "deductions", "additions",
            "total_deductions", "total_additions",
            "final_score", "trust_level",
        ]:
            assert key in bd, f"Missing key: {key}"

    def test_breakdown_deductions_are_negative(self):
        c = TrustCalculation(governance_violations=1, policy_violations=1, failed_runs=1)
        c.apply_formula()
        bd = c.to_breakdown()
        assert bd["total_deductions"] < 0
        for k, v in bd["deductions"].items():
            assert v <= 0, f"Deduction {k} should be <= 0, got {v}"

    def test_breakdown_additions_are_non_negative(self):
        c = TrustCalculation(completed_runs=5, permitted_tool_calls=10)
        c.apply_formula()
        bd = c.to_breakdown()
        assert bd["total_additions"] >= 0
        for k, v in bd["additions"].items():
            assert v >= 0, f"Addition {k} should be >= 0"

    def test_breakdown_final_score_matches_calc(self):
        c = TrustCalculation(governance_violations=2, completed_runs=5)
        c.apply_formula()
        bd = c.to_breakdown()
        assert bd["final_score"] == c.final_score
        assert bd["trust_level"] == c.trust_level


# ═══════════════════════════════════════════════════════════════
# Integration tests — calculate_agent_trust_score with DB
# ═══════════════════════════════════════════════════════════════

class TestCalculateAgentTrustScore:

    @pytest.mark.asyncio
    async def test_fresh_agent_scores_100(self, db_session_factory):
        agent_id = await _make_agent(db_session_factory, "fresh-trust-bot")
        async with db_session_factory() as db:
            calc = await calculate_agent_trust_score(db, agent_id)
        assert calc.final_score == 100.0
        assert calc.trust_level == "TRUSTED"

    @pytest.mark.asyncio
    async def test_unknown_agent_returns_default_100(self, db_session_factory):
        async with db_session_factory() as db:
            calc = await calculate_agent_trust_score(db, str(uuid.uuid4()))
        assert calc.final_score == 100.0

    @pytest.mark.asyncio
    async def test_governance_violation_lowers_score(self, db_session_factory):
        agent_id = await _make_agent(db_session_factory, "viol-trust-bot")
        run_id   = await _make_run(db_session_factory, agent_id)
        await _make_event(db_session_factory, agent_id, run_id, "violation", permitted=False)

        async with db_session_factory() as db:
            calc = await calculate_agent_trust_score(db, agent_id)

        assert calc.governance_violations == 1
        # 100 - 5 (violation) + 0.2 (completed run from _make_run) = 95.2
        assert abs(calc.final_score - 95.2) < 0.01

    @pytest.mark.asyncio
    async def test_policy_violation_medium_lowers_score_8(self, db_session_factory):
        agent_id = await _make_agent(db_session_factory, "pv-medium-bot")
        run_id   = await _make_run(db_session_factory, agent_id)
        await _make_event(
            db_session_factory, agent_id, run_id, "policy_violation",
            input_data={"policy_id": "x", "severity": "MEDIUM"},
        )
        async with db_session_factory() as db:
            calc = await calculate_agent_trust_score(db, agent_id)

        assert calc.policy_violations == 1
        assert calc.high_severity_violations == 0
        assert calc.critical_violations == 0
        # 100 - 8 (policy) + 0.2 (completed run) = 92.2
        assert abs(calc.final_score - 92.2) < 0.01

    @pytest.mark.asyncio
    async def test_policy_violation_high_deducts_extra_5(self, db_session_factory):
        agent_id = await _make_agent(db_session_factory, "pv-high-bot")
        run_id   = await _make_run(db_session_factory, agent_id)
        await _make_event(
            db_session_factory, agent_id, run_id, "policy_violation",
            input_data={"policy_id": "x", "severity": "HIGH"},
        )
        async with db_session_factory() as db:
            calc = await calculate_agent_trust_score(db, agent_id)

        assert calc.high_severity_violations == 1
        # -8 (policy) -5 (high sev) + 0.2 (completed run) = 87.2
        assert abs(calc.final_score - 87.2) < 0.01

    @pytest.mark.asyncio
    async def test_policy_violation_critical_deducts_extra_10(self, db_session_factory):
        agent_id = await _make_agent(db_session_factory, "pv-crit-bot")
        run_id   = await _make_run(db_session_factory, agent_id)
        await _make_event(
            db_session_factory, agent_id, run_id, "policy_violation",
            input_data={"policy_id": "x", "severity": "CRITICAL"},
        )
        async with db_session_factory() as db:
            calc = await calculate_agent_trust_score(db, agent_id)

        assert calc.critical_violations == 1
        # -8 (policy) -10 (critical) + 0.2 (completed run) = 82.2
        assert abs(calc.final_score - 82.2) < 0.01

    @pytest.mark.asyncio
    async def test_failed_runs_lower_score(self, db_session_factory):
        agent_id = await _make_agent(db_session_factory, "failed-run-bot")
        for _ in range(3):
            await _make_run(db_session_factory, agent_id, status="failed")

        async with db_session_factory() as db:
            calc = await calculate_agent_trust_score(db, agent_id)

        assert calc.failed_runs == 3
        assert abs(calc.final_score - 94.0) < 0.01  # 100 - 3*2

    @pytest.mark.asyncio
    async def test_permitted_tool_calls_contribute_positively(self, db_session_factory):
        agent_id = await _make_agent(db_session_factory, "tool-pos-bot")
        # Add a deduction first so additions are visible (otherwise clamped at 100)
        await _make_run(db_session_factory, agent_id, status="failed")
        run_id = await _make_run(db_session_factory, agent_id, status="completed")
        for _ in range(10):
            await _make_event(
                db_session_factory, agent_id, run_id, "tool_end", permitted=True
            )

        async with db_session_factory() as db:
            calc = await calculate_agent_trust_score(db, agent_id)

        assert calc.permitted_tool_calls == 10
        # 100 - 2 (failed run) + 0.2 (completed run in _make_run) + 1.0 (tool calls) = 99.2
        assert abs(calc.final_score - 99.2) < 0.1

    @pytest.mark.asyncio
    async def test_interactions_contribute_positively(self, db_session_factory):
        src_id = await _make_agent(db_session_factory, "src-interact-bot")
        tgt_id = await _make_agent(db_session_factory, "tgt-interact-bot")
        # Deduction so we can see the addition
        await _make_run(db_session_factory, src_id, status="failed")
        for _ in range(5):
            await _make_interaction(db_session_factory, src_id, tgt_id)

        async with db_session_factory() as db:
            calc = await calculate_agent_trust_score(db, src_id)

        assert calc.interactions == 5
        # 100 - 2 + 0.5 = 98.5
        assert abs(calc.final_score - 98.5) < 0.1

    @pytest.mark.asyncio
    async def test_score_not_below_zero(self, db_session_factory):
        agent_id = await _make_agent(db_session_factory, "rock-bottom-bot")
        run_id   = await _make_run(db_session_factory, agent_id)
        for _ in range(100):
            await _make_event(db_session_factory, agent_id, run_id, "violation", permitted=False)

        async with db_session_factory() as db:
            calc = await calculate_agent_trust_score(db, agent_id)

        assert calc.final_score == 0.0

    @pytest.mark.asyncio
    async def test_trust_level_changes_with_score(self, db_session_factory):
        agent_id = await _make_agent(db_session_factory, "level-check-bot")
        run_id   = await _make_run(db_session_factory, agent_id)
        # Drive score to WARNING range (~55)
        # Note: _make_run creates 1 completed run (+0.2), so score = 55.2
        for _ in range(9):  # 9 * 5 = 45 deducted
            await _make_event(db_session_factory, agent_id, run_id, "violation", permitted=False)

        async with db_session_factory() as db:
            calc = await calculate_agent_trust_score(db, agent_id)

        assert abs(calc.final_score - 55.2) < 0.1  # 100 - 45 + 0.2
        assert calc.trust_level == "WARNING"


# ═══════════════════════════════════════════════════════════════
# Integration tests — system-wide trust
# ═══════════════════════════════════════════════════════════════

class TestSystemTrustScore:

    @pytest.mark.asyncio
    async def test_no_agents_returns_zero_average(self, db_session_factory):
        async with db_session_factory() as db:
            result = await calculate_system_trust_score(db)
        assert result["average_trust_score"] == 0.0
        assert result["trust_distribution"] == {
            "TRUSTED": 0, "MONITORED": 0, "WARNING": 0, "HIGH_RISK": 0
        }

    @pytest.mark.asyncio
    async def test_single_clean_agent_100_average(self, db_session_factory):
        await _make_agent(db_session_factory, "sys-clean-bot")
        async with db_session_factory() as db:
            result = await calculate_system_trust_score(db)
        assert result["average_trust_score"] == 100.0
        assert result["trust_distribution"]["TRUSTED"] == 1

    @pytest.mark.asyncio
    async def test_distribution_counts_all_levels(self, db_session_factory):
        # Create 4 agents at different trust levels
        # Agent A: clean → TRUSTED (100)
        await _make_agent(db_session_factory, "sys-dist-a")

        # Agent B: 6 gov violations → WARNING (70.0)
        b_id   = await _make_agent(db_session_factory, "sys-dist-b")
        b_run  = await _make_run(db_session_factory, b_id)
        for _ in range(6):
            await _make_event(db_session_factory, b_id, b_run, "violation", permitted=False)
        # b score: 100 - 30 = 70 → MONITORED boundary

        # Agent C: 11 gov violations → 100 - 55 = 45 → HIGH_RISK
        c_id  = await _make_agent(db_session_factory, "sys-dist-c")
        c_run = await _make_run(db_session_factory, c_id)
        for _ in range(11):
            await _make_event(db_session_factory, c_id, c_run, "violation", permitted=False)

        async with db_session_factory() as db:
            result = await calculate_system_trust_score(db)

        dist = result["trust_distribution"]
        assert dist["TRUSTED"] >= 1
        total = sum(dist.values())
        assert total == 3

    @pytest.mark.asyncio
    async def test_average_is_mean_of_all_agent_scores(self, db_session_factory):
        a_id = await _make_agent(db_session_factory, "sys-avg-a")
        b_id = await _make_agent(db_session_factory, "sys-avg-b")
        # Agent A: clean (100)
        # Agent B: 1 gov violation (95)
        b_run = await _make_run(db_session_factory, b_id)
        await _make_event(db_session_factory, b_id, b_run, "violation", permitted=False)

        async with db_session_factory() as db:
            result = await calculate_system_trust_score(db)

        assert abs(result["average_trust_score"] - 97.5) < 0.1

    @pytest.mark.asyncio
    async def test_get_trust_distribution_delegates(self, db_session_factory):
        """get_trust_distribution() is a wrapper — verify it returns same shape."""
        await _make_agent(db_session_factory, "dist-wrapper-bot")
        async with db_session_factory() as db:
            result = await get_trust_distribution(db)
        assert "average_trust_score" in result
        assert "trust_distribution"  in result


# ═══════════════════════════════════════════════════════════════
# Integration tests — get_agent_trust_breakdown
# ═══════════════════════════════════════════════════════════════

class TestGetAgentTrustBreakdown:

    @pytest.mark.asyncio
    async def test_returns_agent_name(self, db_session_factory):
        agent_id = await _make_agent(db_session_factory, "breakdown-bot")
        async with db_session_factory() as db:
            result = await get_agent_trust_breakdown(db, agent_id)
        assert result["agent_name"] == "breakdown-bot"

    @pytest.mark.asyncio
    async def test_raises_value_error_for_unknown_agent(self, db_session_factory):
        async with db_session_factory() as db:
            with pytest.raises(ValueError, match="not found"):
                await get_agent_trust_breakdown(db, str(uuid.uuid4()))

    @pytest.mark.asyncio
    async def test_breakdown_dict_present(self, db_session_factory):
        agent_id = await _make_agent(db_session_factory, "bd-detail-bot")
        async with db_session_factory() as db:
            result = await get_agent_trust_breakdown(db, agent_id)
        assert "breakdown" in result
        assert "final_score" in result["breakdown"]
        assert "trust_level" in result["breakdown"]


# ═══════════════════════════════════════════════════════════════
# HTTP integration tests
# ═══════════════════════════════════════════════════════════════

class TestTrustHTTPEndpoints:

    async def _make_db(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        from app.db.database import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return async_sessionmaker(engine, expire_on_commit=False)

    async def test_get_system_trust_requires_auth(self, client):
        r = await client.get("/analytics/trust")
        assert r.status_code == 401

    async def test_get_agent_trust_requires_auth(self, client, registered_agent):
        r = await client.get(f"/analytics/trust/{registered_agent['agent_id']}")
        assert r.status_code == 401

    async def test_get_system_trust_returns_200(self, client, registered_agent):
        r = await client.get("/analytics/trust", headers=registered_agent["headers"])
        assert r.status_code == 200

    async def test_get_system_trust_schema(self, client, registered_agent):
        r = await client.get("/analytics/trust", headers=registered_agent["headers"])
        body = r.json()
        assert "average_trust_score" in body
        assert "trust_distribution"  in body
        dist = body["trust_distribution"]
        for level in ("TRUSTED", "MONITORED", "WARNING", "HIGH_RISK"):
            assert level in dist

    async def test_get_system_trust_fresh_agent_scores_100(self, client, registered_agent):
        r = await client.get("/analytics/trust", headers=registered_agent["headers"])
        body = r.json()
        assert body["average_trust_score"] == 100.0
        assert body["trust_distribution"]["TRUSTED"] == 1

    async def test_get_agent_trust_returns_200(self, client, registered_agent):
        r = await client.get(
            f"/analytics/trust/{registered_agent['agent_id']}",
            headers=registered_agent["headers"],
        )
        assert r.status_code == 200

    async def test_get_agent_trust_schema(self, client, registered_agent):
        r = await client.get(
            f"/analytics/trust/{registered_agent['agent_id']}",
            headers=registered_agent["headers"],
        )
        body = r.json()
        for field in ["agent_id", "agent_name", "trust_score", "trust_level", "breakdown"]:
            assert field in body, f"Missing field: {field}"

    async def test_get_agent_trust_fresh_agent_100(self, client, registered_agent):
        r = await client.get(
            f"/analytics/trust/{registered_agent['agent_id']}",
            headers=registered_agent["headers"],
        )
        body = r.json()
        assert body["trust_score"] == 100.0
        assert body["trust_level"] == "TRUSTED"

    async def test_get_agent_trust_breakdown_present(self, client, registered_agent):
        r = await client.get(
            f"/analytics/trust/{registered_agent['agent_id']}",
            headers=registered_agent["headers"],
        )
        bd = r.json()["breakdown"]
        for key in ["base_score", "deductions", "additions", "final_score", "trust_level"]:
            assert key in bd

    async def test_get_agent_trust_not_found_returns_404(self, client, registered_agent):
        r = await client.get(
            f"/analytics/trust/{uuid.uuid4()}",
            headers=registered_agent["headers"],
        )
        assert r.status_code == 404

    async def test_get_agent_stats_includes_trust_fields(self, client, registered_agent):
        r = await client.get(
            f"/analytics/stats/{registered_agent['agent_id']}",
            headers=registered_agent["headers"],
        )
        assert r.status_code == 200
        body = r.json()
        assert "trust_score" in body, "trust_score missing from /analytics/stats/{id}"
        assert "trust_level" in body, "trust_level missing from /analytics/stats/{id}"
        assert body["trust_score"] == 100.0
        assert body["trust_level"] == "TRUSTED"

    async def test_get_system_stats_includes_trust_fields(self, client, registered_agent):
        r = await client.get("/analytics/stats", headers=registered_agent["headers"])
        assert r.status_code == 200
        body = r.json()
        assert "average_trust_score" in body, "average_trust_score missing from /analytics/stats"
        assert "trust_distribution"  in body, "trust_distribution missing from /analytics/stats"
        assert isinstance(body["average_trust_score"], (int, float))
        assert isinstance(body["trust_distribution"], dict)

    async def test_system_stats_trust_distribution_has_all_levels(self, client, registered_agent):
        r = await client.get("/analytics/stats", headers=registered_agent["headers"])
        dist = r.json()["trust_distribution"]
        for level in ("TRUSTED", "MONITORED", "WARNING", "HIGH_RISK"):
            assert level in dist

    async def test_two_agents_distribution_both_trusted(self, client, registered_agent):
        # Register a second agent
        r2 = await client.post("/agents/register", json={
            "name":          "second-trust-bot",
            "allowed_tools": [],
            "secret":        "second-trust-secret-99",
        })
        assert r2.status_code == 201

        r = await client.get("/analytics/trust", headers=registered_agent["headers"])
        body = r.json()
        assert body["trust_distribution"]["TRUSTED"] == 2
        assert body["average_trust_score"] == 100.0