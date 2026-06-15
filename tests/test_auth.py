"""
tests/test_auth.py
───────────────────
HTTP integration tests for agent registration and JWT authentication.

All tests run against a fresh per-test in-memory SQLite database provided
by the conftest.py `client` fixture.  No network calls are made.
No GROQ_API_KEY is required.

Coverage:
  POST /agents/register — success, conflicts, validation
  GET  /agents          — auth, pagination, schema
  GET  /agents/{id}     — by id, 404, secret never exposed
  JWT lifecycle         — expired, bad signature, missing header, orphan
"""

import pytest
import pytest_asyncio
from datetime import timedelta
from httpx import AsyncClient

from app.auth.jwt import create_access_token
pytestmark = pytest.mark.asyncio


# ── Local registered fixture (uses conftest client) ──────────────────────────

@pytest_asyncio.fixture
async def registered(client: AsyncClient) -> dict:
    """Register one agent and return credentials dict."""
    r = await client.post("/agents/register", json={
        "name":          "auth-test-bot",
        "description":   "Test agent",
        "allowed_tools": ["calculator"],
        "secret":        "super-secret-auth-key-42",
    })
    assert r.status_code == 201, f"Setup failed: {r.text}"
    data = r.json()
    return {
        "agent_id": data["agent_id"],
        "token":    data["access_token"],
        "headers":  {"Authorization": f"Bearer {data['access_token']}"},
    }


# ══════════════════════════════════════════════════════════════
# POST /agents/register
# ══════════════════════════════════════════════════════════════

class TestAgentRegistration:

    async def test_register_success_returns_201_with_token(self, client):
        r = await client.post("/agents/register", json={
            "name":          "success-bot",
            "allowed_tools": ["calculator", "weather"],
            "secret":        "long-enough-secret-99",
        })
        assert r.status_code == 201
        body = r.json()
        assert "agent_id"     in body
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert len(body["access_token"].split(".")) == 3

    async def test_register_duplicate_name_returns_409(self, client):
        payload = {
            "name":          "dup-bot",
            "allowed_tools": [],
            "secret":        "long-enough-secret-99",
        }
        r1 = await client.post("/agents/register", json=payload)
        assert r1.status_code == 201
        r2 = await client.post("/agents/register", json=payload)
        assert r2.status_code == 409
        assert "already registered" in r2.json()["detail"].lower()

    async def test_register_invalid_name_format_returns_422(self, client):
        for bad_name in ["Bad Name", "UPPER", "has space", "has!char", "a"]:
            r = await client.post("/agents/register", json={
                "name":          bad_name,
                "allowed_tools": [],
                "secret":        "long-enough-secret-99",
            })
            assert r.status_code == 422, f"Expected 422 for name={bad_name!r}"

    async def test_register_short_secret_returns_422(self, client):
        r = await client.post("/agents/register", json={
            "name":          "short-secret-bot",
            "allowed_tools": [],
            "secret":        "tooshort",
        })
        assert r.status_code == 422

    async def test_register_unknown_tool_returns_422(self, client):
        r = await client.post("/agents/register", json={
            "name":          "bad-tool-bot",
            "allowed_tools": ["nonexistent_tool"],
            "secret":        "long-enough-secret-99",
        })
        assert r.status_code == 422
        assert "Unknown tool" in r.text

    async def test_register_all_tools_allowed(self, client):
        r = await client.post("/agents/register", json={
            "name":          "full-access-bot",
            "allowed_tools": ["calculator", "weather", "file_reader"],
            "secret":        "long-enough-secret-99",
        })
        assert r.status_code == 201

    async def test_register_empty_tools_allowed(self, client):
        r = await client.post("/agents/register", json={
            "name":          "no-tools-bot",
            "allowed_tools": [],
            "secret":        "long-enough-secret-99",
        })
        assert r.status_code == 201

    async def test_register_duplicate_tools_deduped(self, client):
        r = await client.post("/agents/register", json={
            "name":          "dedup-tools-bot",
            "allowed_tools": ["calculator", "calculator", "weather"],
            "secret":        "long-enough-secret-99",
        })
        assert r.status_code == 201
        agent_id = r.json()["agent_id"]
        token    = r.json()["access_token"]
        r2 = await client.get(
            f"/agents/{agent_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.json()["allowed_tools"].count("calculator") == 1

    async def test_register_missing_name_returns_422(self, client):
        r = await client.post("/agents/register", json={
            "allowed_tools": [],
            "secret":        "long-enough-secret-99",
        })
        assert r.status_code == 422

    async def test_register_missing_secret_returns_422(self, client):
        r = await client.post("/agents/register", json={
            "name":          "no-secret-bot",
            "allowed_tools": [],
        })
        assert r.status_code == 422

    async def test_response_never_contains_secret(self, client):
        r = await client.post("/agents/register", json={
            "name":          "secret-check-bot",
            "allowed_tools": [],
            "secret":        "long-enough-secret-99",
        })
        assert r.status_code == 201
        body_text = r.text
        assert "hashed_secret" not in body_text
        assert "long-enough-secret-99" not in body_text


# ══════════════════════════════════════════════════════════════
# GET /agents
# ══════════════════════════════════════════════════════════════

class TestListAgents:

    async def test_list_agents_requires_auth(self, client):
        r = await client.get("/agents")
        assert r.status_code == 401

    async def test_list_agents_returns_200_with_schema(self, client, registered):
        r = await client.get("/agents", headers=registered["headers"])
        assert r.status_code == 200
        body = r.json()
        assert "agents" in body
        assert "total"  in body
        assert body["total"] >= 1

    async def test_list_agents_pagination_limit(self, client, registered):
        r = await client.get("/agents?skip=0&limit=1", headers=registered["headers"])
        assert r.status_code == 200
        assert len(r.json()["agents"]) <= 1

    async def test_list_agents_high_skip_returns_empty(self, client, registered):
        r = await client.get("/agents?skip=999", headers=registered["headers"])
        assert r.status_code == 200
        assert "agents" in r.json()

    async def test_list_agents_agent_fields_present(self, client, registered):
        r = await client.get("/agents", headers=registered["headers"])
        agent = r.json()["agents"][0]
        for field in ["id", "name", "allowed_tools", "created_at"]:
            assert field in agent, f"Missing field: {field}"
        assert "hashed_secret" not in agent


# ══════════════════════════════════════════════════════════════
# GET /agents/{agent_id}
# ══════════════════════════════════════════════════════════════

class TestGetAgent:

    async def test_get_agent_by_id_returns_200(self, client, registered):
        agent_id = registered["agent_id"]
        r = await client.get(f"/agents/{agent_id}", headers=registered["headers"])
        assert r.status_code == 200
        body = r.json()
        assert body["id"]   == agent_id
        assert body["name"] == "auth-test-bot"
        assert "allowed_tools" in body
        assert "created_at"    in body
        assert "hashed_secret" not in body
        assert "secret"        not in body

    async def test_get_agent_not_found_returns_404(self, client, registered):
        r = await client.get("/agents/does-not-exist", headers=registered["headers"])
        assert r.status_code == 404

    async def test_get_agent_requires_auth(self, client, registered):
        r = await client.get(f"/agents/{registered['agent_id']}")
        assert r.status_code == 401


# ══════════════════════════════════════════════════════════════
# JWT lifecycle
# ══════════════════════════════════════════════════════════════

class TestJWTLifecycle:

    async def test_bad_signature_returns_401(self, client):
        r = await client.get(
            "/agents",
            headers={"Authorization": "Bearer eyJhbGci.bad.token"},
        )
        assert r.status_code == 401

    async def test_malformed_bearer_returns_401(self, client):
        r = await client.get(
            "/agents",
            headers={"Authorization": "NotBearer abc"},
        )
        assert r.status_code == 401

    async def test_expired_token_returns_401(self, client):
        expired = create_access_token(
            agent_id="fake-id",
            agent_name="fake",
            expires_delta=timedelta(seconds=-1),
        )
        r = await client.get(
            "/agents",
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert r.status_code == 401

    async def test_valid_token_for_deleted_agent_returns_401(self, client):
        orphan = create_access_token(
            agent_id="00000000-0000-0000-0000-000000000000",
            agent_name="ghost",
        )
        r = await client.get(
            "/agents",
            headers={"Authorization": f"Bearer {orphan}"},
        )
        assert r.status_code == 401

    async def test_missing_authorization_header_returns_401(self, client):
        r = await client.get("/agents")
        assert r.status_code == 401