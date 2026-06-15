"""
tests/test_policies.py
───────────────────────
Comprehensive tests for the Policy Engine (Phase 3).

Coverage:
  POST   /policies                              — creation, validation, duplicates
  GET    /policies                              — list, pagination, schema
  GET    /policies/{id}                         — single policy, 404
  POST   /policies/{id}/agents/{agent_id}       — assignment, duplicate, 404
  DELETE /policies/{id}/agents/{agent_id}       — removal, 404
  GET    /agents/{agent_id}/policies            — agent policy list

  Policy evaluation — tool_deny, prompt_guard, time_window, rate_limit
  Analytics         — total_policies, active_policies, total_policy_violations
  Existing tests    — all 165 existing tests must still pass

Mocking strategy for evaluation tests:
  Policy evaluation is synchronous rule checking (no LLM).
  We call policy_service.evaluate_policies_for_agent() directly
  against a seeded in-memory DB without going through HTTP,
  which keeps the tests fast and deterministic.

  For run-level tests (prompt_guard blocking the full run), we use
  the HTTP client with mocked _invoke_agent_sync (no Groq needed).
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent  import Agent
from app.models.policy import Policy, AgentPolicy
from app.services.policy_service import evaluate_policies_for_agent

pytestmark = pytest.mark.asyncio


# ── Seed helpers ──────────────────────────────────────────────────────────────

async def _register(client: AsyncClient, name: str, tools: list[str] | None = None) -> dict:
    r = await client.post("/agents/register", json={
        "name":          name,
        "allowed_tools": tools or ["calculator"],
        "secret":        "policy-test-secret-42",
    })
    assert r.status_code == 201, f"register failed: {r.text}"
    data = r.json()
    return {
        "agent_id": data["agent_id"],
        "headers":  {"Authorization": f"Bearer {data['access_token']}"},
    }


async def _create_policy(client: AsyncClient, hdrs: dict, **kwargs) -> dict:
    defaults = {
        "name":        f"test-policy-{uuid.uuid4().hex[:6]}",
        "rule_type":   "tool_deny",
        "rule_config": {"tool": "weather"},
        "severity":    "MEDIUM",
        "is_active":   True,
    }
    defaults.update(kwargs)
    r = await client.post("/policies", json=defaults, headers=hdrs)
    assert r.status_code == 201, f"create_policy failed: {r.text}"
    return r.json()


async def _seed_policy_direct(db: AsyncSession, **kwargs) -> Policy:
    """Insert a Policy directly into the test DB (bypasses HTTP)."""
    defaults = {
        "name":        f"direct-policy-{uuid.uuid4().hex[:6]}",
        "rule_type":   "tool_deny",
        "rule_config": {"tool": "weather"},
        "severity":    "MEDIUM",
        "is_active":   True,
    }
    defaults.update(kwargs)
    p = Policy(**defaults)
    db.add(p)
    await db.flush()
    await db.refresh(p)
    return p


async def _assign_direct(db: AsyncSession, agent_id: str, policy_id: str) -> AgentPolicy:
    """Assign a policy to an agent directly (bypasses HTTP)."""
    ap = AgentPolicy(agent_id=agent_id, policy_id=policy_id)
    db.add(ap)
    await db.flush()
    return ap


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def agent(client: AsyncClient) -> dict:
    return await _register(client, "policy-test-bot", ["calculator", "weather"])


@pytest_asyncio.fixture
async def policy(client: AsyncClient, agent: dict) -> dict:
    return await _create_policy(client, agent["headers"])


@pytest_asyncio.fixture
async def assigned(client: AsyncClient, agent: dict, policy: dict) -> dict:
    """Policy assigned to agent."""
    r = await client.post(
        f"/policies/{policy['id']}/agents/{agent['agent_id']}",
        headers=agent["headers"],
    )
    assert r.status_code == 201
    return {"agent": agent, "policy": policy, "assignment": r.json()}


# ══════════════════════════════════════════════════════════════
# POST /policies — creation
# ══════════════════════════════════════════════════════════════

class TestCreatePolicy:

    async def test_create_returns_201(self, client, agent):
        r = await client.post("/policies", json={
            "name":        "my-deny-policy",
            "rule_type":   "tool_deny",
            "rule_config": {"tool": "weather"},
            "severity":    "HIGH",
        }, headers=agent["headers"])
        assert r.status_code == 201

    async def test_create_response_schema(self, client, agent):
        r = await client.post("/policies", json={
            "name":        "schema-check-policy",
            "rule_type":   "tool_deny",
            "rule_config": {"tool": "weather"},
            "severity":    "LOW",
        }, headers=agent["headers"])
        body = r.json()
        for field in ["id", "name", "rule_type", "rule_config",
                      "severity", "is_active", "created_at", "agent_count"]:
            assert field in body, f"Missing field: {field}"

    async def test_create_all_rule_types(self, client, agent):
        cases = [
            ("tool_allow",  {"tool": "calculator"}),
            ("tool_deny",   {"tool": "weather"}),
            ("rate_limit",  {"max_calls_per_run": 5}),
            ("prompt_guard",{"blocked_keywords": ["password"]}),
            ("time_window", {"start_hour": 8, "end_hour": 20}),
        ]
        for i, (rt, rc) in enumerate(cases):
            r = await client.post("/policies", json={
                "name":        f"rule-type-test-{i}",
                "rule_type":   rt,
                "rule_config": rc,
                "severity":    "MEDIUM",
            }, headers=agent["headers"])
            assert r.status_code == 201, f"Failed for rule_type={rt}: {r.text}"
            assert r.json()["rule_type"] == rt

    async def test_create_all_severities(self, client, agent):
        for sev in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]:
            r = await client.post("/policies", json={
                "name":        f"severity-test-{sev.lower()}",
                "rule_type":   "tool_deny",
                "rule_config": {"tool": "weather"},
                "severity":    sev,
            }, headers=agent["headers"])
            assert r.status_code == 201, f"Failed for severity={sev}"
            assert r.json()["severity"] == sev

    async def test_create_requires_auth(self, client):
        r = await client.post("/policies", json={
            "name": "no-auth", "rule_type": "tool_deny",
            "rule_config": {"tool": "x"},
        })
        assert r.status_code == 401

    async def test_create_duplicate_name_returns_409(self, client, policy, agent):
        r = await client.post("/policies", json={
            "name":        policy["name"],
            "rule_type":   "tool_deny",
            "rule_config": {"tool": "weather"},
        }, headers=agent["headers"])
        assert r.status_code == 409

    async def test_create_invalid_rule_type_returns_422(self, client, agent):
        r = await client.post("/policies", json={
            "name":        "bad-rule-type",
            "rule_type":   "unknown_type",
            "rule_config": {},
        }, headers=agent["headers"])
        assert r.status_code == 422

    async def test_create_invalid_severity_returns_422(self, client, agent):
        r = await client.post("/policies", json={
            "name":        "bad-severity",
            "rule_type":   "tool_deny",
            "rule_config": {"tool": "weather"},
            "severity":    "EXTREME",
        }, headers=agent["headers"])
        assert r.status_code == 422

    async def test_create_tool_deny_missing_tool_key_returns_422(self, client, agent):
        r = await client.post("/policies", json={
            "name":        "bad-config",
            "rule_type":   "tool_deny",
            "rule_config": {},   # missing "tool" key
        }, headers=agent["headers"])
        assert r.status_code == 422

    async def test_create_rate_limit_bad_config_returns_422(self, client, agent):
        r = await client.post("/policies", json={
            "name":        "bad-rate",
            "rule_type":   "rate_limit",
            "rule_config": {"max_calls_per_run": -1},
        }, headers=agent["headers"])
        assert r.status_code == 422

    async def test_create_prompt_guard_empty_keywords_returns_422(self, client, agent):
        r = await client.post("/policies", json={
            "name":        "bad-guard",
            "rule_type":   "prompt_guard",
            "rule_config": {"blocked_keywords": []},
        }, headers=agent["headers"])
        assert r.status_code == 422

    async def test_create_time_window_bad_hours_returns_422(self, client, agent):
        r = await client.post("/policies", json={
            "name":        "bad-window",
            "rule_type":   "time_window",
            "rule_config": {"start_hour": 18, "end_hour": 9},  # start > end
        }, headers=agent["headers"])
        assert r.status_code == 422


# ══════════════════════════════════════════════════════════════
# GET /policies
# ══════════════════════════════════════════════════════════════

class TestListPolicies:

    async def test_requires_auth(self, client):
        r = await client.get("/policies")
        assert r.status_code == 401

    async def test_empty_returns_200(self, client, agent):
        r = await client.get("/policies", headers=agent["headers"])
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 0
        assert body["policies"] == []

    async def test_returns_created_policy(self, client, policy, agent):
        r = await client.get("/policies", headers=agent["headers"])
        assert r.json()["total"] >= 1

    async def test_schema_fields_present(self, client, policy, agent):
        r = await client.get("/policies", headers=agent["headers"])
        for field in ["policies", "total", "skip", "limit"]:
            assert field in r.json()

    async def test_pagination_limit(self, client, agent):
        for i in range(3):
            await _create_policy(client, agent["headers"], name=f"page-test-{i}")
        r = await client.get("/policies?limit=2", headers=agent["headers"])
        assert len(r.json()["policies"]) <= 2
        assert r.json()["total"] >= 3


# ══════════════════════════════════════════════════════════════
# GET /policies/{policy_id}
# ══════════════════════════════════════════════════════════════

class TestGetPolicy:

    async def test_returns_200(self, client, policy, agent):
        r = await client.get(f"/policies/{policy['id']}", headers=agent["headers"])
        assert r.status_code == 200
        assert r.json()["id"] == policy["id"]

    async def test_not_found_returns_404(self, client, agent):
        r = await client.get(f"/policies/{uuid.uuid4()}", headers=agent["headers"])
        assert r.status_code == 404

    async def test_requires_auth(self, client, policy):
        r = await client.get(f"/policies/{policy['id']}")
        assert r.status_code == 401


# ══════════════════════════════════════════════════════════════
# POST /policies/{id}/agents/{agent_id}
# ══════════════════════════════════════════════════════════════

class TestAssignPolicy:

    async def test_assign_returns_201(self, client, agent, policy):
        r = await client.post(
            f"/policies/{policy['id']}/agents/{agent['agent_id']}",
            headers=agent["headers"],
        )
        assert r.status_code == 201

    async def test_assign_response_schema(self, client, agent, policy):
        r = await client.post(
            f"/policies/{policy['id']}/agents/{agent['agent_id']}",
            headers=agent["headers"],
        )
        body = r.json()
        for field in ["id", "agent_id", "policy_id", "created_at"]:
            assert field in body

    async def test_assign_agent_name_enriched(self, client, agent, policy):
        r = await client.post(
            f"/policies/{policy['id']}/agents/{agent['agent_id']}",
            headers=agent["headers"],
        )
        assert r.json()["agent_name"] == "policy-test-bot"

    async def test_assign_duplicate_returns_409(self, client, assigned):
        agent  = assigned["agent"]
        policy = assigned["policy"]
        r = await client.post(
            f"/policies/{policy['id']}/agents/{agent['agent_id']}",
            headers=agent["headers"],
        )
        assert r.status_code == 409

    async def test_assign_bad_policy_returns_404(self, client, agent):
        r = await client.post(
            f"/policies/{uuid.uuid4()}/agents/{agent['agent_id']}",
            headers=agent["headers"],
        )
        assert r.status_code == 404

    async def test_assign_bad_agent_returns_404(self, client, agent, policy):
        r = await client.post(
            f"/policies/{policy['id']}/agents/{uuid.uuid4()}",
            headers=agent["headers"],
        )
        assert r.status_code == 404

    async def test_assign_requires_auth(self, client, agent, policy):
        r = await client.post(
            f"/policies/{policy['id']}/agents/{agent['agent_id']}"
        )
        assert r.status_code == 401


# ══════════════════════════════════════════════════════════════
# DELETE /policies/{id}/agents/{agent_id}
# ══════════════════════════════════════════════════════════════

class TestRemovePolicy:

    async def test_remove_returns_204(self, client, assigned):
        agent  = assigned["agent"]
        policy = assigned["policy"]
        r = await client.delete(
            f"/policies/{policy['id']}/agents/{agent['agent_id']}",
            headers=agent["headers"],
        )
        assert r.status_code == 204

    async def test_remove_nonexistent_returns_404(self, client, agent, policy):
        r = await client.delete(
            f"/policies/{policy['id']}/agents/{agent['agent_id']}",
            headers=agent["headers"],
        )
        assert r.status_code == 404

    async def test_remove_requires_auth(self, client, assigned):
        r = await client.delete(
            f"/policies/{assigned['policy']['id']}/agents/{assigned['agent']['agent_id']}"
        )
        assert r.status_code == 401

    async def test_remove_then_reassign_succeeds(self, client, assigned):
        agent  = assigned["agent"]
        policy = assigned["policy"]
        # Remove
        await client.delete(
            f"/policies/{policy['id']}/agents/{agent['agent_id']}",
            headers=agent["headers"],
        )
        # Re-assign
        r = await client.post(
            f"/policies/{policy['id']}/agents/{agent['agent_id']}",
            headers=agent["headers"],
        )
        assert r.status_code == 201


# ══════════════════════════════════════════════════════════════
# GET /agents/{agent_id}/policies
# ══════════════════════════════════════════════════════════════

class TestListAgentPolicies:

    async def test_requires_auth(self, client, agent):
        r = await client.get(f"/agents/{agent['agent_id']}/policies")
        assert r.status_code == 401

    async def test_empty_before_assignment(self, client, agent):
        r = await client.get(
            f"/agents/{agent['agent_id']}/policies",
            headers=agent["headers"],
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0
        assert r.json()["policies"] == []

    async def test_returns_assigned_policy(self, client, assigned):
        agent  = assigned["agent"]
        policy = assigned["policy"]
        r = await client.get(
            f"/agents/{agent['agent_id']}/policies",
            headers=agent["headers"],
        )
        assert r.json()["total"] == 1
        assert r.json()["policies"][0]["id"] == policy["id"]

    async def test_bad_agent_returns_404(self, client, agent):
        r = await client.get(
            f"/agents/{uuid.uuid4()}/policies",
            headers=agent["headers"],
        )
        assert r.status_code == 404

    async def test_schema_fields_present(self, client, assigned):
        agent = assigned["agent"]
        r = await client.get(
            f"/agents/{agent['agent_id']}/policies",
            headers=agent["headers"],
        )
        for field in ["policies", "total", "agent_id"]:
            assert field in r.json()


# ══════════════════════════════════════════════════════════════
# Policy evaluation unit tests (direct service calls)
# ══════════════════════════════════════════════════════════════

class TestPolicyEvaluation:
    """
    Direct calls to evaluate_policies_for_agent() against a real in-memory DB.
    No HTTP, no LLM, no GROQ_API_KEY needed.
    """

    @pytest_asyncio.fixture
    async def eval_db(self, db_session_factory):
        """Yield a fresh session from the per-test factory."""
        async with db_session_factory() as session:
            yield session

    async def _seed_agent(self, db, name="eval-bot"):
        from app.auth.hashing import hash_secret
        agent = Agent(
            id=str(uuid.uuid4()),
            name=name,
            allowed_tools=["calculator"],
            hashed_secret=hash_secret("test-secret-99"),
        )
        db.add(agent)
        await db.flush()
        return agent

    async def test_no_policies_returns_clean_result(self, eval_db):
        agent = await self._seed_agent(eval_db)
        result = await evaluate_policies_for_agent(eval_db, agent.id, "hello world")
        assert result.run_blocked is False
        assert result.violation is None
        assert len(result.blocked_tools) == 0
        assert result.rate_limit is None

    async def test_tool_deny_adds_to_blocked_set(self, eval_db):
        agent  = await self._seed_agent(eval_db)
        policy = await _seed_policy_direct(
            eval_db, rule_type="tool_deny", rule_config={"tool": "weather"}
        )
        await _assign_direct(eval_db, agent.id, policy.id)
        await eval_db.flush()

        result = await evaluate_policies_for_agent(eval_db, agent.id, "what is the weather?")
        assert "weather" in result.blocked_tools
        assert result.run_blocked is False

    async def test_tool_allow_adds_to_explicitly_allowed(self, eval_db):
        agent  = await self._seed_agent(eval_db)
        policy = await _seed_policy_direct(
            eval_db, rule_type="tool_allow", rule_config={"tool": "calculator"}
        )
        await _assign_direct(eval_db, agent.id, policy.id)
        await eval_db.flush()

        result = await evaluate_policies_for_agent(eval_db, agent.id, "calculate something")
        assert "calculator" in result.explicitly_allowed
        assert result.run_blocked is False

    async def test_prompt_guard_blocks_run(self, eval_db):
        agent  = await self._seed_agent(eval_db)
        policy = await _seed_policy_direct(
            eval_db,
            rule_type="prompt_guard",
            rule_config={"blocked_keywords": ["password", "secret"]},
            severity="HIGH",
        )
        await _assign_direct(eval_db, agent.id, policy.id)
        await eval_db.flush()

        result = await evaluate_policies_for_agent(
            eval_db, agent.id, "what is my password?"
        )
        assert result.run_blocked is True
        assert result.violation is not None
        assert result.violation.rule_type == "prompt_guard"
        assert result.violation.severity  == "HIGH"
        assert "password" in result.violation.reason

    async def test_prompt_guard_allows_clean_prompt(self, eval_db):
        agent  = await self._seed_agent(eval_db)
        policy = await _seed_policy_direct(
            eval_db,
            rule_type="prompt_guard",
            rule_config={"blocked_keywords": ["password"]},
        )
        await _assign_direct(eval_db, agent.id, policy.id)
        await eval_db.flush()

        result = await evaluate_policies_for_agent(eval_db, agent.id, "what is 2 + 2?")
        assert result.run_blocked is False

    async def test_prompt_guard_case_insensitive(self, eval_db):
        agent  = await self._seed_agent(eval_db)
        policy = await _seed_policy_direct(
            eval_db,
            rule_type="prompt_guard",
            rule_config={"blocked_keywords": ["PASSWORD"]},
        )
        await _assign_direct(eval_db, agent.id, policy.id)
        await eval_db.flush()

        result = await evaluate_policies_for_agent(
            eval_db, agent.id, "give me the password"
        )
        assert result.run_blocked is True

    async def test_time_window_blocks_outside_hours(self, eval_db):
        agent  = await self._seed_agent(eval_db)
        # Set a window that definitely excludes all hours (start=end-1 at midnight)
        # We mock the hour check by using start=0, end=0 which is invalid, so
        # use a window that is always outside: start=23, end=24 and mock hour=0
        policy = await _seed_policy_direct(
            eval_db,
            rule_type="time_window",
            rule_config={"start_hour": 23, "end_hour": 24},
        )
        await _assign_direct(eval_db, agent.id, policy.id)
        await eval_db.flush()

        # Mock the current UTC hour to be 12 (outside 23-24 window)
        with patch(
            "app.services.policy_service.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 12, 0, 0,
                                                 tzinfo=timezone.utc)
            mock_dt.now.side_effect = lambda tz=None: datetime(
                2024, 1, 15, 12, 0, 0, tzinfo=tz
            )
            result = await evaluate_policies_for_agent(
                eval_db, agent.id, "hello"
            )
        assert result.run_blocked is True
        assert result.violation.rule_type == "time_window"

    async def test_time_window_allows_within_hours(self, eval_db):
        agent  = await self._seed_agent(eval_db)
        policy = await _seed_policy_direct(
            eval_db,
            rule_type="time_window",
            rule_config={"start_hour": 0, "end_hour": 23},  # almost all day
        )
        await _assign_direct(eval_db, agent.id, policy.id)
        await eval_db.flush()

        result = await evaluate_policies_for_agent(eval_db, agent.id, "hello")
        assert result.run_blocked is False

    async def test_rate_limit_stored_in_result(self, eval_db):
        agent  = await self._seed_agent(eval_db)
        policy = await _seed_policy_direct(
            eval_db,
            rule_type="rate_limit",
            rule_config={"max_calls_per_run": 3},
        )
        await _assign_direct(eval_db, agent.id, policy.id)
        await eval_db.flush()

        result = await evaluate_policies_for_agent(eval_db, agent.id, "do something")
        assert result.rate_limit == 3
        assert result.run_blocked is False

    async def test_most_restrictive_rate_limit_wins(self, eval_db):
        agent = await self._seed_agent(eval_db)
        p1 = await _seed_policy_direct(
            eval_db, rule_type="rate_limit", rule_config={"max_calls_per_run": 5}
        )
        p2 = await _seed_policy_direct(
            eval_db, rule_type="rate_limit", rule_config={"max_calls_per_run": 2}
        )
        await _assign_direct(eval_db, agent.id, p1.id)
        await _assign_direct(eval_db, agent.id, p2.id)
        await eval_db.flush()

        result = await evaluate_policies_for_agent(eval_db, agent.id, "do something")
        assert result.rate_limit == 2  # most restrictive wins

    async def test_inactive_policy_not_evaluated(self, eval_db):
        agent  = await self._seed_agent(eval_db)
        policy = await _seed_policy_direct(
            eval_db,
            rule_type="prompt_guard",
            rule_config={"blocked_keywords": ["password"]},
            is_active=False,  # inactive
        )
        await _assign_direct(eval_db, agent.id, policy.id)
        await eval_db.flush()

        result = await evaluate_policies_for_agent(
            eval_db, agent.id, "what is my password?"
        )
        assert result.run_blocked is False  # inactive policy ignored


# ══════════════════════════════════════════════════════════════
# Integration: policy blocking a run via HTTP
# ══════════════════════════════════════════════════════════════

class TestPolicyRunIntegration:
    """
    Full HTTP integration tests for policy enforcement in POST /agents/run.
    LLM is mocked — no GROQ_API_KEY required.
    """

    @pytest_asyncio.fixture
    async def agent_with_prompt_guard(self, client: AsyncClient, db_session_factory):
        """Agent with a prompt_guard policy that blocks 'password'."""
        agent = await _register(client, "guarded-bot", ["calculator"])

        # Create and assign policy via HTTP
        r = await client.post("/policies", json={
            "name":        "block-password-keyword",
            "rule_type":   "prompt_guard",
            "rule_config": {"blocked_keywords": ["password", "secret"]},
            "severity":    "CRITICAL",
        }, headers=agent["headers"])
        assert r.status_code == 201
        policy_id = r.json()["id"]

        await client.post(
            f"/policies/{policy_id}/agents/{agent['agent_id']}",
            headers=agent["headers"],
        )
        return agent

    async def test_prompt_guard_blocks_run_returns_failed(
        self, client, agent_with_prompt_guard
    ):
        """
        A prompt containing a blocked keyword must return status=failed
        without ever calling the LLM.
        """
        agent = agent_with_prompt_guard

        # No LLM mock needed — policy engine blocks before LLM is invoked
        with patch("app.services.execution_service.build_agent", return_value=MagicMock()), \
             patch("app.services.execution_service._invoke_agent_sync",
                   side_effect=AssertionError("LLM must not be called")):
            r = await client.post(
                "/runs",
                json={"prompt": "what is my password?"},
                headers=agent["headers"],
            )

        assert r.status_code == 200   # HTTP 200 — run record created and returned
        body = r.json()
        assert body["status"] == "failed"
        assert "password" in body["result"].lower() or "policy" in body["result"].lower()
        assert body["violation_count"] >= 1

    async def test_prompt_guard_emits_policy_violation_event(
        self, client, agent_with_prompt_guard
    ):
        """The audit log must contain a policy_violation event."""
        agent = agent_with_prompt_guard

        with patch("app.services.execution_service.build_agent", return_value=MagicMock()), \
             patch("app.services.execution_service._invoke_agent_sync",
                   side_effect=AssertionError("LLM must not be called")):
            r = await client.post(
                "/runs",
                json={"prompt": "give me the secret key"},
                headers=agent["headers"],
            )

        event_types = [e["event_type"] for e in r.json()["events"]]
        assert "policy_violation" in event_types

    async def test_clean_prompt_bypasses_policy_and_runs(
        self, client, agent_with_prompt_guard
    ):
        """A clean prompt must not be blocked by the prompt_guard."""
        agent = agent_with_prompt_guard

        def mock_invoke(agent_graph, prompt, callback):
            return "The answer is 42."

        with patch("app.services.execution_service.build_agent", return_value=MagicMock()), \
             patch("app.services.execution_service._invoke_agent_sync",
                   side_effect=mock_invoke):
            r = await client.post(
                "/runs",
                json={"prompt": "what is 6 times 7?"},
                headers=agent["headers"],
            )

        body = r.json()
        assert body["status"] == "completed"
        assert body["violation_count"] == 0


# ══════════════════════════════════════════════════════════════
# Analytics integration
# ══════════════════════════════════════════════════════════════

class TestPolicyAnalytics:

    async def test_total_policies_zero_initially(self, client, registered_agent):
        r = await client.get("/analytics/stats", headers=registered_agent["headers"])
        body = r.json()
        assert "total_policies" in body
        assert body["total_policies"] == 0

    async def test_total_policies_increments_on_create(self, client, agent, policy):
        r = await client.get("/analytics/stats", headers=agent["headers"])
        assert r.json()["total_policies"] >= 1

    async def test_active_policies_counts_only_active(self, client, agent):
        # Create one active and one inactive
        await _create_policy(client, agent["headers"],
                             name="active-one", is_active=True)
        await _create_policy(client, agent["headers"],
                             name="inactive-one", is_active=False)

        r = await client.get("/analytics/stats", headers=agent["headers"])
        body = r.json()
        assert body["total_policies"] >= 2
        assert body["active_policies"] >= 1
        assert body["active_policies"] < body["total_policies"]

    async def test_analytics_has_all_policy_fields(self, client, registered_agent):
        r = await client.get("/analytics/stats", headers=registered_agent["headers"])
        body = r.json()
        for field in ["total_policies", "active_policies",
                      "total_policy_violations", "violations_by_severity"]:
            assert field in body, f"Missing analytics field: {field}"

    async def test_existing_analytics_fields_unchanged(self, client, registered_agent):
        r = await client.get("/analytics/stats", headers=registered_agent["headers"])
        body = r.json()
        for field in ["total_agents", "total_runs", "total_events",
                      "total_violations", "violation_rate",
                      "total_interactions", "tool_latency"]:
            assert field in body, f"Existing analytics field missing: {field}"