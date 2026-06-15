# """
# app/main.py
# ────────────
# FastAPI application factory and entry point.

# This file is intentionally kept small.  Its only responsibilities are:
#   1. Create the FastAPI app instance with metadata.
#   2. Register startup/shutdown logic via the lifespan context manager.
#   3. Mount middleware (CORS, logging).
#   4. Include all API routers.
#   5. Define the GET /health endpoint (no router needed — it's one line).

# All business logic lives in services/.
# All HTTP routing lives in api/.
# All DB setup lives in db/.

# To run locally:
#     uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# """

# import logging
# from contextlib import asynccontextmanager
# from typing import AsyncGenerator

# from fastapi import FastAPI
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.staticfiles import StaticFiles
# from fastapi.responses import FileResponse
# import os

# from app.core.config import settings
# from app.db.database import init_db
# from app.api.agents import router as agents_router
# from app.api.runs import router as runs_router

# from app.api.audit import router as audit_router
# from app.api.governance import router as governance_router
# from app.api.analytics import router as analytics_router

# # ── Logging setup ─────────────────────────────────────────────────────────────
# # Configure root logger so all modules share the same format.
# logging.basicConfig(
#     level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
#     format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
#     datefmt="%Y-%m-%dT%H:%M:%S",
# )
# logger = logging.getLogger("agentwatch")


# # ── Lifespan ──────────────────────────────────────────────────────────────────
# # FastAPI's lifespan replaces the deprecated @app.on_event("startup") pattern.
# # Code before `yield` runs at startup; code after `yield` runs at shutdown.
# @asynccontextmanager
# async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
#     """Application startup and shutdown logic."""

#     # ── Startup ───────────────────────────────────────────────────────
#     logger.info("AgentWatch starting up (env=%s)", settings.APP_ENV)

#     # Initialise the database — creates tables if they don't exist.
#     # In production you would run `alembic upgrade head` in your
#     # deployment pipeline instead.
#     await init_db()
#     logger.info("Database initialised at: %s", settings.DATABASE_URL)

#     yield   # ← application runs while we are suspended here

#     # ── Shutdown ──────────────────────────────────────────────────────
#     logger.info("AgentWatch shutting down.")


# # ── Application factory ───────────────────────────────────────────────────────
# app = FastAPI(
#     title="AgentWatch",
#     description=(
#         "AI Agent Runtime Governance and Observability Platform.\n\n"
#         "AgentWatch tracks every tool call an AI agent makes, enforces "
#         "tool-level permissions, detects governance violations, and "
#         "maintains a full audit trail."
#     ),
#     version="1.0.0",
#     docs_url="/docs",       # Swagger UI
#     redoc_url="/redoc",     # ReDoc UI
#     lifespan=lifespan,
# )


# # ── Middleware ─────────────────────────────────────────────────────────────────
# # CORS — allow all origins in development.
# # Tighten allow_origins in production to your actual frontend domain.
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"] if not settings.is_production else [],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )


# # ── Routers ───────────────────────────────────────────────────────────────────
# # All routers are prefixed with /api/v1 to namespace the API and leave
# # room for future versioning without breaking existing clients.
# API_PREFIX = ""   # No prefix for MVP simplicity — add /api/v1 when needed.

# app.include_router(agents_router, prefix=API_PREFIX)
# app.include_router(agents_router, prefix=API_PREFIX)
# app.include_router(runs_router, prefix=API_PREFIX)

# app.include_router(audit_router, prefix=API_PREFIX)
# app.include_router(governance_router, prefix=API_PREFIX)
# app.include_router(analytics_router, prefix=API_PREFIX)


# # ── Static files & Dashboard ──────────────────────────────────────────────────
# # Serve the Bootstrap dashboard from the /dashboard directory.
# # The StaticFiles mount is only added if the directory exists, so the
# # app still starts cleanly during early development.
# _dashboard_dir = os.path.join(os.path.dirname(__file__), "..", "dashboard")
# print("DASHBOARD PATH =", _dashboard_dir)
# print("EXISTS =", os.path.isdir(_dashboard_dir))
# if os.path.isdir(_dashboard_dir):
#     app.mount(
#         "/dashboard",
#         StaticFiles(directory=_dashboard_dir, html=True),
#         name="dashboard",
#     )

#     @app.get("/", include_in_schema=False)
#     async def root() -> FileResponse:
#         """Redirect root URL to the dashboard."""
#         return FileResponse(os.path.join(_dashboard_dir, "index.html"))


# # ── Health check ──────────────────────────────────────────────────────────────
# @app.get(
#     "/health",
#     tags=["System"],
#     summary="Health check",
#     description="Returns service status. No authentication required.",
# )
# async def health_check() -> dict:
#     """
#     Simple liveness probe.

#     Used by Docker health checks, load balancers, and Kubernetes probes.
#     Returns 200 OK as long as the event loop is running.

#     For a deeper readiness probe (checks DB connectivity), add:
#         await db.execute(text("SELECT 1"))
#     and inject the DB session dependency.
#     """
#     return {
#         "status": "healthy",
#         "service": "agentwatch",
#         "version": "1.0.0",
#         "environment": settings.APP_ENV,
#     }







# """
# app/main.py
# ────────────
# FastAPI application factory and entry point.
# """

# import logging
# from contextlib import asynccontextmanager
# from typing import AsyncGenerator

# from fastapi import FastAPI
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.staticfiles import StaticFiles
# from fastapi.responses import FileResponse
# import os

# from app.core.config import settings
# from app.db.database import init_db
# from app.api.agents       import router as agents_router
# from app.api.runs         import router as runs_router
# from app.api.audit        import router as audit_router
# from app.api.governance   import router as governance_router
# from app.api.analytics    import router as analytics_router
# from app.api.interactions import router as interactions_router

# logging.basicConfig(
#     level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
#     format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
#     datefmt="%Y-%m-%dT%H:%M:%S",
# )
# logger = logging.getLogger("agentwatch")


# @asynccontextmanager
# async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
#     logger.info("AgentWatch starting up (env=%s)", settings.APP_ENV)
#     await init_db()
#     logger.info("Database initialised at: %s", settings.DATABASE_URL)
#     yield
#     logger.info("AgentWatch shutting down.")


# app = FastAPI(
#     title="AgentWatch",
#     description=(
#         "AI Agent Runtime Governance and Observability Platform.\n\n"
#         "AgentWatch tracks every tool call an AI agent makes, enforces "
#         "tool-level permissions, detects governance violations, and "
#         "maintains a full audit trail."
#     ),
#     version="1.0.0",
#     docs_url="/docs"  if not settings.is_production else None,
#     redoc_url="/redoc" if not settings.is_production else None,
#     lifespan=lifespan,
# )

# _allow_origins = (
#     settings.ALLOW_ORIGINS.split(",")
#     if settings.ALLOW_ORIGINS
#     else ([] if settings.is_production else ["*"])
# )
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=_allow_origins,
#     allow_credentials=bool(_allow_origins and "*" not in _allow_origins),
#     allow_methods=["GET", "POST", "OPTIONS"],
#     allow_headers=["Authorization", "Content-Type"],
# )

# API_PREFIX = ""

# app.include_router(agents_router,       prefix=API_PREFIX)
# app.include_router(runs_router,         prefix=API_PREFIX)
# app.include_router(audit_router,        prefix=API_PREFIX)
# app.include_router(governance_router,   prefix=API_PREFIX)
# app.include_router(analytics_router,    prefix=API_PREFIX)
# app.include_router(interactions_router, prefix=API_PREFIX)

# _dashboard_dir = os.path.join(os.path.dirname(__file__), "..", "dashboard")
# if os.path.isdir(_dashboard_dir):
#     app.mount(
#         "/dashboard",
#         StaticFiles(directory=_dashboard_dir, html=True),
#         name="dashboard",
#     )

#     @app.get("/", include_in_schema=False)
#     async def root() -> FileResponse:
#         return FileResponse(os.path.join(_dashboard_dir, "index.html"))


# @app.get(
#     "/health",
#     tags=["System"],
#     summary="Health check",
#     description="Returns service status. No authentication required.",
# )
# async def health_check() -> dict:
#     return {
#         "status": "healthy",
#         "service": "agentwatch",
#         "version": "1.0.0",
#         "environment": settings.APP_ENV,
#     }


# """
# app/main.py
# ────────────
# FastAPI application factory and entry point.
# """

# import logging
# from contextlib import asynccontextmanager
# from typing import AsyncGenerator

# from fastapi import FastAPI
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.staticfiles import StaticFiles
# from fastapi.responses import FileResponse
# import os

# from app.core.config import settings
# from app.db.database import init_db
# from app.api.agents       import router as agents_router
# from app.api.runs         import router as runs_router
# from app.api.audit        import router as audit_router
# from app.api.governance   import router as governance_router
# from app.api.analytics    import router as analytics_router
# from app.api.interactions import router as interactions_router

# logging.basicConfig(
#     level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
#     format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
#     datefmt="%Y-%m-%dT%H:%M:%S",
# )
# logger = logging.getLogger("agentwatch")


# @asynccontextmanager
# async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
#     logger.info("AgentWatch starting up (env=%s)", settings.APP_ENV)
#     await init_db()
#     logger.info("Database initialised at: %s", settings.DATABASE_URL)
#     yield
#     logger.info("AgentWatch shutting down.")


# app = FastAPI(
#     title="AgentWatch",
#     description=(
#         "AI Agent Runtime Governance and Observability Platform.\n\n"
#         "AgentWatch tracks every tool call an AI agent makes, enforces "
#         "tool-level permissions, detects governance violations, and "
#         "maintains a full audit trail."
#     ),
#     version="1.0.0",
#     docs_url="/docs"  if not settings.is_production else None,
#     redoc_url="/redoc" if not settings.is_production else None,
#     lifespan=lifespan,
# )

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"] if not settings.is_production else [],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# API_PREFIX = ""

# app.include_router(agents_router,       prefix=API_PREFIX)
# app.include_router(runs_router,         prefix=API_PREFIX)
# app.include_router(audit_router,        prefix=API_PREFIX)
# app.include_router(governance_router,   prefix=API_PREFIX)
# app.include_router(analytics_router,    prefix=API_PREFIX)
# app.include_router(interactions_router, prefix=API_PREFIX)

# _dashboard_dir = os.path.join(os.path.dirname(__file__), "..", "dashboard")
# if os.path.isdir(_dashboard_dir):
#     app.mount(
#         "/dashboard",
#         StaticFiles(directory=_dashboard_dir, html=True),
#         name="dashboard",
#     )

#     @app.get("/", include_in_schema=False)
#     async def root() -> FileResponse:
#         return FileResponse(os.path.join(_dashboard_dir, "index.html"))


# @app.get(
#     "/health",
#     tags=["System"],
#     summary="Health check",
#     description="Returns service status. No authentication required.",
# )
# async def health_check() -> dict:
#     return {
#         "status": "healthy",
#         "service": "agentwatch",
#         "version": "1.0.0",
#         "environment": settings.APP_ENV,
#     }











"""
app/main.py
────────────
FastAPI application factory and entry point.

This file is intentionally kept small.  Its only responsibilities are:
  1. Create the FastAPI app instance with metadata.
  2. Register startup/shutdown logic via the lifespan context manager.
  3. Mount middleware (CORS, logging).
  4. Include all API routers.
  5. Define the GET /health endpoint (no router needed — it's one line).

All business logic lives in services/.
All HTTP routing lives in api/.
All DB setup lives in db/.

To run locally:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from app.core.config import settings
from app.db.database import init_db
from app.api.agents     import router as agents_router
from app.api.runs       import router as runs_router
from app.api.audit      import router as audit_router
from app.api.governance import router as governance_router
from app.api.analytics     import router as analytics_router
from app.api.interactions  import router as interactions_router
from app.api.policies      import router as policies_router

# ── Logging setup ─────────────────────────────────────────────────────────────
# Configure root logger so all modules share the same format.
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("agentwatch")


# ── Lifespan ──────────────────────────────────────────────────────────────────
# FastAPI's lifespan replaces the deprecated @app.on_event("startup") pattern.
# Code before `yield` runs at startup; code after `yield` runs at shutdown.
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown logic."""

    # ── Startup ───────────────────────────────────────────────────────
    logger.info("AgentWatch starting up (env=%s)", settings.APP_ENV)

    # Initialise the database — creates tables if they don't exist.
    # In production you would run `alembic upgrade head` in your
    # deployment pipeline instead.
    await init_db()
    logger.info("Database initialised at: %s", settings.DATABASE_URL)

    yield   # ← application runs while we are suspended here

    # ── Shutdown ──────────────────────────────────────────────────────
    logger.info("AgentWatch shutting down.")


# ── Application factory ───────────────────────────────────────────────────────
app = FastAPI(
    title="AgentWatch",
    description=(
        "AI Agent Runtime Governance and Observability Platform.\n\n"
        "AgentWatch tracks every tool call an AI agent makes, enforces "
        "tool-level permissions, detects governance violations, and "
        "maintains a full audit trail."
    ),
    version="1.0.0",
    # Disable interactive docs in production to reduce attack surface.
    docs_url="/docs"  if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    lifespan=lifespan,
)


# ── Middleware ─────────────────────────────────────────────────────────────────
# CORS — allow all origins in development.
# Tighten allow_origins in production to your actual frontend domain.
# CORS — allow_credentials=True is incompatible with allow_origins=["*"].
# Development: accept all origins without credentials.
# Production: set ALLOW_ORIGINS=https://yourdomain.com in .env.
_allow_origins = (
    settings.ALLOW_ORIGINS.split(",")
    if settings.ALLOW_ORIGINS
    else ([] if settings.is_production else ["*"])
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=bool(_allow_origins and "*" not in _allow_origins),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# ── Routers ───────────────────────────────────────────────────────────────────
# All routers are prefixed with /api/v1 to namespace the API and leave
# room for future versioning without breaking existing clients.
API_PREFIX = ""   # No prefix for MVP simplicity — add /api/v1 when needed.

app.include_router(agents_router,     prefix=API_PREFIX)
app.include_router(runs_router,        prefix=API_PREFIX)
app.include_router(audit_router,      prefix=API_PREFIX)
app.include_router(governance_router, prefix=API_PREFIX)
app.include_router(analytics_router,    prefix=API_PREFIX)
app.include_router(interactions_router, prefix=API_PREFIX)
app.include_router(policies_router,     prefix=API_PREFIX)


# ── Static files & Dashboard ──────────────────────────────────────────────────
# Serve the Bootstrap dashboard from the /dashboard directory.
# The StaticFiles mount is only added if the directory exists, so the
# app still starts cleanly during early development.
_dashboard_dir = os.path.join(os.path.dirname(__file__), "..", "dashboard")
if os.path.isdir(_dashboard_dir):
    app.mount(
        "/dashboard",
        StaticFiles(directory=_dashboard_dir, html=True),
        name="dashboard",
    )

    @app.get("/", include_in_schema=False)
    async def root() -> FileResponse:
        """Redirect root URL to the dashboard."""
        return FileResponse(os.path.join(_dashboard_dir, "index.html"))


# ── Health check ──────────────────────────────────────────────────────────────
@app.get(
    "/health",
    tags=["System"],
    summary="Health check",
    description="Returns service status. No authentication required.",
)
async def health_check() -> dict:
    """
    Simple liveness probe.

    Used by Docker health checks, load balancers, and Kubernetes probes.
    Returns 200 OK as long as the event loop is running.

    For a deeper readiness probe (checks DB connectivity), add:
        await db.execute(text("SELECT 1"))
    and inject the DB session dependency.
    """
    return {
        "status": "healthy",
        "service": "agentwatch",
        "version": "1.0.0",
        "environment": settings.APP_ENV,
    }