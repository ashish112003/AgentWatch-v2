# """
# app/db/database.py
# ──────────────────
# Async SQLAlchemy 2.0 database setup.

# Architecture decisions:
#   • We use async engine + AsyncSession so FastAPI request handlers
#     can await DB operations without blocking the event loop.
#   • A single AsyncEngine is created at import time (module singleton).
#   • get_db() is a FastAPI dependency that yields a session per request
#     and guarantees cleanup via try/finally.
#   • Base is the declarative base shared by all ORM models.
# """

# from sqlalchemy.ext.asyncio import (
#     AsyncSession,
#     async_sessionmaker,
#     create_async_engine,
# )
# from sqlalchemy.orm import DeclarativeBase
# from typing import AsyncGenerator

# from app.core.config import settings


# # ── Declarative base ─────────────────────────────────────────────────────────
# # All ORM model classes inherit from Base.  SQLAlchemy uses this to discover
# # tables when create_all() or Alembic migrations run.
# class Base(DeclarativeBase):
#     pass


# # ── Async engine ─────────────────────────────────────────────────────────────
# # echo=True logs every SQL statement in development — helpful for debugging,
# # but disable in production to avoid noisy logs.
# engine = create_async_engine(
#     settings.DATABASE_URL,
#     echo=(settings.APP_ENV == "development"),
#     # SQLite-specific: allow the same connection to be used across threads.
#     # Required for aiosqlite to work with SQLAlchemy's connection pool.
#     connect_args={"check_same_thread": False}
#     if "sqlite" in settings.DATABASE_URL
#     else {},
# )

# # ── Session factory ───────────────────────────────────────────────────────────
# # async_sessionmaker is the async equivalent of sessionmaker.
# # expire_on_commit=False keeps model instances usable after a commit,
# # which is important when we return Pydantic schemas built from ORM objects.
# AsyncSessionLocal = async_sessionmaker(
#     bind=engine,
#     class_=AsyncSession,
#     expire_on_commit=False,
#     autoflush=False,
#     autocommit=False,
# )


# # ── FastAPI dependency ────────────────────────────────────────────────────────
# async def get_db() -> AsyncGenerator[AsyncSession, None]:
#     """
#     Yield an AsyncSession for the duration of a single HTTP request.

#     Usage in a route:
#         @router.get("/items")
#         async def list_items(db: AsyncSession = Depends(get_db)):
#             ...

#     The session is rolled back on exception and always closed afterwards,
#     so there are no connection leaks.
#     """
#     async with AsyncSessionLocal() as session:
#         try:
#             yield session
#             await session.commit()
#         except Exception:
#             await session.rollback()
#             raise
#         finally:
#             await session.close()


# # ── Table initialisation helper ───────────────────────────────────────────────
# async def init_db() -> None:
#     """
#     Create all tables defined via Base metadata.

#     Called once at application startup (see app/main.py lifespan handler).
#     In production you would use Alembic migrations instead, but this
#     create_all() call is kept so the dev server works out of the box.
#     """
#     async with engine.begin() as conn:
#         await conn.run_sync(Base.metadata.create_all)




"""
app/db/database.py
──────────────────
Async SQLAlchemy 2.0 database setup.

Architecture decisions:
  • We use async engine + AsyncSession so FastAPI request handlers
    can await DB operations without blocking the event loop.
  • A single AsyncEngine is created at import time (module singleton).
  • get_db() is a FastAPI dependency that yields a session per request
    and guarantees cleanup via try/finally.
  • Base is the declarative base shared by all ORM models.
"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from typing import AsyncGenerator

from app.core.config import settings


# ── Declarative base ─────────────────────────────────────────────────────────
# All ORM model classes inherit from Base.  SQLAlchemy uses this to discover
# tables when create_all() or Alembic migrations run.
class Base(DeclarativeBase):
    pass


# ── Async engine ─────────────────────────────────────────────────────────────
# Auto-selects between SQLite and PostgreSQL based on the effective URL.
#
# SQLite  (development):
#   • connect_args={"check_same_thread": False} — required for aiosqlite.
#   • NullPool is NOT set; aiosqlite uses StaticPool by default.
#
# PostgreSQL (production):
#   • asyncpg driver via postgresql+asyncpg://...
#   • Connection pooling via QueuePool (SQLAlchemy default for async).
#   • pool_size / max_overflow control concurrency.
#   • pool_pre_ping=True validates connections before use — guards against
#     idle-timeout disconnects from managed PostgreSQL services (RDS, Supabase).

_db_url = settings.effective_database_url
_is_sqlite = _db_url.startswith("sqlite")

_engine_kwargs: dict = {
    "echo": (settings.APP_ENV == "development"),
}

if _is_sqlite:
    # aiosqlite requires check_same_thread=False
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # PostgreSQL production settings
    _engine_kwargs["pool_size"]        = 10    # connections kept open permanently
    _engine_kwargs["max_overflow"]     = 20    # extra connections allowed under load
    _engine_kwargs["pool_pre_ping"]    = True  # check connection liveness before use
    _engine_kwargs["pool_recycle"]     = 3600  # recycle connections after 1 hour

engine = create_async_engine(_db_url, **_engine_kwargs)

# ── Session factory ───────────────────────────────────────────────────────────
# async_sessionmaker is the async equivalent of sessionmaker.
# expire_on_commit=False keeps model instances usable after a commit,
# which is important when we return Pydantic schemas built from ORM objects.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ── FastAPI dependency ────────────────────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an AsyncSession for the duration of a single HTTP request.

    Usage in a route:
        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...

    The session is rolled back on exception and always closed afterwards,
    so there are no connection leaks.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Table initialisation helper ───────────────────────────────────────────────
async def init_db() -> None:
    """
    Create all tables defined via Base metadata.

    Called once at application startup (see app/main.py lifespan handler).

    SQLite:     create_all() is idempotent — safe to call on every start.
    PostgreSQL: create_all() is also safe on first run but for production
                upgrades you should use `alembic upgrade head` instead so
                schema changes are applied incrementally without data loss.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)