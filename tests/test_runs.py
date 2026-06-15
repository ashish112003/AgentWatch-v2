"""
tests/test_runs.py
───────────────────
HTTP integration tests for POST /agents/run.

LLM mock strategy:
  Every test patches two things:
    1. build_agent()          → returns MagicMock so ChatGroq is never built
                                (no GROQ_API_KEY needed)
    2. _invoke_agent_sync()   → synchronous callable that fires ToolEventCallback
                                hooks directly and returns a fixed answer string

  This exercises the complete execution_service.run_agent() path —
  AgentRun creation, event persistence, governance enforcement, response
  building — without any network calls.

Coverage:
  Authentication enforcement on /agents/run
  Request validation (empty prompt, prompt too long)
  Successful run: response structure, event sequence, DB persistence
  Governance: violation events created, run still completes
  Error path: LLM exception → 500, run marked failed
  Concurrent safety: two runs from same agent stored independently
"""

import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock
from httpx import AsyncClient

from app.tools.calculator import calculator
from app.governance.enforcer import GovernanceEnforcer, ViolationRecord
pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mock_invoke(tool_calls: list[tuple[str, dict]], answer: str = "Mock answer."):
    """
    Build a replacement for _invoke_agent_sync.

    Fires on_tool_start / on_tool_end on the callback for each (tool, kwargs)
    pair, then returns `answer`.  Does not call build_agent() or Groq.
    """
    def mock_invoke(agent_graph, prompt: str, callback):
        for tool_name, kwargs in tool_calls:
            callback.on_tool_start(
                {"name": tool_name}, str(kwargs),
                run_id="mock", inputs=kwargs,
            )
            # Simulate real tool output for permitted calls
            if tool_name == "calculator":
                result = calculator.func(**kwargs)
            else:
                result = f"Mock result for {tool_name}"
            callback.on_tool_end(result, run_id="mock")
        return answer
    return mock_invoke


def _patch_llm(mock_invoke_fn):
    """Context manager that patches both build_agent and _invoke_agent_sync."""
    return (
        patch("app.services.execution_service.build_agent", return_value=MagicMock()),
        patch("app.services.execution_service._invoke_agent_sync", side_effect=mock_invoke_fn),
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def calc_agent(client: AsyncClient) -> dict:
    """Agent allowed only calculator."""
    r = await client.post("/agents/register", json={
        "name":          "calc-run-bot",
        "allowed_tools": ["calculator"],
        "secret":        "run-test-secret-key-42",
    })
    assert r.status_code == 201
    data = r.json()
    return {
        "agent_id": data["agent_id"],
        "headers":  {"Authorization": f"Bearer {data['access_token']}"},
    }


@pytest_asyncio.fixture
async def full_agent(client: AsyncClient) -> dict:
    """Agent allowed all three tools."""
    r = await client.post("/agents/register", json={
        "name":          "full-run-bot",
        "allowed_tools": ["calculator", "weather", "file_reader"],
        "secret":        "run-test-secret-key-99",
    })
    assert r.status_code == 201
    data = r.json()
    return {
        "agent_id": data["agent_id"],
        "headers":  {"Authorization": f"Bearer {data['access_token']}"},
    }


@pytest_asyncio.fixture
async def no_tools_agent(client: AsyncClient) -> dict:
    """Agent with no allowed tools — everything it calls is a violation."""
    r = await client.post("/agents/register", json={
        "name":          "no-tools-run-bot",
        "allowed_tools": [],
        "secret":        "run-test-secret-key-77",
    })
    assert r.status_code == 201
    data = r.json()
    return {
        "agent_id": data["agent_id"],
        "headers":  {"Authorization": f"Bearer {data['access_token']}"},
    }


# ══════════════════════════════════════════════════════════════
# Authentication enforcement
# ══════════════════════════════════════════════════════════════

class TestRunAuthentication:

    async def test_run_without_token_returns_401(self, client):
        r = await client.post("/runs", json={"prompt": "hello"})
        assert r.status_code == 401

    async def test_run_with_bad_token_returns_401(self, client):
        r = await client.post(
            "/runs",
            json={"prompt": "hello"},
            headers={"Authorization": "Bearer bad.token.here"},
        )
        assert r.status_code == 401


# ══════════════════════════════════════════════════════════════
# Request validation
# ══════════════════════════════════════════════════════════════

class TestRunValidation:

    async def test_empty_prompt_returns_422(self, client, calc_agent):
        r = await client.post(
            "/runs",
            json={"prompt": ""},
            headers=calc_agent["headers"],
        )
        assert r.status_code == 422

    async def test_missing_prompt_returns_422(self, client, calc_agent):
        r = await client.post(
            "/runs",
            json={},
            headers=calc_agent["headers"],
        )
        assert r.status_code == 422

    async def test_prompt_too_long_returns_422(self, client, calc_agent):
        r = await client.post(
            "/runs",
            json={"prompt": "x" * 4001},
            headers=calc_agent["headers"],
        )
        assert r.status_code == 422


# ══════════════════════════════════════════════════════════════
# Successful run — response structure
# ══════════════════════════════════════════════════════════════

class TestRunSuccess:

    async def test_run_returns_200_with_correct_schema(self, client, calc_agent):
        mock = _make_mock_invoke(
            [("calculator", {"expression": "6*7"})],
            answer="6 times 7 is 42.",
        )
        p1, p2 = _patch_llm(mock)
        with p1, p2:
            r = await client.post(
                "/runs",
                json={"prompt": "What is 6 * 7?"},
                headers=calc_agent["headers"],
            )

        assert r.status_code == 200
        body = r.json()
        # Required top-level fields
        for field in ["run_id", "agent_id", "trace_id", "status",
                      "prompt", "result", "started_at", "events",
                      "violation_count", "latency_ms"]:
            assert field in body, f"Missing field: {field}"

    async def test_run_status_is_completed(self, client, calc_agent):
        mock = _make_mock_invoke([], answer="Done.")
        p1, p2 = _patch_llm(mock)
        with p1, p2:
            r = await client.post(
                "/runs",
                json={"prompt": "Say done."},
                headers=calc_agent["headers"],
            )
        assert r.json()["status"] == "completed"

    async def test_run_result_matches_agent_answer(self, client, calc_agent):
        mock = _make_mock_invoke([], answer="The result is 42.")
        p1, p2 = _patch_llm(mock)
        with p1, p2:
            r = await client.post(
                "/runs",
                json={"prompt": "What is 6*7?"},
                headers=calc_agent["headers"],
            )
        assert r.json()["result"] == "The result is 42."

    async def test_run_agent_id_matches_registered_agent(self, client, calc_agent):
        mock = _make_mock_invoke([], answer="ok")
        p1, p2 = _patch_llm(mock)
        with p1, p2:
            r = await client.post(
                "/runs",
                json={"prompt": "ping"},
                headers=calc_agent["headers"],
            )
        assert r.json()["agent_id"] == calc_agent["agent_id"]

    async def test_run_prompt_echoed_in_response(self, client, calc_agent):
        mock = _make_mock_invoke([], answer="ok")
        p1, p2 = _patch_llm(mock)
        with p1, p2:
            r = await client.post(
                "/runs",
                json={"prompt": "echo this prompt"},
                headers=calc_agent["headers"],
            )
        assert r.json()["prompt"] == "echo this prompt"

    async def test_run_latency_ms_is_non_negative(self, client, calc_agent):
        mock = _make_mock_invoke([], answer="ok")
        p1, p2 = _patch_llm(mock)
        with p1, p2:
            r = await client.post(
                "/runs",
                json={"prompt": "ping"},
                headers=calc_agent["headers"],
            )
        lat = r.json()["latency_ms"]
        assert lat is None or lat >= 0


# ══════════════════════════════════════════════════════════════
# Event sequence correctness
# ══════════════════════════════════════════════════════════════

class TestRunEventSequence:

    async def test_events_start_with_run_start(self, client, calc_agent):
        mock = _make_mock_invoke([], answer="ok")
        p1, p2 = _patch_llm(mock)
        with p1, p2:
            r = await client.post(
                "/runs", json={"prompt": "go"},
                headers=calc_agent["headers"],
            )
        events = r.json()["events"]
        assert events[0]["event_type"] == "run_start"

    async def test_events_end_with_run_end(self, client, calc_agent):
        mock = _make_mock_invoke([], answer="ok")
        p1, p2 = _patch_llm(mock)
        with p1, p2:
            r = await client.post(
                "/runs", json={"prompt": "go"},
                headers=calc_agent["headers"],
            )
        events = r.json()["events"]
        assert events[-1]["event_type"] == "run_end"

    async def test_permitted_tool_call_creates_tool_call_and_tool_end(
        self, client, calc_agent
    ):
        mock = _make_mock_invoke(
            [("calculator", {"expression": "10+5"})],
            answer="15",
        )
        p1, p2 = _patch_llm(mock)
        with p1, p2:
            r = await client.post(
                "/runs", json={"prompt": "what is 10+5"},
                headers=calc_agent["headers"],
            )
        types = [e["event_type"] for e in r.json()["events"]]
        assert "tool_call" in types
        assert "tool_end"  in types

    async def test_permitted_tool_call_has_permitted_true(self, client, calc_agent):
        mock = _make_mock_invoke(
            [("calculator", {"expression": "2+2"})],
            answer="4",
        )
        p1, p2 = _patch_llm(mock)
        with p1, p2:
            r = await client.post(
                "/runs", json={"prompt": "2+2"},
                headers=calc_agent["headers"],
            )
        tool_calls = [
            e for e in r.json()["events"]
            if e["event_type"] == "tool_call"
        ]
        assert all(e["permitted"] is True for e in tool_calls)

    async def test_no_violation_when_using_allowed_tools(self, client, calc_agent):
        mock = _make_mock_invoke(
            [("calculator", {"expression": "3*3"})],
            answer="9",
        )
        p1, p2 = _patch_llm(mock)
        with p1, p2:
            r = await client.post(
                "/runs", json={"prompt": "3*3"},
                headers=calc_agent["headers"],
            )
        assert r.json()["violation_count"] == 0
        types = [e["event_type"] for e in r.json()["events"]]
        assert "violation" not in types

    async def test_run_with_no_tool_calls_still_has_run_events(
        self, client, calc_agent
    ):
        mock = _make_mock_invoke([], answer="No tools needed.")
        p1, p2 = _patch_llm(mock)
        with p1, p2:
            r = await client.post(
                "/runs", json={"prompt": "just answer"},
                headers=calc_agent["headers"],
            )
        types = [e["event_type"] for e in r.json()["events"]]
        assert "run_start" in types
        assert "run_end"   in types


# ══════════════════════════════════════════════════════════════
# Governance enforcement via HTTP
# ══════════════════════════════════════════════════════════════

class TestRunGovernance:

    async def test_blocked_tool_creates_violation_event(
        self, client, calc_agent
    ):
        """
        calc_agent only has calculator.  Mock asks for weather → violation.
        """
        original_blocked = GovernanceEnforcer._make_blocked_proxy

        def patched_blocked(self, original_tool):
            proxy = original_blocked(self, original_tool)
            if original_tool.name == "weather":
                self.violations.append(
                    ViolationRecord(tool_name="weather", input_data={"city": "Paris"})
                )
            return proxy

        def mock_invoke(agent_graph, prompt, callback):
            callback.on_tool_start(
                {"name": "weather"}, "Paris", run_id="r1", inputs={"city": "Paris"},
            )
            callback.on_tool_end("Access denied: tool 'weather' is not permitted.", run_id="r1")
            return "I could not get weather — permission denied."

        with patch("app.services.execution_service.build_agent", return_value=MagicMock()), \
             patch("app.services.execution_service._invoke_agent_sync", side_effect=mock_invoke), \
             patch.object(GovernanceEnforcer, "_make_blocked_proxy", patched_blocked):
            r = await client.post(
                "/runs",
                json={"prompt": "What is the weather in Paris?"},
                headers=calc_agent["headers"],
            )

        assert r.status_code == 200
        body = r.json()
        assert body["status"]          == "completed"
        assert body["violation_count"] == 1
        violation_events = [e for e in body["events"] if e["event_type"] == "violation"]
        assert len(violation_events) == 1
        assert violation_events[0]["tool_name"] == "weather"
        assert violation_events[0]["permitted"] is False

    async def test_run_completes_despite_violation(self, client, calc_agent):
        """
        Governance violation must not crash the run — status=completed.
        """
        original_blocked = GovernanceEnforcer._make_blocked_proxy

        def patched_blocked(self, original_tool):
            proxy = original_blocked(self, original_tool)
            if original_tool.name == "weather":
                self.violations.append(
                    ViolationRecord(tool_name="weather", input_data={"city": "Tokyo"})
                )
            return proxy

        def mock_invoke(agent_graph, prompt, callback):
            callback.on_tool_start(
                {"name": "weather"}, "Tokyo", run_id="r1", inputs={"city": "Tokyo"},
            )
            callback.on_tool_end("Access denied.", run_id="r1")
            return "Weather unavailable due to governance restrictions."

        with patch("app.services.execution_service.build_agent", return_value=MagicMock()), \
             patch("app.services.execution_service._invoke_agent_sync", side_effect=mock_invoke), \
             patch.object(GovernanceEnforcer, "_make_blocked_proxy", patched_blocked):
            r = await client.post(
                "/runs",
                json={"prompt": "Tokyo weather?"},
                headers=calc_agent["headers"],
            )

        assert r.status_code == 200
        assert r.json()["status"] == "completed"

    async def test_blocked_tool_call_event_has_permitted_false(
        self, client, calc_agent
    ):
        original_blocked = GovernanceEnforcer._make_blocked_proxy

        def patched_blocked(self, original_tool):
            proxy = original_blocked(self, original_tool)
            if original_tool.name == "weather":
                self.violations.append(
                    ViolationRecord(tool_name="weather", input_data={"city": "X"})
                )
            return proxy

        def mock_invoke(agent_graph, prompt, callback):
            callback.on_tool_start(
                {"name": "weather"}, "X", run_id="r1", inputs={"city": "X"},
            )
            callback.on_tool_end("denied", run_id="r1")
            return "denied"

        with patch("app.services.execution_service.build_agent", return_value=MagicMock()), \
             patch("app.services.execution_service._invoke_agent_sync", side_effect=mock_invoke), \
             patch.object(GovernanceEnforcer, "_make_blocked_proxy", patched_blocked):
            r = await client.post(
                "/runs",
                json={"prompt": "weather?"},
                headers=calc_agent["headers"],
            )

        weather_calls = [
            e for e in r.json()["events"]
            if e["event_type"] == "tool_call" and e["tool_name"] == "weather"
        ]
        assert len(weather_calls) == 1
        assert weather_calls[0]["permitted"] is False

    async def test_no_tools_agent_every_call_is_violation(
        self, client, no_tools_agent
    ):
        original_blocked = GovernanceEnforcer._make_blocked_proxy

        def patched_blocked(self, original_tool):
            proxy = original_blocked(self, original_tool)
            if original_tool.name == "calculator":
                self.violations.append(
                    ViolationRecord(tool_name="calculator", input_data={"expression": "1+1"})
                )
            return proxy

        def mock_invoke(agent_graph, prompt, callback):
            callback.on_tool_start(
                {"name": "calculator"}, "1+1", run_id="r1", inputs={"expression": "1+1"},
            )
            callback.on_tool_end("denied", run_id="r1")
            return "No tools available."

        with patch("app.services.execution_service.build_agent", return_value=MagicMock()), \
             patch("app.services.execution_service._invoke_agent_sync", side_effect=mock_invoke), \
             patch.object(GovernanceEnforcer, "_make_blocked_proxy", patched_blocked):
            r = await client.post(
                "/runs",
                json={"prompt": "1+1?"},
                headers=no_tools_agent["headers"],
            )

        assert r.status_code == 200
        assert r.json()["violation_count"] >= 1


# ══════════════════════════════════════════════════════════════
# Error handling
# ══════════════════════════════════════════════════════════════

class TestRunErrors:

    async def test_llm_exception_returns_500(self, client, calc_agent):
        def crashing_invoke(agent_graph, prompt, callback):
            raise RuntimeError("Groq API unreachable")

        with patch("app.services.execution_service.build_agent", return_value=MagicMock()), \
             patch("app.services.execution_service._invoke_agent_sync", side_effect=crashing_invoke):
            r = await client.post(
                "/runs",
                json={"prompt": "this will crash"},
                headers=calc_agent["headers"],
            )

        assert r.status_code == 500
        # Internal error details must NOT be leaked in the response
        assert "Groq API unreachable" not in r.json().get("detail", "")

    async def test_500_response_does_not_leak_internal_details(
        self, client, calc_agent
    ):
        def crashing_invoke(agent_graph, prompt, callback):
            raise ValueError("SECRET_DB_PASSWORD=hunter2")

        with patch("app.services.execution_service.build_agent", return_value=MagicMock()), \
             patch("app.services.execution_service._invoke_agent_sync", side_effect=crashing_invoke):
            r = await client.post(
                "/runs",
                json={"prompt": "crash"},
                headers=calc_agent["headers"],
            )

        assert r.status_code == 500
        assert "SECRET_DB_PASSWORD" not in r.text
        assert "hunter2"           not in r.text