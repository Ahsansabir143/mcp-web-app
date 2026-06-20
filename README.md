# Trading Platform v2 — Binance Intelligence & Execution

Binance-only trading intelligence and execution platform with Claude connected through MCP. Production-grade Python monorepo.

## Architecture

```
Claude (reasoning layer)
    │  MCP bounded tool interface
    ▼
mcp-server ─────────────────────────────────────────────────
                                                            │
gateway-api (REST + WebSocket, web/mobile clients)          │
    │                                                       │
    ▼                                                       ▼
strategy-service ──► execution (paper / live orders)
    │                      │
    │               private user stream
    ▼                      │
analytics ◄────────────────┘
    │
    ▼
normalizer
    │
    ▼
binance-ingest (WebSocket streams + REST polling)
    │
    ▼
Redis Streams + Redis hot state
    │
    ▼
Postgres (persistent history + all business records)
```

## Services

| Service | Port | Purpose |
|---------|------|---------|
| gateway-api | 8000 | REST + WebSocket API |
| binance-ingest | 8001 | Raw stream ingest |
| normalizer | 8002 | Payload normalization + hot state |
| analytics | 8003 | Derived metrics + decision snapshots |
| strategy-service | 8004 | Strategy CRUD, eval, intents |
| execution | 8005 | Risk validation + order routing |
| mcp-server | 8006 | Claude MCP tool interface |

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Shared foundation (schemas, Redis, DB, utils, infra) | ✅ Complete |
| 2 | Binance ingest (all stream types + private user stream) | 🔲 Next |
| 3 | Normalizer (all event types + hot state) | 🔲 |
| 4 | Analytics (10 engines + decision snapshot builder) | 🔲 |
| 5 | Strategy service (CRUD, versioning, eval, simulation) | 🔲 |
| 6 | Execution — paper mode (validation + risk engine) | 🔲 |
| 7 | Execution — live (Binance orders + fill reconciliation) | 🔲 |
| 8 | MCP server (25 bounded tools + OAuth + audit) | 🔲 |
| 9 | Gateway API (all REST + WebSocket routes) | 🔲 |
| 10 | Hardening (tests, observability, incident workflows) | 🔲 |

## Quick Start (local dev)

### Prerequisites
- Python 3.12
- Docker + Docker Compose

### 1. Install shared package

```powershell
cd C:\Users\ahsan\trading-platform-v2
pip install -e .
```

### 2. Copy and configure env

```powershell
Copy-Item .env.example .env
# Edit .env with your Binance keys and secrets
```

### 3. Start infrastructure

```powershell
docker compose -f infra/docker-compose.yml up postgres redis -d
```

### 4. Run migrations

```powershell
$env:PYTHONPATH = "C:\Users\ahsan\trading-platform-v2"
alembic -c migrations/alembic.ini upgrade head
```

### 5. Start a service (example: gateway-api)

```powershell
$env:PYTHONPATH = "C:\Users\ahsan\trading-platform-v2"
python -m uvicorn services.gateway_api.main:app --host 0.0.0.0 --port 8000 --reload
```

### 6. Run tests

```powershell
$env:PYTHONPATH = "C:\Users\ahsan\trading-platform-v2"
python -m pytest tests/unit/ -v
```

## Key Design Principles

1. **Claude is the controller** — never the direct execution layer
2. **Strategy ≠ Execution** — TradeIntent objects cross the boundary, never raw orders
3. **Execution is the only Binance caller** — no other service may place orders
4. **Private user streams are truth** — fills, balances, positions come from there
5. **Raw ≠ Derived** — never mixed in the same API field
6. **MCP is bounded** — approval levels and symbol policies enforced on every tool call
7. **Paper = Live pipeline** — same validation, different execution path
8. **Everything is auditable** — audit_log, mcp_tool_calls, execution_events for every action
9. **Idempotent jobs** — job_id + deterministic client_order_id prevent duplicate execution

## Redis Key Domains

All keys are built via `shared.redis.keys.RedisKeys` — never inline strings.

- `market:{type}:{symbol}:*` — hot market state
- `analytics:{type}:{symbol}:*` — derived analytics
- `account:{user_id}:*` — account state
- `risk:{user_id}:*` — risk state and limits
- `strategy:{id}:*` — active strategy state
- `approval:{user_id}:level` — current approval level
- `kill_switch:{account_id}` — global kill switch flag
- `pause:user:{account_id}` — per-user trading pause
- `pause:symbol:{account_id}:{symbol}` — per-symbol pause
- `cooldown:{account_id}:{symbol}` — post-loss cooldown
- `job:lock:{job_id}` — execution job idempotency lock

## Redis Streams

- `stream:binance:raw` — raw Binance payloads
- `stream:binance:normalized` — canonical NormalizedEvent records
- `stream:analytics:derived` — analytics engine outputs
- `stream:strategy:intents` — TradeIntent objects
- `stream:execution:events` — execution lifecycle events
- `stream:mcp:audit` — MCP tool call audit trail

## Database

28 tables across 5 domains:

- **Market**: symbols, candles, trade_history, funding_history, oi_history, liquidation_events, wall_events, market_snapshots
- **Account**: users, exchange_accounts, api_credentials_ref, balances, positions, orders, fills
- **Execution**: execution_jobs, execution_events, risk_policies, approval_levels
- **Strategy**: strategies, strategy_versions, strategy_runs, strategy_evaluations, strategy_actions, strategy_rollbacks
- **Audit**: mcp_sessions, mcp_tool_calls, audit_log, incident_log, account_update_reasons

## Approval Levels

| Level | Value | Can Do |
|-------|-------|--------|
| L0 | l0_readonly | Read data only |
| L1 | l1_simulation | Run simulations |
| L2 | l2_paper | Paper trading |
| L3 | l3_assisted_live | Live with confirmation |
| L4 | l4_bounded_auto | Bounded auto-execution |
