# AgentWatch

**AI Agent Runtime Governance & Observability Platform**

AgentWatch tracks every tool call an AI agent makes, enforces tool-level permissions, detects governance violations, maintains a full audit trail, calculates Trust and Risk scores, and visualises everything in a real-time dashboard.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Bootstrap Dashboard  (dashboard/)                          │
│  Chart.js charts · JWT auth · Live polling                 │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP / REST
┌──────────────────────────▼──────────────────────────────────┐
│  FastAPI Application  (app/)                                │
│  Auth · Agents · Runs · Audit · Governance · Analytics     │
│  Interactions · Policy Engine · Trust Score · Risk Score   │
└──────────────────────────┬──────────────────────────────────┘
                           │ SQLAlchemy 2.0 async
┌──────────────────────────▼──────────────────────────────────┐
│  Database  (SQLite — asyncpg/PostgreSQL planned)            │
│  agents · agent_runs · agent_events · agent_interactions   │
│  policies · agent_policies                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## Quickstart (Local Development)

### Prerequisites

- Python 3.11+
- Groq API key — get one free at https://console.groq.com

### Install

```bash
git clone https://github.com/yourname/agentwatch
cd agentwatch

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:
```
GROQ_API_KEY=your_groq_api_key_here
JWT_SECRET_KEY=<output of: python -c "import secrets; print(secrets.token_hex(32))">
```

### Run

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- **Dashboard**: http://localhost:8000/dashboard/
- **API Docs (Swagger)**: http://localhost:8000/docs
- **API Docs (ReDoc)**: http://localhost:8000/redoc

---

## Docker

```bash
cp .env.example .env
# Set GROQ_API_KEY and JWT_SECRET_KEY

docker-compose up --build
```

The SQLite database persists in the `agentwatch_data` Docker volume.

---

## Database

AgentWatch currently uses **SQLite** (via `aiosqlite`) as its database. This works well for development, local deployments, and single-server production use with moderate traffic.

**PostgreSQL support is planned for a future release.** The ORM models and SQLAlchemy 2.0 async patterns are already compatible with PostgreSQL — switching will require only a driver install (`asyncpg`) and a `DATABASE_URL` change with no model rewrites.

To configure the database path:

```
# .env
DATABASE_URL=sqlite+aiosqlite:///./agentwatch.db

# Docker (named volume)
DATABASE_URL=sqlite+aiosqlite:////app/data/agentwatch.db
```

---

## API Reference

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/agents/register` | — | Register agent, receive JWT |
| GET | `/agents` | ✓ | List all agents |
| GET | `/agents/{id}` | ✓ | Agent detail |
| POST | `/agents/run` | ✓ | Execute governed agent run |
| GET | `/audit/logs` | ✓ | Full audit event log |
| GET | `/audit/logs/{agent_id}` | ✓ | Agent-scoped audit log |
| GET | `/governance/violations` | ✓ | Tool governance violations |
| GET | `/governance/runs` | ✓ | Run history with violation counts |
| GET | `/agent-interactions` | ✓ | List all agent interactions |
| POST | `/agent-interactions` | ✓ | Record an interaction |
| GET | `/agent-interactions/{id}` | ✓ | Interactions for one agent |
| GET | `/policies` | ✓ | List policies |
| POST | `/policies` | ✓ | Create a policy rule |
| GET | `/policies/{id}` | ✓ | Policy detail |
| POST | `/policies/{id}/agents/{id}` | ✓ | Assign policy to agent |
| DELETE | `/policies/{id}/agents/{id}` | ✓ | Remove policy from agent |
| GET | `/agents/{id}/policies` | ✓ | Policies on an agent |
| GET | `/analytics/stats` | ✓ | Platform-wide aggregates |
| GET | `/analytics/stats/{agent_id}` | ✓ | Per-agent aggregates |
| GET | `/analytics/tool-latency` | ✓ | Tool latency (avg + P95) |
| GET | `/analytics/trust` | ✓ | Trust score distribution |
| GET | `/analytics/trust/{agent_id}` | ✓ | Agent trust breakdown |
| GET | `/analytics/risk` | ✓ | Risk score distribution |
| GET | `/analytics/risk/{agent_id}` | ✓ | Agent risk breakdown |
| GET | `/health` | — | Health check |

---

## Testing

```bash
# Run all tests
pytest tests/ -q

# Specific modules
pytest tests/test_auth.py -v
pytest tests/test_trust.py -v
pytest tests/test_risk.py -v
pytest tests/test_policies.py -v

# With coverage
pip install pytest-cov
pytest tests/ --cov=app --cov-report=term-missing
```

---

## Implemented Phases

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | Tool Expansion (10 tools) | ✅ Complete |
| 2 | Agent-to-Agent Interaction Tracking | ✅ Complete |
| 3 | Policy Engine (5 rule types) | ✅ Complete |
| 4 | Trust Score (0–100) | ✅ Complete |
| 5 | Risk Score (0–100) | ✅ Complete |
| 6 | Visual Analytics Dashboard (Chart.js) | ✅ Complete |
| 7 | PostgreSQL Support | 🔜 Planned |

---

## Security Checklist

- [ ] `JWT_SECRET_KEY` is ≥32 random hex chars
- [ ] `APP_ENV=production` (disables Swagger UI)
- [ ] `GROQ_API_KEY` is set
- [ ] `.env` is `chmod 600` and listed in `.gitignore`
- [ ] App runs behind TLS-terminating reverse proxy