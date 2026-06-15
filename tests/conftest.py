"""
tests/conftest.py
──────────────────
Shared pytest fixtures for the entire AgentWatch test suite.

Critical design: test isolation
────────────────────────────────
Every test that touches the database must run against a fresh, empty
in-memory SQLite database.  Without this, agent names registered in one
test persist into the next (because the real agentwatch.db on disk is
never reset between tests), causing 409 Conflict failures on names that
worked the first time.

Solution — FastAPI dependency_overrides:
  FastAPI's dependency injection system allows any Depends() target to be
  replaced at test time via app.dependency_overrides[original] = replacement.
  We replace get_db with a function that yields sessions from a per-test
  in-memory engine.  The real agentwatch.db is never touched during tests.

Fixture scoping:
  • engine / session_factory — function scope (new DB per test)
  • client — function scope (fresh app state per test)
  • registered_agent — function scope (registers one agent into the test DB)

All fixtures are async because FastAPI route handlers are async and the
httpx.AsyncClient + ASGITransport pattern requires an async context.
"""

import pytest
import pytest_asyncio
from typing import AsyncGenerator

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.main import app
from app.db.database import Base, get_db


# ── Per-test in-memory engine ────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_engine():
    """
    Create a fresh in-memory SQLite engine for one test.

    'sqlite+aiosqlite:///:memory:' gives each fixture call its own
    completely isolated database that is destroyed when the engine closes.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session_factory(db_engine):
    """Return an async_sessionmaker bound to the test engine."""
    return async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


# ── Dependency override ──────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(db_session_factory) -> AsyncGenerator[AsyncClient, None]:
    """
    Async HTTP test client wired to an isolated in-memory database.

    Replaces the real get_db dependency with one that uses the test DB,
    then restores the original after the test completes.

    Usage in a test:
        async def test_something(client):
            r = await client.post("/agents/register", json={...})
            assert r.status_code == 201
    """
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with db_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    # Install override
    app.dependency_overrides[get_db] = override_get_db

    # Run lifespan (creates real tables via init_db — harmless because
    # init_db uses the module-level engine which is also in-memory in the
    # test environment, but we bypass it with the override anyway).
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            yield c

    # Always remove override so it doesn't leak into other tests
    app.dependency_overrides.pop(get_db, None)


# ── Convenience: pre-registered agent ────────────────────────────────────────

@pytest_asyncio.fixture
async def registered_agent(client: AsyncClient) -> dict:
    """
    Register a default test agent and return its credentials.

    Returns a dict with keys:
        agent_id    — UUID string
        token       — JWT access token
        headers     — {"Authorization": "Bearer <token>"}
        name        — agent display name
        allowed_tools — list of allowed tool names
    """
    r = await client.post("/agents/register", json={
        "name":          "test-agent",
        "description":   "Shared fixture agent for HTTP tests",
        "allowed_tools": ["calculator"],
        "secret":        "fixture-secret-key-42",
    })
    assert r.status_code == 201, (
        f"registered_agent fixture failed: {r.status_code} {r.text}"
    )
    data = r.json()
    return {
        "agent_id":      data["agent_id"],
        "token":         data["access_token"],
        "headers":       {"Authorization": f"Bearer {data['access_token']}"},
        "name":          "test-agent",
        "allowed_tools": ["calculator"],
    }