# Railway Deploy Checklist — trading-platform-v2 (Paper Mode)

> Operator reference. Follow steps in order. Do not skip ahead.

---

## Pre-flight

- [ ] `railway.toml` is at repo root (committed in 5329343)
- [ ] All 7 Dockerfiles use `CMD ["sh", "-c", "... --port ${PORT:-NNNN}"]`
- [ ] You have four secure random values ready (see `.env.railway.example` for generation commands):
  - `MCP_API_KEY`
  - `GATEWAY_API_KEY`
  - `ADMIN_API_KEY`
  - `SECRET_KEY`
- [ ] `BINANCE_API_KEY` and `BINANCE_API_SECRET` are intentionally **empty**

---

## Step 1 — Create Railway project

1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
2. Connect repo `Ahsansabir143/mcp-web-app`
3. Cancel or skip any auto-deploy prompt — you will add services manually

---

## Step 2 — Add Postgres and Redis plugins

1. **+ New → Database → Add PostgreSQL** — Railway provisions Postgres and exposes `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`
2. **+ New → Database → Add Redis** — Railway provisions Redis and exposes `REDIS_URL`

---

## Step 3 — Add all 8 services

For each row below: **+ New → GitHub Repo → mcp-web-app** → rename → set Dockerfile + Build Context.

| Service name | Dockerfile path | Build context | Healthcheck path |
|---|---|---|---|
| `migrate` | `Dockerfile.migrate` | `/` | *(none — one-shot)* |
| `binance-ingest` | `services/binance_ingest/Dockerfile` | `/` | `/health` |
| `normalizer` | `services/normalizer/Dockerfile` | `/` | `/health` |
| `analytics` | `services/analytics/Dockerfile` | `/` | `/health` |
| `strategy-service` | `services/strategy_service/Dockerfile` | `/` | `/health` |
| `execution` | `services/execution/Dockerfile` | `/` | `/health` |
| `mcp-server` | `services/mcp_server/Dockerfile` | `/` | `/health` |
| `gateway-api` | `services/gateway_api/Dockerfile` | `/` | `/api/health` |

**Build Context must be `/` (repo root) for all services.** The Dockerfiles use `COPY shared/` and `COPY pyproject.toml` relative to root — building from a subdirectory will fail.

---

## Step 4 — Set environment variables

### 4a — Project-level shared variables

Open **Project → Settings → Shared Variables** and add:

| Variable | Value |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}` |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` |
| `ENVIRONMENT` | `staging` |
| `TRADING_MODE` | `paper` |
| `LOG_LEVEL` | `INFO` |
| `SECRET_KEY` | *(your 64-char hex value)* |

Then **attach these shared variables** to the services that need them:

| Variable | Services to attach to |
|---|---|
| `DATABASE_URL` | `migrate`, `analytics`, `strategy-service`, `execution`, `mcp-server`, `gateway-api` |
| `REDIS_URL` | `binance-ingest`, `normalizer`, `analytics`, `strategy-service`, `execution`, `mcp-server`, `gateway-api` |
| `ENVIRONMENT`, `TRADING_MODE`, `LOG_LEVEL`, `SECRET_KEY` | all 7 app services (not `migrate`) |

### 4b — Service-specific variables

**On `mcp-server` only:**
```
MCP_API_KEY = <your value>
```

**On `gateway-api` only:**
```
GATEWAY_API_KEY = <your value>
ADMIN_API_KEY   = <your different value>
```

**On `binance-ingest` only:**
```
BINANCE_API_KEY         =
BINANCE_API_SECRET      =
BINANCE_FUTURES_API_KEY =
BINANCE_FUTURES_API_SECRET =
BINANCE_USE_TESTNET     = false
SPOT_STREAMS            = btcusdt@aggTrade,btcusdt@depth5@100ms,ethusdt@aggTrade,ethusdt@depth5@100ms
FUTURES_STREAMS         =
```

> **Critical:** `SPOT_STREAMS` must be non-empty or no market data will flow and all downstream services will be idle.

---

## Step 5 — Deploy in order

Deploy one service at a time. Wait for each to show **Running** (or exit 0 for migrate) before proceeding.

```
1. migrate          ← deploy first; wait for "upgrade head" success in logs; disable auto-deploy
2. binance-ingest
3. normalizer
4. analytics
5. strategy-service
6. execution
7. mcp-server
8. gateway-api      ← deploy last
```

For `migrate`:
- Logs should end with `Running upgrade ... -> <rev>` or `INFO ... (nothing to do)` if already applied
- Railway may show it as "failed" because the container exits — this is expected for a one-shot job
- After confirming success: **Service Settings → Disable auto-deploy** on the `migrate` service

---

## Step 6 — First smoke tests

Replace `GATEWAY_URL` and `MCP_URL` with the Railway-assigned public domain for each service.
Run the scripts in `scripts/` after exporting the required env vars:

```bash
export GATEWAY_URL=https://gateway-api-xxx.up.railway.app
export GATEWAY_API_KEY=<your value>
export ADMIN_API_KEY=<your value>
export MCP_URL=https://mcp-server-xxx.up.railway.app
export MCP_API_KEY=<your value>

bash scripts/smoke_gateway.sh
bash scripts/smoke_mcp.sh
bash scripts/smoke_paper_trade.sh
```

### Quick manual checks

```bash
# 1. Gateway health (no auth needed)
curl -s https://GATEWAY_URL/api/health

# 2. Health detail (checks DB + Redis)
curl -s https://GATEWAY_URL/api/health/detail

# 3. Confirm trading mode is paper (requires ADMIN_API_KEY)
curl -s -H "X-API-Key: ADMIN_API_KEY" https://GATEWAY_URL/api/ops/trading-mode

# 4. Stream lengths (wait 60s after binance-ingest starts)
curl -s -H "X-API-Key: ADMIN_API_KEY" https://GATEWAY_URL/api/ops/metrics | python -m json.tool
```

---

## Paper-mode safety checklist

- [ ] `TRADING_MODE=paper` confirmed in every service env panel
- [ ] `BINANCE_API_KEY` and `BINANCE_API_SECRET` are empty on `binance-ingest`
- [ ] `GET /api/ops/trading-mode` returns `"paper_only"` or `"paper"`
- [ ] `GET /api/ops/metrics` shows `"trading_mode": "paper_only"` and `"emergency_stop_active": false`
- [ ] No service log shows `"live"` or `"placing real order"`

---

## Rollback

If any service fails to start:
1. Check Railway logs for the first `ERROR` or exception traceback
2. Fix the env var or config
3. Click **Redeploy** on that service only
4. Other running services are unaffected — they will reconnect automatically
