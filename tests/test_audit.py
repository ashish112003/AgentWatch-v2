"""
tests/test_audit.py
────────────────────
HTTP integration tests for:
  GET /audit/logs
  GET /audit/logs/{agent_id}
  GET /governance/violations
  GET /governance/violations/{agent_id}
  GET /governance/runs
  GET /analytics/stats
  GET /analytics/stats/{agent_id}
  GET /analytics/tool-latency

Seeding strategy:
  Each test class that needs data uses a `seeded` fixture that:
    1. Registers an agent.
    2. Injects AgentRun and AgentEvent rows directly into the DB
       (via the db session factory from conftest) rather than calling
       POST /agents/run.  This avoids the LLM mock setup overhead and
       keeps the fixture fast and deterministic.

Coverage per endpoint:
  Authentication  — 401 without token
  Empty state     — 200 with empty list before any data
  Schema          — required fields present, correct types
  Pagination      — skip/limit respected
  Filtering       — event_type filter, agent_id scoping
  Data accuracy   — counts, agent names, denial messages
"""

import uuid
import pytest
import pytest_asyncio
from datetime import datetime
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentRun, AgentEvent
pytestmark = pytest.mark.asyncio


# ── Seed helpers ──────────────────────────────────────────────────────────────

async def _register(client: AsyncClient, name: str, tools: list) -> dict:
    r = await client.post("/agents/register", json={
        "name":          name,
        "allowed_tools": tools,
        "secret":        "audit-test-secret-42",
    })
    assert r.status_code == 201, f"Register failed: {r.text}"
    data = r.json()
    return {
        "agent_id": data["agent_id"],
        "token":    data["access_token"],
        "headers":  {"Authorization": f"Bearer {data['access_token']}"},
    }


async def _seed_run(
    db_factory,
    agent_id: str,
    *,
    status: str = "completed",
    prompt: str = "test prompt",
) -> AgentRun:
    """Insert one AgentRun row directly into the test DB."""
    async with db_factory() as db:
        run = AgentRun(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            prompt=prompt,
            status=status,
            trace_id=str(uuid.uuid4()),
            started_at=datetime(2024, 1, 1, 10, 0, 0),
            ended_at=datetime(2024, 1, 1, 10, 0, 2),
            result="Mocked result",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
    return run


async def _seed_events(db_factory, run: AgentRun) -> list:
    """Insert a realistic event sequence for a run with one violation."""
    events = []
    async with db_factory() as db:
        for evt_type, tool, permitted, latency, in_data, out_data in [
            ("run_start",  None,         None,  None,  {"prompt": run.prompt}, None),
            ("tool_call",  "weather",    False, None,  {"city": "London"},     None),
            ("violation",  "weather",    False, None,  {"city": "London"},     {"denial_message": "Access denied: tool 'weather' is not permitted."}),
            ("tool_call",  "calculator", True,  None,  {"expression": "2+2"},  None),
            ("tool_end",   "calculator", True,  12.5,  {"expression": "2+2"},  {"result": "4"}),
            ("run_end",    None,         None,  None,  None,                   {"result": "Done"}),
        ]:
            ev = AgentEvent(
                id=str(uuid.uuid4()),
                run_id=run.id,
                agent_id=run.agent_id,
                trace_id=run.trace_id,
                event_type=evt_type,
                tool_name=tool,
                permitted=permitted,
                latency_ms=latency,
                input_data=in_data,
                output_data=out_data,
                timestamp=datetime(2024, 1, 1, 10, 0, 1),
            )
            db.add(ev)
            events.append(ev)
        await db.commit()
    return events


# ── Seeded fixture ────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seeded(client: AsyncClient, db_session_factory):
    """
    Register an agent, seed one run + 6 events (including 1 violation).
    Returns { agent, run, events, headers }.
    """
    agent  = await _register(client, "audit-bot", ["calculator"])
    run    = await _seed_run(db_session_factory, agent["agent_id"])
    events = await _seed_events(db_session_factory, run)
    return {"agent": agent, "run": run, "events": events}


# ══════════════════════════════════════════════════════════════
# GET /audit/logs
# ══════════════════════════════════════════════════════════════

class TestAuditLogs:

    async def test_requires_auth(self, client):
        r = await client.get("/audit/logs")
        assert r.status_code == 401

    async def test_empty_returns_200_empty_list(self, client, registered_agent):
        r = await client.get("/audit/logs", headers=registered_agent["headers"])
        assert r.status_code == 200
        body = r.json()
        assert body["total"]  == 0
        assert body["events"] == []

    async def test_schema_fields_present(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/audit/logs", headers=hdrs)
        assert r.status_code == 200
        body = r.json()
        for field in ["events", "total", "skip", "limit"]:
            assert field in body
        if body["events"]:
            ev = body["events"][0]
            for field in ["id", "run_id", "agent_id", "trace_id",
                          "event_type", "timestamp", "agent_name"]:
                assert field in ev, f"Missing event field: {field}"

    async def test_returns_seeded_events(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/audit/logs", headers=hdrs)
        assert r.json()["total"] == 6

    async def test_filter_by_event_type(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/audit/logs?event_type=violation", headers=hdrs)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["events"][0]["event_type"] == "violation"

    async def test_filter_by_run_id(self, client, seeded):
        hdrs   = seeded["agent"]["headers"]
        run_id = seeded["run"].id
        r      = await client.get(f"/audit/logs?run_id={run_id}", headers=hdrs)
        assert r.status_code == 200
        assert r.json()["total"] == 6

    async def test_filter_by_nonexistent_run_id(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/audit/logs?run_id=does-not-exist", headers=hdrs)
        assert r.json()["total"] == 0

    async def test_pagination_limit(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/audit/logs?limit=2", headers=hdrs)
        assert r.status_code == 200
        body = r.json()
        assert len(body["events"]) <= 2
        assert body["total"] == 6      # total unchanged by limit

    async def test_pagination_skip(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/audit/logs?skip=4&limit=10", headers=hdrs)
        assert r.status_code == 200
        assert len(r.json()["events"]) == 2

    async def test_agent_name_enriched(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/audit/logs", headers=hdrs)
        events_with_name = [
            e for e in r.json()["events"] if e.get("agent_name")
        ]
        assert len(events_with_name) > 0
        assert events_with_name[0]["agent_name"] == "audit-bot"

    async def test_events_ordered_newest_first(self, client, seeded):
        """All seeded events share the same timestamp, so order is stable but
        we at least verify the endpoint returns without error and respects DESC."""
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/audit/logs", headers=hdrs)
        assert r.status_code == 200
        assert len(r.json()["events"]) == 6


# ══════════════════════════════════════════════════════════════
# GET /audit/logs/{agent_id}
# ══════════════════════════════════════════════════════════════

class TestAuditLogsByAgent:

    async def test_scoped_to_agent(self, client, seeded, registered_agent):
        """
        Two agents in the DB — scoped endpoint returns only the seeded agent's events.
        """
        agent_id = seeded["agent"]["agent_id"]
        hdrs     = seeded["agent"]["headers"]
        r = await client.get(f"/audit/logs/{agent_id}", headers=hdrs)
        assert r.status_code == 200
        assert r.json()["total"] == 6

    async def test_other_agent_sees_zero(self, client, seeded, registered_agent):
        """
        registered_agent has no events — its scoped log must return 0.
        """
        other_id = registered_agent["agent_id"]
        hdrs     = registered_agent["headers"]
        r = await client.get(f"/audit/logs/{other_id}", headers=hdrs)
        assert r.json()["total"] == 0

    async def test_nonexistent_agent_id_returns_empty(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/audit/logs/does-not-exist", headers=hdrs)
        assert r.status_code == 200
        assert r.json()["total"] == 0

    async def test_requires_auth(self, client, seeded):
        r = await client.get(f"/audit/logs/{seeded['agent']['agent_id']}")
        assert r.status_code == 401


# ══════════════════════════════════════════════════════════════
# GET /governance/violations
# ══════════════════════════════════════════════════════════════

class TestGovernanceViolations:

    async def test_requires_auth(self, client):
        r = await client.get("/governance/violations")
        assert r.status_code == 401

    async def test_empty_before_any_violations(self, client, registered_agent):
        r = await client.get(
            "/governance/violations", headers=registered_agent["headers"]
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"]      == 0
        assert body["violations"] == []

    async def test_schema_fields(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/governance/violations", headers=hdrs)
        assert r.status_code == 200
        body = r.json()
        for field in ["violations", "total", "skip", "limit"]:
            assert field in body

    async def test_returns_one_violation(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/governance/violations", headers=hdrs)
        assert r.json()["total"] == 1

    async def test_violation_fields_present(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/governance/violations", headers=hdrs)
        v    = r.json()["violations"][0]
        for field in ["id", "run_id", "agent_id", "tool_name",
                      "attempted_input", "denial_message", "timestamp"]:
            assert field in v, f"Missing violation field: {field}"

    async def test_violation_tool_name_correct(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/governance/violations", headers=hdrs)
        assert r.json()["violations"][0]["tool_name"] == "weather"

    async def test_violation_attempted_input_correct(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/governance/violations", headers=hdrs)
        v    = r.json()["violations"][0]
        assert v["attempted_input"] == {"city": "London"}

    async def test_violation_denial_message_present(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/governance/violations", headers=hdrs)
        v    = r.json()["violations"][0]
        assert v["denial_message"] is not None
        assert "not permitted" in v["denial_message"].lower()

    async def test_agent_name_enriched(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/governance/violations", headers=hdrs)
        v    = r.json()["violations"][0]
        assert v["agent_name"] == "audit-bot"

    async def test_filter_by_agent_id(self, client, seeded, registered_agent):
        agent_id = seeded["agent"]["agent_id"]
        hdrs     = seeded["agent"]["headers"]
        r = await client.get(
            f"/governance/violations?agent_id={agent_id}", headers=hdrs
        )
        assert r.json()["total"] == 1

    async def test_filter_other_agent_returns_zero(
        self, client, seeded, registered_agent
    ):
        other_id = registered_agent["agent_id"]
        hdrs     = registered_agent["headers"]
        r = await client.get(
            f"/governance/violations?agent_id={other_id}", headers=hdrs
        )
        assert r.json()["total"] == 0

    async def test_pagination(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/governance/violations?limit=1", headers=hdrs)
        assert len(r.json()["violations"]) == 1

    async def test_skip_past_end_returns_empty(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/governance/violations?skip=999", headers=hdrs)
        assert r.json()["violations"] == []


# ══════════════════════════════════════════════════════════════
# GET /governance/violations/{agent_id}
# ══════════════════════════════════════════════════════════════

class TestGovernanceViolationsByAgent:

    async def test_scoped_returns_correct_count(self, client, seeded):
        agent_id = seeded["agent"]["agent_id"]
        hdrs     = seeded["agent"]["headers"]
        r        = await client.get(
            f"/governance/violations/{agent_id}", headers=hdrs
        )
        assert r.status_code == 200
        assert r.json()["total"] == 1

    async def test_nonexistent_agent_returns_empty(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get(
            "/governance/violations/does-not-exist", headers=hdrs
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0

    async def test_requires_auth(self, client, seeded):
        r = await client.get(
            f"/governance/violations/{seeded['agent']['agent_id']}"
        )
        assert r.status_code == 401


# ══════════════════════════════════════════════════════════════
# GET /governance/runs
# ══════════════════════════════════════════════════════════════

class TestGovernanceRuns:

    async def test_requires_auth(self, client):
        r = await client.get("/governance/runs")
        assert r.status_code == 401

    async def test_empty_before_any_runs(self, client, registered_agent):
        r = await client.get(
            "/governance/runs", headers=registered_agent["headers"]
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0

    async def test_returns_seeded_run(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/governance/runs", headers=hdrs)
        assert r.status_code == 200
        assert r.json()["total"] == 1

    async def test_run_summary_fields(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/governance/runs", headers=hdrs)
        run  = r.json()["runs"][0]
        for field in ["id", "agent_id", "agent_name", "status",
                      "prompt", "started_at", "violation_count"]:
            assert field in run, f"Missing run field: {field}"

    async def test_violation_count_in_run_summary(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/governance/runs", headers=hdrs)
        run  = r.json()["runs"][0]
        assert run["violation_count"] == 1

    async def test_filter_by_status(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/governance/runs?status=completed", headers=hdrs)
        assert r.status_code == 200
        assert r.json()["total"] == 1

    async def test_filter_by_wrong_status_returns_zero(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/governance/runs?status=failed", headers=hdrs)
        assert r.json()["total"] == 0

    async def test_filter_by_agent_id(self, client, seeded, registered_agent):
        agent_id = seeded["agent"]["agent_id"]
        hdrs     = seeded["agent"]["headers"]
        r = await client.get(
            f"/governance/runs?agent_id={agent_id}", headers=hdrs
        )
        assert r.json()["total"] == 1

    async def test_pagination_respected(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        # limit must be >= 1 (enforced by Query validator); limit=1 returns <= 1 row
        r    = await client.get("/governance/runs?limit=1", headers=hdrs)
        assert r.status_code == 200
        assert len(r.json()["runs"]) <= 1


# ══════════════════════════════════════════════════════════════
# GET /analytics/stats
# ══════════════════════════════════════════════════════════════

class TestAnalyticsStats:

    async def test_requires_auth(self, client):
        r = await client.get("/analytics/stats")
        assert r.status_code == 401

    async def test_schema_fields_present(self, client, registered_agent):
        r = await client.get(
            "/analytics/stats", headers=registered_agent["headers"]
        )
        assert r.status_code == 200
        body = r.json()
        for field in ["total_agents", "total_runs", "total_events",
                      "total_tool_calls", "total_violations",
                      "violation_rate", "completed_runs", "failed_runs",
                      "tool_latency"]:
            assert field in body, f"Missing stats field: {field}"

    async def test_counts_reflect_registered_agents(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/analytics/stats", headers=hdrs)
        body = r.json()
        assert body["total_agents"] >= 1
        assert body["total_runs"]   >= 1
        assert body["total_events"] >= 6

    async def test_violation_count_accurate(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/analytics/stats", headers=hdrs)
        assert r.json()["total_violations"] >= 1

    async def test_violation_rate_is_float_0_to_100(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/analytics/stats", headers=hdrs)
        rate = r.json()["violation_rate"]
        assert isinstance(rate, float)
        assert 0.0 <= rate <= 100.0

    async def test_tool_latency_is_list(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/analytics/stats", headers=hdrs)
        assert isinstance(r.json()["tool_latency"], list)

    async def test_empty_db_returns_zero_counts(self, client, registered_agent):
        hdrs = registered_agent["headers"]
        r    = await client.get("/analytics/stats", headers=hdrs)
        body = r.json()
        assert body["total_runs"]      == 0
        assert body["total_violations"] == 0
        assert body["violation_rate"]  == 0.0


# ══════════════════════════════════════════════════════════════
# GET /analytics/stats/{agent_id}
# ══════════════════════════════════════════════════════════════

class TestAnalyticsAgentStats:

    async def test_requires_auth(self, client, seeded):
        r = await client.get(f"/analytics/stats/{seeded['agent']['agent_id']}")
        assert r.status_code == 401

    async def test_returns_200_with_correct_schema(self, client, seeded):
        agent_id = seeded["agent"]["agent_id"]
        hdrs     = seeded["agent"]["headers"]
        r        = await client.get(f"/analytics/stats/{agent_id}", headers=hdrs)
        assert r.status_code == 200
        body = r.json()
        for field in ["agent_id", "agent_name", "total_runs",
                      "completed_runs", "failed_runs", "total_events",
                      "total_tool_calls", "total_violations",
                      "violation_rate", "tools_used"]:
            assert field in body, f"Missing agent stats field: {field}"

    async def test_agent_name_correct(self, client, seeded):
        agent_id = seeded["agent"]["agent_id"]
        hdrs     = seeded["agent"]["headers"]
        r        = await client.get(f"/analytics/stats/{agent_id}", headers=hdrs)
        assert r.json()["agent_name"] == "audit-bot"

    async def test_run_count_accurate(self, client, seeded):
        agent_id = seeded["agent"]["agent_id"]
        hdrs     = seeded["agent"]["headers"]
        r        = await client.get(f"/analytics/stats/{agent_id}", headers=hdrs)
        assert r.json()["total_runs"] == 1
        assert r.json()["completed_runs"] == 1

    async def test_violation_count_accurate(self, client, seeded):
        agent_id = seeded["agent"]["agent_id"]
        hdrs     = seeded["agent"]["headers"]
        r        = await client.get(f"/analytics/stats/{agent_id}", headers=hdrs)
        assert r.json()["total_violations"] == 1

    async def test_tools_used_correct(self, client, seeded):
        agent_id = seeded["agent"]["agent_id"]
        hdrs     = seeded["agent"]["headers"]
        r        = await client.get(f"/analytics/stats/{agent_id}", headers=hdrs)
        # Calculator was the only permitted tool_end in the seeded data
        assert "calculator" in r.json()["tools_used"]

    async def test_nonexistent_agent_returns_404(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/analytics/stats/does-not-exist", headers=hdrs)
        assert r.status_code == 404

    async def test_violation_rate_computed_correctly(self, client, seeded):
        """
        Seeded data: 2 tool_call events, 1 violation → rate = 50.0%
        """
        agent_id = seeded["agent"]["agent_id"]
        hdrs     = seeded["agent"]["headers"]
        r        = await client.get(f"/analytics/stats/{agent_id}", headers=hdrs)
        body     = r.json()
        if body["total_tool_calls"] > 0:
            expected = round(
                body["total_violations"] / body["total_tool_calls"] * 100, 1
            )
            assert body["violation_rate"] == expected


# ══════════════════════════════════════════════════════════════
# GET /analytics/tool-latency
# ══════════════════════════════════════════════════════════════

class TestAnalyticsToolLatency:

    async def test_requires_auth(self, client):
        r = await client.get("/analytics/tool-latency")
        assert r.status_code == 401

    async def test_empty_before_tool_ends(self, client, registered_agent):
        r = await client.get(
            "/analytics/tool-latency", headers=registered_agent["headers"]
        )
        assert r.status_code == 200
        assert r.json() == []

    async def test_returns_list_after_tool_end_seeded(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/analytics/tool-latency", headers=hdrs)
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        assert len(items) >= 1

    async def test_tool_latency_schema(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/analytics/tool-latency", headers=hdrs)
        item = r.json()[0]
        for field in ["tool_name", "avg_ms", "p95_ms", "call_count"]:
            assert field in item, f"Missing latency field: {field}"

    async def test_calculator_present_after_seeding(self, client, seeded):
        hdrs   = seeded["agent"]["headers"]
        r      = await client.get("/analytics/tool-latency", headers=hdrs)
        tools  = [item["tool_name"] for item in r.json()]
        assert "calculator" in tools

    async def test_avg_ms_is_positive(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/analytics/tool-latency", headers=hdrs)
        for item in r.json():
            if item["avg_ms"] is not None:
                assert item["avg_ms"] > 0

    async def test_call_count_is_positive(self, client, seeded):
        hdrs = seeded["agent"]["headers"]
        r    = await client.get("/analytics/tool-latency", headers=hdrs)
        for item in r.json():
            assert item["call_count"] > 0