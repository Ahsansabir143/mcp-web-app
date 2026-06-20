# Trading Platform v2 — Backend Architecture Overview

> Written for a senior backend or trading-systems engineer joining the project.
> Last updated: Phase 9 (ops surface + observability).

---

## Table of Contents

1. [Service Map](#1-service-map)
2. [Data Flow](#2-data-flow)
3. [Redis Keys and Streams](#3-redis-keys-and-streams)
4. [Database Tables](#4-database-tables)
5. [MCP Tools](#5-mcp-tools)
6. [Gateway Routes](#6-gateway-routes)
7. [Admin / Ops Routes](#7-admin--ops-routes)
8. [Safety Model](#8-safety-model)
9. [Deployment Notes](#9-deployment-notes)

---

## 1. Service Map

All services are Python 3.12 async, FastAPI + SQLAlchemy 2.0 async + asyncpg. Monorepo
pip-editable install: `pip install -e .` from repo root.

| Service | Port | Responsibility |
|---|---|---|
| **binance_ingest** | 8001 | Maintains persistent WebSocket connections to Binance (public + private streams). Publishes raw messages to `stream:binance:raw`. |
| **normalizer** | 8002 | Consumes `stream:binance:raw`, parses every Binance WebSocket event, writes normalized hot-state to Redis, and republishes to `stream:binance:normalized`. |
| **analytics** | 8003 | Consumes `stream:binance:normalized`, runs flow analytics (CVD, delta, RVOL), book analytics, funding pressure, liquidation clusters, and technical indicators. Writes per-symbol hot-state and a unified `analytics:{mt}:{sym}:snapshot` JSON blob. Publishes signals to `stream:analytics:derived`. |
| **strategy_service** | 8004 | Consumes `stream:analytics:derived`. For each active strategy, runs `StrategyEvaluator.evaluate()` against the snapshot. On signal, publishes a `TradeIntent` to `stream:strategy:intents`. Manages lifecycle state machine via `LifecycleManager`. |
| **execution** | 8005 | Consumes `stream:strategy:intents`. Runs risk checks + approval level gating, then dispatches to a trading adapter (currently only PaperAdapter). Writes `ExecutionJob` + `Order` + `Fill` records to Postgres. Reconciliation consumer tails `stream:binance:normalized` for USER_ORDER events to close open paper jobs. |
| **mcp_server** | 8006 | Remote MCP server (HTTP + SSE transport, protocol 2024-11-05). Exposes 9 tools to Claude for read, simulation, and paper-safe control operations. All tool implementations delegate to shared facades; no parallel business logic. |
| **gateway_api** | 8007 | Public REST API + internal admin/ops REST API. All routes delegate to the same facades used by MCP tools. User-facing routes require `X-API-Key` (gateway key); ops routes require a separate admin key. |

### Shared packages (`shared/`)

| Module | Contents |
|---|---|
| `shared/db/` | SQLAlchemy async session factory, Base, all ORM models |
| `shared/redis/` | `RedisClient`, `stream_publish`, `stream_read_group`, `RedisKeys`, `StreamNames` |
| `shared/schemas/` | Pydantic v2 models: `TradeIntent`, `UnifiedDecisionSnapshot`, all enums |
| `shared/risk/` | `RiskEngine` + `RiskLimits` (stateless checks, called by execution) |
| `shared/policies/` | `ApprovalPolicy`, `PermissionsPolicy` |
| `shared/metrics.py` | In-process `MetricsRegistry` (counters + gauges) |
| `shared/utils/logging.py` | `StructuredLogger` wrapper, `setup_logging()` |

---

## 2. Data Flow

```
Binance WebSocket
      │
      ▼
┌─────────────────┐
│  binance_ingest │  public streams (book, trades, klines, funding, OI, liq)
│  (port 8001)    │  private streams (USER_DATA: orders, fills, positions)
└────────┬────────┘
         │ stream:binance:raw
         ▼
┌─────────────────┐
│   normalizer    │  parse raw → NormalizedEvent
│  (port 8002)    │  write hot-state: market:*, account:*
└────────┬────────┘
         │ stream:binance:normalized
         ├──────────────────────────────────────────────────────┐
         ▼                                                      │
┌─────────────────┐                                             │ (USER_ORDER events)
│   analytics     │  flow, book, funding, indicators            │
│  (port 8003)    │  write analytics:* + snapshot              ▼
└────────┬────────┘                              ┌──────────────────────┐
         │ stream:analytics:derived               │     execution        │
         ▼                                       │  (reconciliation     │
┌─────────────────┐                              │   consumer)          │
│ strategy_service│  evaluate() → TradeIntent    └──────────┬───────────┘
│  (port 8004)    │  if signal fired                        │
└────────┬────────┘                                         │ fills closed
         │ stream:strategy:intents                          │
         ▼                                                  │
┌──────────────────────────────────────────────────────────┤
│              execution service (port 8005)               │
│                                                          │
│  1. Validate intent (risk checks, approval level,        │
│     kill switch, symbol pause, cooldown, circuit breaker)│
│  2. Dispatch to PaperAdapter (no real orders yet)        │
│  3. Write ExecutionJob → Order → Fill to Postgres        │
│  4. Publish result to stream:execution:events            │
└──────────┬───────────────────────────────────────────────┘
           │ stream:execution:events
           │
           ├──────────────────────────┐
           ▼                          ▼
┌──────────────────┐       ┌──────────────────────┐
│   mcp_server     │       │    gateway_api        │
│  (port 8006)     │       │   (port 8007)         │
│                  │       │                       │
│  Claude → tools  │       │  REST clients → routes│
│  → facades       │       │  → same facades       │
└──────────────────┘       └──────────────────────┘
```

### Key rules

- **Facades live in `services/mcp_server/facades/`** and are imported by both the MCP tool handlers and the gateway routes. There is no parallel business logic.
- The **strategy consumer** calls `evaluator.evaluate()` which is synchronous and stateless. Only the consumer publishes the resulting intent. The `simulate_*` MCP tools also call `evaluate()` but never publish — this is enforced in `simulation.py`.
- The **execution service** is the only place intents become real actions. All risk and approval gating is there.
- Reconciliation runs inside the execution service as a separate async task (`NormalizedEventConsumer` + `ReconciliationLoop`).

---

## 3. Redis Keys and Streams

### Key namespaces (`shared/redis/keys.py`)

All key strings are constructed **only** via `RedisKeys` static methods. Never construct them inline.

| Key pattern | Type | Written by | Read by | Purpose |
|---|---|---|---|---|
| `market:{mt}:{sym}:price` | String (JSON) | normalizer | analytics, execution, mcp | Latest mark/last price |
| `market:{mt}:{sym}:book` | String (JSON) | normalizer | analytics | Full order book snapshot |
| `market:{mt}:{sym}:book_ticker` | String (JSON) | normalizer | mcp, execution | Best bid/ask |
| `market:{mt}:{sym}:funding` | String (JSON) | normalizer | analytics | Funding rate data |
| `market:{mt}:{sym}:mark` | String (JSON) | normalizer | analytics | Mark price |
| `analytics:{mt}:{sym}:snapshot` | String (JSON) | analytics | strategy, mcp | `UnifiedDecisionSnapshot` — full input to `evaluate()` |
| `analytics:{mt}:{sym}:cvd` | String (JSON) | analytics | analytics snapshot | Cumulative Volume Delta |
| `analytics:{mt}:{sym}:rvol` | String (JSON) | analytics | analytics snapshot | Relative Volume |
| `analytics:{mt}:{sym}:liquidation_clusters` | String (JSON) | analytics | analytics snapshot | Liq cluster levels |
| `account:{user_id}:positions` | String (JSON) | normalizer | execution risk | Open positions hash |
| `kill_switch:{account_id}` | String | ops API | execution | Blocks all intents for account |
| `pause:user:{account_id}` | String | ops API | execution | Soft pause (less severe than kill switch) |
| `pause:symbol:{account_id}:{symbol}` | String | ops API | execution | Per-symbol pause |
| `cooldown:{account_id}:{symbol}` | String | execution | execution | Post-trade re-entry lock |
| `circuit_breaker:{account_id}` | String | execution | execution | Auto-trip on excessive loss |
| `rate_limit:{api_key}:{minute_bucket}` | String (counter) | gateway | gateway | Per-key rate limiting |
| `global:trading_mode` | String | ops API | advisory | `paper_only` / `mixed` / `emergency_stop` |
| `global:emergency_stop` | String | ops API | advisory | Set when mode = emergency_stop |

`{mt}` = market type: `futures` or `spot`.

### Streams (`shared/redis/streams.py`)

| Stream | Producer | Consumer(s) | Consumer group | Purpose |
|---|---|---|---|---|
| `stream:binance:raw` | binance_ingest | normalizer | `normalizer-group` | Raw Binance WebSocket messages |
| `stream:binance:normalized` | normalizer | analytics, execution-recon | `analytics-group`, `execution-reconcile` | Parsed NormalizedEvent payloads |
| `stream:analytics:derived` | analytics | strategy_service | `strategy-group` | Analytics signals + snapshots |
| `stream:strategy:intents` | strategy_service, mcp_server | execution | `execution-group` | TradeIntent payloads |
| `stream:execution:events` | execution | (subscribers TBD) | — | Execution state change events |
| `stream:mcp:audit` | (reserved) | — | — | Reserved for future MCP audit trail |

All streams use `MAXLEN ~50000` (approximate trimming). Consumer groups are created with `id=0` (replay from beginning) on first read. Adjust in production.

---

## 4. Database Tables

Schema lives in `migrations/versions/`. All tables use UUID primary keys and `TimestampMixin` (created_at, updated_at).

### Market / Account

| Table | Purpose |
|---|---|
| `users` | User accounts. Referenced by strategies, MCP sessions. |
| `exchange_accounts` | One per user per venue. Holds trading_mode, approval_level, is_active. |
| `api_credential_refs` | Encrypted API key references (AES-256-GCM). Never stores keys in plaintext. |
| `account_update_reasons` | Audit log of account balance/position change events from Binance. |

### Approval & Risk

| Table | Purpose |
|---|---|
| `approval_levels` | Per-account L0–L4 approval level, paper_only flag, allowed/denied symbols. |
| `risk_policies` | Per-account risk limits: max position size, leverage, daily loss, concurrent positions, cooldown, circuit breaker thresholds. |

### Strategy

| Table | Purpose |
|---|---|
| `strategies` | Strategy identity: name, state, market_type, symbol_filters, current_version. |
| `strategy_versions` | Immutable versioned snapshots of rules + parameters. `current_version` FK from strategies. |
| `strategy_runs` | Execution context per run (simulation, live-sim, paper). |
| `strategy_evaluations` | One row per `evaluate()` call: signal, direction, confidence, intent_id. |
| `strategy_actions` | Audit trail of lifecycle state changes (state_change, rollback, pause). |
| `strategy_rollbacks` | Records when a strategy version is rolled back. |

### Execution

| Table | Purpose |
|---|---|
| `execution_jobs` | One per processed TradeIntent. Tracks status through lifecycle (pending → submitted → filled). Contains intent_json, result_json, error. |
| `execution_events` | State transition events per job. Useful for debugging stuck jobs. |
| `orders` | Exchange order records (paper or real). One-to-many with execution_jobs. |
| `fills` | Fill records. Deduped by `exchange_trade_id`. |

### Audit

| Table | Purpose |
|---|---|
| `incident_log` | Execution anomalies: orphan fills, stale orders, reconciliation mismatches. Severity: info/warning/error/critical. |
| `audit_log` | Generic user-action audit trail. |
| `mcp_sessions` | MCP session records (for future audit). |
| `mcp_tool_calls` | Per-call log of MCP tool invocations (for future audit). |

---

## 5. MCP Tools

The MCP server runs on port 8006. Claude connects via:

```
GET  /sse                       → SSE channel; receives endpoint URL
POST /messages?session_id=<id>  → JSON-RPC 2.0; X-API-Key: <MCP_API_KEY>
```

Protocol: MCP 2024-11-05. All tools return `{"content": [{"type": "text", "text": "<json>"}], "isError": false}`.

### Read tools

| Tool | Required inputs | Output |
|---|---|---|
| `get_symbol_snapshot` | `symbol` (str), `market_type` (default: futures) | Full analytics snapshot from Redis: price, book, CVD, delta, RVOL, funding, liquidation clusters, indicators |
| `list_strategies` | `symbol?`, `state?`, `limit?` (default 50) | List of strategy summaries: id, name, state, market_type, symbol_filters, current_version |
| `get_strategy_details` | `strategy_id` (UUID) | Full strategy: version rules, parameters, approval_required, last evaluation result |
| `get_recent_executions` | `strategy_id?`, `symbol?`, `limit?` (default 20) | Execution job list: status, side, fill_price, exchange_order_id |
| `get_incidents` | `symbol?`, `since_ts?` (ms), `limit?` (default 50) | Incident log entries: type, severity, description, context |

### Simulation tools

| Tool | Required inputs | Output |
|---|---|---|
| `simulate_strategy_on_snapshot` | `strategy_id`, `symbol`, `market_type?` | Dry-run evaluation: signal, direction, confidence, explanation, hypothetical_intent (if signal). **Nothing is published.** |
| `simulate_strategy_on_range` | `strategy_id`, `symbol`, `start_ms?`, `end_ms?` | Stub — returns `not_implemented` with explanation. Historical replay deferred. |

### Control tools (paper-safe)

| Tool | Required inputs | Output |
|---|---|---|
| `request_paper_trade` | `strategy_id`, `symbol`, `side` (BUY/SELL), `size_usd?` or `size?`, `reason?` | Publishes TradeIntent to `stream:strategy:intents`. Strategy must be in paper_active / assisted_live / bounded_auto_live. Execution service enforces all risk checks. |
| `update_strategy_state` | `strategy_id`, `target_state`, `justification`, `approval_level?` | Validates state transition via LifecycleManager, writes StrategyAction audit record. Returns previous + new state. |

---

## 6. Gateway Routes

Base URL: `http://gateway-api:8007`

All routes require `X-API-Key: <GATEWAY_API_KEY>` and are subject to 60 req/min rate limiting (Redis sliding window per key per minute).

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Simple liveness probe. No auth. |
| GET | `/api/health/detail` | Redis PING + DB SELECT 1. Returns degraded if either fails. No auth. |
| GET | `/api/market/snapshot/{market_type}/{symbol}` | Latest analytics snapshot for a symbol. 404 if no data. |
| GET | `/api/strategies` | List strategies. Query params: `symbol`, `state`, `limit` (default 50). |
| GET | `/api/strategies/{strategy_id}` | Strategy detail with version rules and last evaluation. |
| POST | `/api/strategies/{strategy_id}/simulate` | Body: `{"symbol": "...", "market_type": "futures"}`. Dry-run evaluation. |
| POST | `/api/strategies/{strategy_id}/state` | Body: `{"target_state": "...", "justification": "...", "approval_level": "..."}`. |
| GET | `/api/executions/recent` | Query params: `strategy_id`, `symbol`, `limit`. |
| GET | `/api/incidents` | Query params: `symbol`, `since_ts`, `limit`. |
| POST | `/api/paper-trade` | Body: `{"strategy_id", "symbol", "side", "size_usd"/"size", "reason"}`. 400 if strategy not in emit state. 404 if not found. |

---

## 7. Admin / Ops Routes

Base URL: `http://gateway-api:8007`

All ops routes require `X-API-Key: <ADMIN_API_KEY>` (separate from the gateway key). These are **internal-only** and must not be exposed to end-users or proxied through public-facing infrastructure.

| Method | Path | Description |
|---|---|---|
| GET | `/api/ops/streams` | Length + consumer group lag for all 6 Redis streams. Useful for detecting backlog. |
| GET | `/api/ops/strategy/{id}/status` | Full ops view: state, is_emitting_intents, last evaluation, last action, last intent emission. |
| GET | `/api/ops/execution/jobs` | Query params: `status`, `symbol`, `strategy_id`, `limit` (max 200). Returns full job detail including intent_json, error text. |
| GET | `/api/ops/trading-mode` | Read current global trading mode from Redis. |
| POST | `/api/ops/trading-mode` | Body: `{"mode": "paper_only"\|"mixed"\|"emergency_stop", "reason": "..."}`. Sets `global:trading_mode` + `global:emergency_stop` in Redis. |
| GET | `/api/ops/kill-switch/{account_id}` | Read kill switch, user pause, circuit breaker state for account. |
| POST | `/api/ops/kill-switch` | Body: `{"action": "activate"\|"clear"\|"pause_user"\|"resume_user"\|"pause_symbol"\|"resume_symbol"\|"trip_circuit_breaker"\|"reset_circuit_breaker", "account_id", "symbol"?, "ttl_s"?}`. Wraps `KillSwitchControl`. |
| GET | `/api/ops/metrics` | Aggregated metrics: stream lengths, consumer lag, safety control state, incident counts by severity, in-process gateway counters. |

---

## 8. Safety Model

### Paper-only enforcement

Paper mode is enforced at the **execution service layer**, not at the gateway or MCP layer. Every `ExecutionJob` carries a `trading_mode` field. The `PaperAdapter` (the only adapter currently wired) does not send real orders to Binance. The `AccountContext.paper_only` flag is loaded from DB for every intent and checked before dispatch.

Attempting to submit a TradeIntent via MCP or the gateway does **not** bypass this — the intent goes to `stream:strategy:intents`, and the execution service consumer validates it independently.

### Approval levels

| Level | Value | Can do |
|---|---|---|
| L0 | `l0_readonly` | Read-only |
| L1 | `l1_simulation` | Simulation runs |
| L2 | `l2_paper` | Paper trading |
| L3 | `l3_assisted_live` | Assisted live (human confirms each trade) |
| L4 | `l4_bounded_auto` | Bounded autonomous (within limits, no human confirm) |

`LifecycleManager.transition(from_state, to_state, approval_level)` validates both the state graph and the minimum required approval level for the target state. This is enforced in both the strategy facade and the strategy consumer.

### Risk checks (execution service, `services/execution/risk/`)

All checks run in `RiskEngine.run_checks()` before any adapter call:

1. `kill_switch` — Redis EXISTS on `kill_switch:{account_id}`
2. `user_pause` — Redis EXISTS on `pause:user:{account_id}`
3. `symbol_pause` — Redis EXISTS on `pause:symbol:{account_id}:{symbol}`
4. `symbol_cooldown` — Redis EXISTS on `cooldown:{account_id}:{symbol}`
5. `circuit_breaker` — Redis EXISTS on `circuit_breaker:{account_id}`
6. `approval_level` — minimum level required for target state
7. `account_mode` — trading_mode must match intent type
8. `symbol_policy` — symbol in allowed list / not in denied list
9. `max_position_size` — position value vs `RiskPolicy.max_position_size_usd`
10. `max_daily_loss` — realized PnL from Redis vs `RiskPolicy.max_daily_loss_usd`
11. `max_concurrent_positions` — open position count vs `RiskPolicy.max_concurrent_positions`
12. `funding_window` — if funding rate > threshold, blocks entry (futures only)

### Kill switch and circuit breaker

- **Kill switch**: operator-set via `POST /api/ops/kill-switch`. Blocks all intents for an account. TTL-based, expires automatically.
- **Circuit breaker**: auto-tripped by the execution service when loss exceeds `circuit_breaker_threshold` (default 5%) in the `circuit_breaker_window_seconds` window. Operator-reset via `/api/ops/kill-switch`.
- **Emergency stop**: global mode via `POST /api/ops/trading-mode` `{"mode": "emergency_stop"}`. Sets `global:emergency_stop` in Redis. Services that check this key should halt intent processing.

---

## 9. Deployment Notes

### Required environment variables

Each service reads from a `.env` file in the repo root (pydantic-settings). In production, set these as real environment variables; do not commit `.env`.

| Variable | Used by | Description |
|---|---|---|
| `DATABASE_URL` | All services | `postgresql+asyncpg://user:pass@host:5432/db` |
| `REDIS_URL` | All services | `redis://host:6379/0` |
| `SECRET_KEY` | Shared | AES master key for credential encryption (32 bytes, hex or base64) |
| `MCP_API_KEY` | mcp_server, clients | Key for `X-API-Key` on MCP POST /messages |
| `GATEWAY_API_KEY` | gateway_api, clients | Key for `X-API-Key` on public gateway routes |
| `ADMIN_API_KEY` | gateway_api, ops tools | Key for `X-API-Key` on `/api/ops/*` routes — keep separate from gateway key |
| `BINANCE_API_KEY` | binance_ingest | Binance REST/WS API key (for private streams) |
| `BINANCE_API_SECRET` | binance_ingest | Binance REST/WS API secret |
| `LOG_LEVEL` | All services | `DEBUG`, `INFO` (default), `WARNING`, `ERROR` |
| `TRADING_MODE` | All services | `paper` (default) or `live`. Core enforcement is in execution regardless. |
| `ENVIRONMENT` | All services | `dev`, `staging`, `prod` |

### Encrypted credentials

Per-account API credentials (for future live trading) are stored encrypted in `api_credential_refs`. Encryption uses AES-256-GCM with a 12-byte nonce. The encryption key derives from `SECRET_KEY`. See `services/execution/credentials.py`.

### Database migrations

```bash
alembic upgrade head
```

Two migrations exist: initial schema (001) and constraint/index additions (002).

### Starting services (development)

```bash
# From repo root, each in a separate terminal or via a process manager:
python -m services.binance_ingest.main
python -m services.normalizer.main
python -m services.analytics.main
python -m services.strategy_service.main
python -m services.execution.main
python -m services.mcp_server.main
python -m services.gateway_api.main
```

Or use the Procfile / Docker Compose if available in `infra/`.

### Health checks

Each service exposes:
- `GET /health` → `{"status": "ok"}` (no auth, simple liveness)
- `GET /health/detail` → service-specific detail (consumer stats, connection states)

The gateway additionally exposes `GET /api/health/detail` which checks Redis PING and DB connectivity.

### Deployment constraints

- All services share a single Redis instance (multi-DB is not used; key namespaces prevent collisions).
- Postgres and Redis must be reachable before any service starts.
- The mcp_server and gateway_api import from `services/mcp_server/facades/` directly (monorepo import). They must run from the repo root where `pip install -e .` was run.
- Consumer groups are created automatically with `id=0` on first connect (`mkstream=True`). In production you may want to set `id=$` (deliver only new messages) to avoid replaying historical data on service restart. Change `stream_read_group()` in `shared/redis/client.py` accordingly.
- SSE sessions in mcp_server are in-memory. Restarting the service drops all active Claude sessions; clients will need to reconnect.

### What is NOT yet implemented

- **Live trading adapter**: only `PaperAdapter` exists. A `BinanceAdapter` is the next phase.
- **Historical snapshot store**: `simulate_strategy_on_range` is a stub. Requires a time-series store of `analytics:snapshot` data.
- **Authentication beyond API keys**: MCP and gateway use static API keys. JWT or OAuth2 is not implemented.
- **Multi-venue support**: architecture is Binance-only. Other venues would require new ingest + normalizer adapters.
- **Alerting / PagerDuty integration**: incident_log exists but no webhook fanout.
- **Metrics export**: `shared/metrics.py` provides in-process counters; no Prometheus scrape endpoint or external export is wired up.
