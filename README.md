# AgentWatch 🛡️

> **AI Agent Runtime Governance & Observability Platform**  
> FastAPI · LangGraph · SQLAlchemy · Chart.js · JWT · Groq Llama 3.3 70B

[![Tests](https://img.shields.io/badge/tests-439%20passing-brightgreen)](#testing)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](#quickstart)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688)](#api-reference)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.2.4-orange)](#architecture)
[![License](https://img.shields.io/badge/license-MIT-purple)](#license)

AgentWatch is a production-grade AI governance platform that wraps every LLM agent run with a full observability and enforcement layer. It tracks every tool call, enforces named governance policies, detects violations, scores agent behaviour with Trust and Risk metrics, and visualises everything in a live Chart.js dashboard.

---

## Table of Contents

- [Why AgentWatch](#why-agentwatch)
- [Architecture](#architecture)
- [Request Lifecycle](#request-lifecycle)
- [Governance Enforcement Flow](#governance-enforcement-flow)
- [Database Schema](#database-schema)
- [Features by Phase](#features-by-phase)
- [Trust & Risk Scoring](#trust--risk-scoring)
- [Policy Engine](#policy-engine)
- [Dashboard](#dashboard)
- [API Reference](#api-reference)
- [Quickstart](#quickstart)
- [Docker](#docker)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [Security](#security)
- [Roadmap](#roadmap)

---

## Why AgentWatch

LLM agents are powerful but unpredictable. Without governance, a single misconfigured agent can:

- Call tools it was never supposed to access
- Run indefinitely without rate limits
- Execute dangerous prompts with no filtering
- Leave zero audit trail for debugging or compliance

AgentWatch solves this by inserting a **governance layer between every agent and every tool**, logging every decision, and surfacing anomalies in real time.

---

## Architecture

<img width="723" height="497" alt="image" src="https://github.com/user-attachments/assets/26b28caa-021f-4c05-864a-96a7a47e40a8" />


```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                                    │
│                                                                         │
│   ┌──────────────────────────┐      ┌──────────────────────────────┐   │
│   │  AgentWatch Dashboard    │      │  External Callers            │   │
│   │  Bootstrap · Chart.js    │      │  curl · CI/CD · Agents       │   │
│   └────────────┬─────────────┘      └───────────────┬──────────────┘   │
└────────────────┼──────────────────────────────────-─┼────────────────-─┘
                 │  HTTP + Bearer JWT                  │
┌────────────────▼────────────────────────────────────▼──────────────────┐
│                     FASTAPI APPLICATION (24 endpoints)                  │
│                                                                         │
│   /agents  /agents/run  /audit  /governance  /analytics                 │
│   /agent-interactions  /policies  /health  /dashboard                   │
│                                │                                        │
│                    ┌───────────▼────────────┐                           │
│                    │   execution_service    │                           │
│                    │  ① Policy Evaluation  │  ← pre-execution          │
│                    │  ② GovernanceEnforcer │  ← proxy construction     │
│                    │  ③ LangGraph ReAct   │  ← asyncio.to_thread      │
│                    │  ④ Audit Persistence │  ← event logging           │
│                    │  ⑤ Score Updates    │  ← Trust + Risk             │
│                    └────────────────────────┘                           │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │  SQLAlchemy 2.0 async ORM
┌─────────────────────────▼───────────────────────────────────────────────┐
│                    SQLite  (asyncpg/PostgreSQL planned)                  │
│  agents · agent_runs · agent_events · agent_interactions                │
│  policies · agent_policies                                              │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Request Lifecycle

Every call to `POST /agents/run` passes through this exact sequence:

```
Client
  │
  ├─► [1] JWT Authentication
  │         ├── Decode HS256 token
  │         ├── Verify not expired
  │         └── Confirm agent exists in DB
  │
  ├─► [2] Request Validation (Pydantic v2)
  │         ├── prompt non-empty, ≤4000 chars
  │         └── Schema types enforced
  │
  ├─► [3] Policy Evaluation  ◄── Phase 3
  │         ├── Load active policies for agent from DB
  │         ├── prompt_guard  → block if keyword matched → HTTP 400
  │         ├── time_window   → block if outside hours  → HTTP 400
  │         ├── tool_deny     → remove tool from allowed set
  │         └── rate_limit    → post-hoc check after run
  │
  ├─► [4] GovernanceEnforcer construction
  │         ├── For each of 10 tools in ALL_TOOLS:
  │         │     ├── IN allowed_tools → PermittedProxy (real func)
  │         │     └── NOT in allowed   → BlockedProxy  (ViolationRecord)
  │         └── governed_tools list passed to LangGraph
  │
  ├─► [5] LangGraph ReAct execution (asyncio.to_thread)
  │         ├── LLM receives prompt + all tool schemas
  │         ├── LLM reasons and emits tool_call
  │         ├── ToolEventCallback: on_tool_start / on_tool_end
  │         ├── Proxy intercepts, permits or blocks
  │         └── LLM sees result, loops or produces answer
  │
  ├─► [6] Audit event persistence
  │         ├── run_start
  │         ├── tool_call (permitted=True|False)
  │         ├── tool_end  (latency_ms recorded)
  │         ├── violation (denial_message + ViolationRecord)
  │         ├── policy_violation
  │         └── run_end
  │
  ├─► [7] AgentRun update
  │         ├── status  = "completed" | "failed"
  │         ├── result  = final LLM answer
  │         └── ended_at = UTC timestamp
  │
  └─► [8] RunResponse returned
            ├── run_id, trace_id, agent_id, status
            ├── events[]  (full ordered trace)
            ├── violation_count
            └── latency_ms
```

---

## Governance Enforcement Flow
<img width="715" height="483" alt="image" src="https://github.com/user-attachments/assets/5b483498-2387-4988-9bcc-cff6e0f5b820" />



```
               GovernanceEnforcer
               (built per-run)
                      │
          ┌───────────┴───────────┐
     IN allowed_tools         NOT in allowed
          │                        │
   PermittedProxy            BlockedProxy
   calls real func           appends ViolationRecord
          │                        │
   tool_call  permitted=T    tool_call  permitted=F
   tool_end   latency_ms     violation  denial_msg
          │                        │
   result → LLM context      "Access denied" → LLM
```

Key design: **blocked tools remain visible in the LLM's schema** — the LLM can still reason about them and relay the denial to the user, creating a better UX and a cleaner audit trail than silently removing tools.

---

## Database Schema

```
┌──────────────────────┐       ┌──────────────────────┐
│       agents         │       │   agent_interactions  │
├──────────────────────┤       ├──────────────────────┤
│ id          UUID PK  │◄──┐   │ id            UUID PK│
│ name        UNIQUE   │   │   │ source_agent_id FK──►│
│ description TEXT     │   │   │ target_agent_id FK──►│
│ allowed_tools JSON   │   │   │ interaction_type      │
│ hashed_secret        │   │   │ message TEXT          │
│ created_at           │   └───│ created_at            │
└──────────┬───────────┘       └──────────────────────┘
           │ 1:N
           │                   ┌──────────────────────┐
┌──────────▼───────────┐       │      policies        │
│     agent_runs        │       ├──────────────────────┤
├──────────────────────┤       │ id            UUID PK│
│ id          UUID PK  │       │ name          UNIQUE  │
│ agent_id    FK       │       │ rule_type     VARCHAR │
│ prompt      TEXT     │       │ rule_config   JSON    │
│ status      VARCHAR  │       │ severity      VARCHAR │
│ result      TEXT     │       │ is_active     BOOL    │
│ trace_id    UUID     │       │ created_at            │
│ started_at           │       └──────────┬───────────┘
│ ended_at             │                  │ M:N
└──────────┬───────────┘       ┌──────────▼───────────┐
           │ 1:N               │   agent_policies      │
┌──────────▼───────────┐       ├──────────────────────┤
│    agent_events       │       │ agent_id      FK     │
├──────────────────────┤       │ policy_id     FK     │
│ id          UUID PK  │       │ created_at            │
│ run_id      FK       │       └──────────────────────┘
│ agent_id    FK       │
│ trace_id    UUID     │
│ event_type  VARCHAR  │  ← run_start|tool_call|tool_end
│ tool_name   VARCHAR  │    violation|policy_violation
│ input_data  JSON     │    run_end|agent_handoff
│ output_data JSON     │
│ permitted   BOOL     │  ← the governance gate result
│ latency_ms  FLOAT    │  ← ms, tool_end events only
│ timestamp   DATETIME │
└──────────────────────┘
```

---

## Features by Phase

### Phase 1 - Tool Expansion (10 tools)

All tools follow the LangChain `@tool` decorator pattern, are self-contained, return strings, and automatically participate in governance, audit, and analytics.

| Tool | Description |
|------|-------------|
| `calculator` | AST-safe arithmetic — never calls `eval()` |
| `weather` | Current conditions for any city |
| `file_reader` | Reads files from a sandboxed `/sandbox` directory |
| `datetime_tool` | date / time / utc / day / timestamp / all |
| `currency_converter` | 20-currency conversion with static 2024 rates |
| `wikipedia_search` | Wikipedia article summaries (graceful offline fallback) |
| `text_summarizer` | Extractive TF-score summarisation, no extra LLM calls |
| `word_counter` | Words, chars, sentences, paragraphs, reading time |
| `json_formatter` | format / validate / minify / keys / stats |
| `uuid_generator` | 1–20 UUID v4 values, optional uppercase/no-hyphens/braces |

### Phase 2 - Agent-to-Agent Interaction Tracking

Record directed interactions between agents. Every interaction automatically emits an `agent_handoff` audit event backed by a real `AgentRun` row (no FK sentinel values).

**Interaction types:** `handoff` · `delegation` · `request` · `response`

### Phase 3 - Policy Engine (5 Rule Types)

Named, reusable rules assigned to agents in a many-to-many relationship. Evaluated **before** GovernanceEnforcer on every run.

| Rule Type | Config Example | Enforcement |
|-----------|---------------|-------------|
| `tool_deny` | `{"tool": "weather"}` | Removes tool from effective allow-list |
| `tool_allow` | `{"tool": "calculator"}` | Adds tool to effective allow-list |
| `rate_limit` | `{"max_calls_per_run": 3}` | Checked post-hoc; blocks if exceeded |
| `prompt_guard` | `{"blocked_keywords": ["password"]}` | Blocks run before LLM is called |
| `time_window` | `{"start_hour": 9, "end_hour": 18}` | Blocks run outside allowed hours |

**Severity levels:** `LOW` · `MEDIUM` · `HIGH` · `CRITICAL`  
Severity feeds directly into Trust Score deductions and Risk Score additions.

### Phase 4 - Trust Score

### Phase 5 - Risk Score

<img width="721" height="413" alt="image" src="https://github.com/user-attachments/assets/e502966e-0036-49eb-9c38-9685faa4458a" />



See [Trust & Risk Scoring](#trust--risk-scoring) section below.

### Phase 6 - Visual Analytics Dashboard

Five Chart.js charts powered by a single `GET /analytics/stats` call, with lifecycle management (`_destroyChart` / `_charts` registry) to prevent canvas-reuse errors on refresh.

| Chart | Type | Data Source |
|-------|------|-------------|
| Trust Distribution | Doughnut | `stats.trust_distribution` |
| Risk Distribution | Doughnut | `stats.risk_distribution` |
| Interaction Types | Pie | `stats.interactions_by_type` |
| Tool Usage | Bar | `stats.tool_latency[].call_count` |
| Violation Overview | Bar | `stats.total_violations` + `stats.total_policy_violations` |

---

## Trust & Risk Scoring

Two complementary scores, computed from historical `agent_events` on demand:

**Trust Score** — starts at 100, evolves down with violations, up with good behaviour:
```
Score = 100
      − (governance_violations × 5)
      − (policy_violations × 8)
      − (failed_runs × 2)
      − (high_sev_violations × 5)    ← additional
      − (critical_violations × 10)   ← additional
      + (completed_runs × 0.2)
      + (permitted_tool_calls × 0.1)
      + (interactions × 0.1)
      clamped to [0, 100]
```

| Range | Level |
|-------|-------|
| 90–100 | `TRUSTED` |
| 70–89 | `MONITORED` |
| 50–69 | `WARNING` |
| 0–49 | `HIGH_RISK` |

**Risk Score** — starts at 0, rises with dangerous behaviour, falls with safe behaviour:
```
Score = 0
      + (governance_violations × 15)
      + (policy_violations × 20)
      + (high_sev_violations × 10)   ← additional
      + (critical_violations × 20)   ← additional
      + (failed_runs × 5)
      + (denied_tool_calls × 10)
      + (handoff_interactions × 2)
      + (delegation_interactions × 4)
      + (escalation_interactions × 8)
      − (completed_runs × 0.1)
      − (permitted_tool_calls × 0.05)
      clamped to [0, 100]
```

| Range | Level |
|-------|-------|
| 0–24 | `SAFE` |
| 25–49 | `LOW` |
| 50–74 | `MEDIUM` |
| 75–89 | `HIGH` |
| 90–100 | `CRITICAL` |

> **Key insight:** An agent can have `Trust = 95, Risk = 80` — historically excellent but currently behaving dangerously. The scores are independent and intentionally measure different things.

Both scores are returned in:
- `GET /analytics/stats/{agent_id}` (as `trust_score`, `trust_level`, `risk_score`, `risk_level`)
- `GET /analytics/trust/{agent_id}` (full breakdown per contributing factor)
- `GET /analytics/risk/{agent_id}` (full breakdown per contributing factor)
- `GET /analytics/stats` (averages and distributions across all agents)

---

## Policy Engine

### Create a policy

```bash
curl -X POST http://localhost:8000/policies \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "no-weather-in-prod",
    "description": "Block weather tool in production environment",
    "rule_type": "tool_deny",
    "rule_config": {"tool": "weather"},
    "severity": "HIGH",
    "is_active": true
  }'
```

### Assign to an agent

```bash
curl -X POST http://localhost:8000/policies/{policy_id}/agents/{agent_id} \
  -H "Authorization: Bearer $TOKEN"
```

### Create a prompt guard

```bash
curl -X POST http://localhost:8000/policies \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "no-secrets-in-prompts",
    "rule_type": "prompt_guard",
    "rule_config": {"blocked_keywords": ["password", "secret", "api_key", "token"]},
    "severity": "CRITICAL",
    "is_active": true
  }'
```

---

## Dashboard

Open `http://localhost:8000/dashboard/` after starting the server.

On first load you'll see the **auth banner** — either register a new agent directly from the UI or paste an existing JWT token. The dashboard stores the token in `sessionStorage` (clears on tab close) and never sends it anywhere except your local FastAPI server.

**Dashboard sections:**

| Section | What it shows |
|---------|--------------|
| Overview | 6 stat cards + 5 Chart.js charts + recent violations preview |
| Violations | Paginated governance violation table with attempted input + denial message |
| Audit Log | Full event log, filterable by event type including `agent_handoff` and `policy_violation` |
| Interactions | Agent-to-agent interaction table |
| Policies | Active policy rules and assignments |

Auto-refreshes every 30 seconds. Manual refresh via the ↻ button in the top bar.

---

## API Reference

All endpoints except `/agents/register` and `/health` require:
```
Authorization: Bearer <JWT>
```

```
AGENTS
  POST   /agents/register                     Register agent, receive JWT
  GET    /agents                              List all agents (paginated)
  GET    /agents/{id}                         Agent detail (no secret exposed)
  GET    /agents/{id}/policies                Policies assigned to this agent

RUNS
  POST   /agents/run                          Execute governed agent run

AUDIT
  GET    /audit/logs                          Full audit log (filter: agent_id, event_type, run_id)
  GET    /audit/logs/{agent_id}               Agent-scoped audit log

GOVERNANCE
  GET    /governance/violations               All governance violations (paginated)
  GET    /governance/violations/{agent_id}    Agent violations
  GET    /governance/runs                     Run history + violation counts

INTERACTIONS
  POST   /agent-interactions                  Record agent-to-agent interaction
  GET    /agent-interactions                  List all interactions (paginated)
  GET    /agent-interactions/{agent_id}       Interactions where agent is source or target

POLICIES
  POST   /policies                            Create a policy rule
  GET    /policies                            List all policies (paginated)
  GET    /policies/{id}                       Policy detail + rule_config
  POST   /policies/{id}/agents/{agent_id}     Assign policy to agent
  DELETE /policies/{id}/agents/{agent_id}     Remove policy from agent

ANALYTICS
  GET    /analytics/stats                     Platform-wide aggregates (charts data source)
  GET    /analytics/stats/{agent_id}          Per-agent aggregates incl. Trust + Risk
  GET    /analytics/tool-latency              avg + P95 latency per tool
  GET    /analytics/trust                     Trust distribution across all agents
  GET    /analytics/trust/{agent_id}          Full trust breakdown per factor
  GET    /analytics/risk                      Risk distribution across all agents
  GET    /analytics/risk/{agent_id}           Full risk breakdown per factor

SYSTEM
  GET    /health                              Health check (no auth required)
  GET    /dashboard/                          Web dashboard
  GET    /docs                                Swagger UI (dev only)
  GET    /redoc                               ReDoc (dev only)
```

### Quick example

```bash
# Register
TOKEN=$(curl -sX POST http://localhost:8000/agents/register \
  -H "Content-Type: application/json" \
  -d '{"name":"calc-bot","allowed_tools":["calculator"],"secret":"my-secret-42"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Run — calculator allowed
curl -sX POST http://localhost:8000/agents/run \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"What is sqrt(1764)?"}' | python3 -m json.tool

# Run — weather NOT allowed → violation logged
curl -sX POST http://localhost:8000/agents/run \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"What is the weather in Tokyo and what is 99*99?"}' | python3 -m json.tool

# Check violations
curl -s http://localhost:8000/governance/violations \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Trust score
curl -s http://localhost:8000/analytics/trust/$(echo $TOKEN | python3 -c "
import sys,base64,json
t=sys.stdin.read().strip().split('.')[1]
print(json.loads(base64.b64decode(t+'=='))['sub'])
") -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

---

## Quickstart

### Prerequisites

- Python 3.11+
- [Groq API key](https://console.groq.com) — free tier available

### Install

```bash
git clone https://github.com/yourname/agentwatch
cd agentwatch

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
```

Minimum required in `.env`:
```env
GROQ_API_KEY=your_groq_api_key_here
JWT_SECRET_KEY=<run: python -c "import secrets; print(secrets.token_hex(32))">
```

### Run

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

| URL | Purpose |
|-----|---------|
| http://localhost:8000/dashboard/ | Live governance dashboard |
| http://localhost:8000/docs | Swagger UI (all endpoints) |
| http://localhost:8000/redoc | ReDoc |
| http://localhost:8000/health | Health check |

---

## Docker

```bash
cp .env.example .env
# Set GROQ_API_KEY and JWT_SECRET_KEY

docker-compose up --build
```

The SQLite database persists in the `agentwatch_data` Docker named volume.

```bash
# Tail logs
docker-compose logs -f agentwatch

# Stop
docker-compose down

# Backup database
docker run --rm \
  -v agentwatch_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/agentwatch_backup.tar.gz /data
```

---

## Testing

```bash
# Full suite
pytest tests/ -q           # 439 tests, ~90 seconds

# By module
pytest tests/test_auth.py -v          # 24 — JWT + registration
pytest tests/test_runs.py -v          # 23 — LLM execution (mocked, no API key needed)
pytest tests/test_audit.py -v         # 62 — audit + analytics + governance
pytest tests/test_interactions.py -v  # 36 — agent-to-agent tracking
pytest tests/test_policies.py -v      # 55 — policy engine enforcement
pytest tests/test_trust.py -v         # 60 — trust score formula + API
pytest tests/test_risk.py -v          # 83 — risk score formula + API
pytest tests/test_dashboard_charts.py # 44 — chart data + HTML structure
pytest tests/test_postgres_config.py  # 32 — config + engine logic

# With coverage
pip install pytest-cov
pytest tests/ --cov=app --cov-report=term-missing
```

**Test isolation:** every test runs against a fresh in-memory SQLite database via `app.dependency_overrides[get_db]`. The on-disk `agentwatch.db` is never touched during test runs. No GROQ_API_KEY is required — LLM calls are mocked via `unittest.mock.patch`.

---

## Project Structure

```
agentwatch/
├── app/
│   ├── api/                     # FastAPI routers (thin — zero business logic)
│   │   ├── agents.py
│   │   ├── runs.py
│   │   ├── audit.py
│   │   ├── governance.py
│   │   ├── analytics.py
│   │   ├── interactions.py
│   │   └── policies.py
│   ├── auth/
│   │   ├── dependencies.py      # get_current_agent FastAPI dependency
│   │   ├── hashing.py           # bcrypt via passlib
│   │   └── jwt.py               # HS256 create / decode / verify
│   ├── core/
│   │   └── config.py            # Pydantic Settings from .env
│   ├── db/
│   │   └── database.py          # async engine, session factory, init_db
│   ├── governance/
│   │   └── enforcer.py          # GovernanceEnforcer, PermittedProxy, BlockedProxy
│   ├── models/
│   │   ├── agent.py             # Agent, AgentRun, AgentEvent, AgentInteraction
│   │   └── policy.py            # Policy, AgentPolicy
│   ├── schemas/
│   │   ├── agent.py
│   │   ├── audit.py             # All response schemas (Trust/Risk included)
│   │   ├── interaction.py
│   │   ├── policy.py
│   │   └── run.py
│   ├── services/
│   │   ├── agent_service.py
│   │   ├── audit_service.py     # All analytics queries (read-only, no HTTP)
│   │   ├── execution_service.py # run_agent() main orchestrator
│   │   ├── interaction_service.py
│   │   ├── llm_service.py       # ChatGroq + LangGraph build_agent()
│   │   ├── policy_service.py
│   │   ├── risk_service.py      # RiskCalculation + formula
│   │   └── trust_service.py     # TrustCalculation + formula
│   ├── tools/                   # 10 LangChain @tool functions
│   │   ├── calculator.py
│   │   ├── currency_converter.py
│   │   ├── datetime_tool.py
│   │   ├── file_reader.py
│   │   ├── json_formatter.py
│   │   ├── text_summarizer.py
│   │   ├── uuid_generator.py
│   │   ├── weather.py
│   │   ├── wikipedia_search.py
│   │   └── word_counter.py
│   └── main.py                  # App factory, lifespan, CORS, router wiring
│
├── dashboard/
│   ├── index.html               # Single-page dashboard (all views)
│   ├── app.js                   # State, API calls, 5 chart renderers
│   └── styles.css               # Dark-mode design system (CSS variables)
│
├── docs/
│   ├── architecture.svg         # System architecture diagram
│   ├── governance_flow.svg      # Governance enforcement flow
│   └── scoring.svg              # Trust + Risk scoring diagram
│
├── tests/
│   ├── conftest.py              # Per-test in-memory SQLite + registered_agent fixture
│   ├── test_auth.py             # 24 tests
│   ├── test_runs.py             # 23 tests
│   ├── test_audit.py            # 62 tests
│   ├── test_interactions.py     # 36 tests
│   ├── test_policies.py         # 55 tests
│   ├── test_trust.py            # 60 tests
│   ├── test_risk.py             # 83 tests
│   ├── test_dashboard_charts.py # 44 tests
│   └── test_postgres_config.py  # 32 tests
│
├── Dockerfile                   # Multi-stage: builder (gcc) + runtime (non-root)
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── alembic/                     # DB migration scripts
└── sandbox/                     # file_reader tool's read-only directory
```



## Security

| Concern | Implementation |
|---------|---------------|
| Secret storage | bcrypt hash only — plaintext never persisted |
| Authentication | HS256 JWT with configurable expiry |
| Secret in API responses | `hashed_secret` absent from all Pydantic response schemas by design |
| Calculator safety | AST evaluator with explicit operator whitelist — `eval()` never called |
| File reader safety | `pathlib.resolve()` + sandbox prefix check blocks path traversal |
| CORS | Empty allow-list when `APP_ENV=production` |
| Swagger/ReDoc | Disabled when `APP_ENV=production` |
| Container | Non-root user (uid 1001), read-only mounts where possible |

---

## License

MIT — see [LICENSE](LICENSE)

---

<p align="center">
  Built end-to-end with FastAPI · LangGraph · SQLAlchemy · Chart.js · Groq
</p>
