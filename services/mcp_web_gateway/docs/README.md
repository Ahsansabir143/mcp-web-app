# MCP Web Gateway

OAuth 2.1-protected SSE proxy that exposes read-only trading-platform tools to
remote MCP clients (Claude web, Claude.ai, any MCP-compatible host).

---

## What this service does

| Concern | Behaviour |
|---|---|
| Auth role | **Resource server** only — validates tokens, does not issue them |
| Transport | SSE (`GET /sse`) + JSON-RPC messages (`POST /messages?session_id=…`) |
| Token type | JWT Bearer; RS256/ES256 via JWKS (production) or HS256 static secret (dev) |
| Tool exposure | 12 read-only tools (see below) |
| Write tools | **Never** forwarded — blocked at the policy layer before the proxy is called |
| Live trading | Cannot be enabled through this gateway |
| Internal key | `MCP_INTERNAL_API_KEY` is used server-side only; callers present only their JWT |

---

## Architecture

```
MCP Client (Claude web / remote host)
      │  Authorization: Bearer <jwt>
      │
      ▼
┌─────────────────────────────┐
│     mcp-web-gateway :8007   │
│                             │
│  auth.py    ← validate JWT  │
│  policy.py  ← scope + allow │
│  proxy.py   → SSE call      │
│  audit.py   → JSON log line │
└──────────────┬──────────────┘
               │  X-API-Key: <internal key>
               ▼
   ┌─────────────────────────┐
   │  mcp-server :8006       │
   │  (internal, unchanged)  │
   └─────────────────────────┘
```

For each approved `tools/call` the gateway opens a one-shot SSE session to the
internal MCP server (connect → initialize → call → close). The internal server
never receives or validates the client's JWT.

---

## Auth Provider Recommendation

### Comparison — Auth0 vs AWS Cognito vs Keycloak

| Criterion | Auth0 | AWS Cognito | Keycloak |
|---|---|---|---|
| Setup time to first working token | ~15 min | ~30 min | ~60 min (self-hosted) |
| JWKS endpoint | Automatic at `{tenant}/.well-known/jwks.json` | Automatic at Cognito pool URL | Automatic |
| Custom scopes | Easy — API + scopes in dashboard | Supported (resource servers) | Supported |
| Railway friendliness | Hosted SaaS — zero infra | Hosted SaaS — zero infra | Needs a separate Railway service or external host |
| Claude web / MCP client compatibility | Excellent — standard OAuth 2.1 PKCE | Good — standard PKCE | Good |
| Issuer URL stability | `https://{tenant}.auth0.com/` | `https://cognito-idp.{region}.amazonaws.com/{pool_id}` | `https://{host}/realms/{realm}` |
| Free tier | 7,500 MAU free | 50,000 MAU free | Self-hosted — no cost, but you run the server |
| Long-term maintainability | Managed, no ops burden | Managed, tight AWS lock-in | Full control, operational overhead |

### Recommendation: **Auth0**

**Why Auth0 for this project:**
- Fastest path to a working Claude web connection — you can have JWKS and a test token in 15 minutes.
- The free tier (7,500 MAU) covers solo/team use with zero cost.
- No infra to maintain — Auth0 is a Railway-friendly SaaS.
- Auth0's `{tenant}.auth0.com/.well-known/jwks.json` is the simplest value to put in `OAUTH_JWKS_URL`.
- Machine-to-machine (M2M) apps let you generate tokens for scripted testing immediately.
- Claude web's MCP connector performs standard OAuth 2.1 PKCE, which Auth0 handles natively.

**When to choose Cognito instead:** if your stack is already AWS-heavy and you want consolidated IAM.

**When to choose Keycloak:** if you need full data sovereignty or complex enterprise SSO flows.

---

## Auth0 Setup (15-minute path to first token)

1. Create a free account at https://auth0.com
2. **Create an API** (Applications → APIs → Create API)
   - Name: `MCP Web Gateway`
   - Identifier (audience): `mcp-web-gateway`
   - Signing: RS256 (default)
3. **Add custom scopes** to the API (Permissions tab):
   - `mcp:tools:read`
   - `mcp:account:read`
   - `mcp:strategy:read`
4. **Create a Machine-to-Machine App** (for scripted testing):
   - Applications → Create → Machine to Machine → select your API
   - Grant all three scopes
   - Note the Domain, Client ID, Client Secret
5. **Get your JWKS URL**: `https://{YOUR_TENANT}.auth0.com/.well-known/jwks.json`
6. **Get a test token** (M2M curl):
   ```powershell
   $body = @{
       grant_type    = "client_credentials"
       client_id     = "YOUR_CLIENT_ID"
       client_secret = "YOUR_CLIENT_SECRET"
       audience      = "mcp-web-gateway"
       scope         = "mcp:tools:read mcp:account:read mcp:strategy:read"
   }
   $resp = Invoke-RestMethod -Method POST `
       -Uri "https://YOUR_TENANT.auth0.com/oauth/token" `
       -Body $body
   $env:TOKEN = $resp.access_token
   ```

---

## Environment Variables

### Local development (.env)

```ini
# OAuth — dev mode (HS256 static secret, never use in production)
OAUTH_JWT_SECRET=local-dev-secret
OAUTH_AUDIENCE=mcp-web-gateway
OAUTH_JWKS_URL=
OAUTH_ISSUER_URL=

# Internal MCP server (must be running locally)
MCP_INTERNAL_URL=http://localhost:8006
MCP_INTERNAL_API_KEY=<value of MCP_API_KEY from your local .env>

# Public gateway URL (for metadata endpoint)
MCP_RESOURCE_URL=http://localhost:8007

# Leave empty for local dev (no auth server running)
MCP_AUTHORIZATION_SERVERS=

# Logging
LOG_LEVEL=DEBUG
ENVIRONMENT=dev
```

### Railway staging / production

```ini
# OAuth — production JWKS path (Auth0 example)
OAUTH_JWKS_URL=https://YOUR_TENANT.auth0.com/.well-known/jwks.json
OAUTH_ISSUER_URL=https://YOUR_TENANT.auth0.com/
OAUTH_AUDIENCE=mcp-web-gateway
# Do NOT set OAUTH_JWT_SECRET in production

# Internal MCP server — Railway private network URL
MCP_INTERNAL_URL=http://mcp-server.railway.internal:8006
MCP_INTERNAL_API_KEY=<same value as MCP_API_KEY on the mcp-server service>

# Public HTTPS URL Railway assigns this service
MCP_RESOURCE_URL=https://YOUR-GATEWAY-SUBDOMAIN.up.railway.app

# Authorization server base URL(s) advertised in metadata
MCP_AUTHORIZATION_SERVERS=https://YOUR_TENANT.auth0.com

# Logging
LOG_LEVEL=INFO
ENVIRONMENT=staging

# DO NOT set PORT — Railway injects it automatically
```

### Variable reference

| Variable | Required | Description |
|---|---|---|
| `OAUTH_JWKS_URL` | Production | JWKS endpoint of the auth server |
| `OAUTH_ISSUER_URL` | Optional | JWT issuer; JWKS URL derived as `{issuer}/.well-known/jwks.json` if `OAUTH_JWKS_URL` not set; also validates `iss` claim |
| `OAUTH_AUDIENCE` | Yes | Expected `aud` claim (default: `mcp-web-gateway`) |
| `OAUTH_JWT_SECRET` | Dev only | HS256 static secret — **never in production** |
| `MCP_INTERNAL_URL` | Yes | Internal MCP server base URL |
| `MCP_INTERNAL_API_KEY` | Yes | X-API-Key for the internal MCP server |
| `MCP_RESOURCE_URL` | Yes | Public base URL of this gateway |
| `MCP_AUTHORIZATION_SERVERS` | Yes | Comma-separated auth server base URLs for metadata |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` (default: `INFO`) |
| `MAX_SESSIONS` | No | Concurrent session cap (default: 50) |
| `SESSION_TIMEOUT_S` | No | SSE session lifetime in seconds (default: 300) |
| `PORT` | **Railway injects** | Do not set manually |

---

## Exposed tools (read-only)

| Tool | Required scope |
|---|---|
| `get_account_connection_status` | `mcp:account:read` |
| `get_stream_health` | `mcp:account:read` |
| `get_account_balances` | `mcp:account:read` |
| `get_account_positions` | `mcp:account:read` |
| `get_recent_fills` | `mcp:account:read` |
| `get_open_orders` | `mcp:account:read` |
| `check_live_trade_policy` | `mcp:account:read` |
| `get_incidents` | `mcp:tools:read` |
| `get_symbol_snapshot` | `mcp:tools:read` |
| `list_strategies` | `mcp:strategy:read` |
| `get_strategy_details` | `mcp:strategy:read` |
| `get_recent_executions` | `mcp:strategy:read` |

### Blocked tools (never forwarded)

- `request_paper_trade`
- `update_strategy_state`
- `simulate_strategy_on_snapshot`
- `simulate_strategy_on_range`

---

## Running locally

```powershell
# 1. Copy and fill .env
cp .env.example .env
# Edit .env — add at minimum:
#   OAUTH_JWT_SECRET=local-dev-secret
#   OAUTH_AUDIENCE=mcp-web-gateway
#   MCP_INTERNAL_URL=http://localhost:8006
#   MCP_INTERNAL_API_KEY=<your local MCP_API_KEY>
#   MCP_RESOURCE_URL=http://localhost:8007
#   ENVIRONMENT=dev

# 2. Install gateway deps (from repo root)
pip install -r services/mcp_web_gateway/requirements.txt

# 3. Start the gateway
$env:PYTHONPATH="."
uvicorn services.mcp_web_gateway.main:app --port 8007 --reload

# 4. Generate a dev token
python scripts/gen_dev_token.py

# 5. Run E2E validation (in a new terminal)
.\scripts\test_gateway_e2e.ps1
# or skip the proxy round-trip test if mcp-server isn't running locally:
.\scripts\test_gateway_e2e.ps1 -SkipProxyTest
```

Or via docker-compose (starts the full stack):

```powershell
docker compose -f infra/docker-compose.yml up mcp-web-gateway
```

---

## Local E2E test reference

```powershell
# Health check (no auth needed)
Invoke-RestMethod http://localhost:8007/health

# Protected-resource metadata (no auth needed)
Invoke-RestMethod http://localhost:8007/.well-known/oauth-protected-resource

# Unauthenticated → expect 401
try { Invoke-WebRequest http://localhost:8007/sse } catch { $_.Exception.Response.StatusCode }

# Generate a dev token
$env:OAUTH_JWT_SECRET = "local-dev-secret"
python scripts/gen_dev_token.py
# Copy the token, then:
$TOKEN = "paste-token-here"

# Open SSE stream (first line should be: event: endpoint)
$req = [System.Net.HttpWebRequest]::Create("http://localhost:8007/sse")
$req.Headers.Add("Authorization", "Bearer $TOKEN")
$req.Accept = "text/event-stream"
$resp = $req.GetResponse()
$reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
$reader.ReadLine()   # → "event: endpoint"
$reader.ReadLine()   # → "data: /messages?session_id=<id>"
```

---

## Railway deployment steps

Do these steps **after** setting up Auth0 (or your chosen auth provider).

### 1. Create the service

1. Open your Railway project dashboard.
2. Click **New Service** → **GitHub Repo** → select this repo.
3. Set **Root Directory** to the repo root (not a subdirectory).
4. Set **Dockerfile Path** to `services/mcp_web_gateway/Dockerfile`.
5. Rename the service to `mcp-web-gateway`.

### 2. Set variables

In the `mcp-web-gateway` service → Variables tab, add:

```
OAUTH_JWKS_URL        = https://YOUR_TENANT.auth0.com/.well-known/jwks.json
OAUTH_ISSUER_URL      = https://YOUR_TENANT.auth0.com/
OAUTH_AUDIENCE        = mcp-web-gateway
MCP_INTERNAL_URL      = http://mcp-server.railway.internal:8006
MCP_INTERNAL_API_KEY  = <copy from mcp-server service Variables: MCP_API_KEY>
MCP_RESOURCE_URL      = https://<railway-assigned-domain-for-this-service>
MCP_AUTHORIZATION_SERVERS = https://YOUR_TENANT.auth0.com
LOG_LEVEL             = INFO
ENVIRONMENT           = staging
```

> **Note:** Do NOT set `PORT`. Railway injects it automatically; the Dockerfile
> reads `${PORT:-8007}`.

### 3. Deploy

Click **Deploy** in the Railway UI (or push to the branch Railway watches).

### 4. Verify deployment

```powershell
$BASE = "https://YOUR-GATEWAY.up.railway.app"

# Health check
Invoke-RestMethod "$BASE/health"
# Expected: {"status":"ok","service":"mcp-web-gateway","oauth_mode":"jwks",...}

# Metadata
Invoke-RestMethod "$BASE/.well-known/oauth-protected-resource"
# Expected: {"resource":"https://...","authorization_servers":["https://..."],...}

# Unauthenticated → 401
try { Invoke-WebRequest "$BASE/sse" } catch { $_.Exception.Response.StatusCode }
```

### 5. Test with a real Auth0 M2M token

```powershell
# Get M2M token from Auth0
$auth0 = Invoke-RestMethod -Method POST `
    -Uri "https://YOUR_TENANT.auth0.com/oauth/token" `
    -Body @{
        grant_type    = "client_credentials"
        client_id     = "YOUR_M2M_CLIENT_ID"
        client_secret = "YOUR_M2M_CLIENT_SECRET"
        audience      = "mcp-web-gateway"
        scope         = "mcp:tools:read mcp:account:read mcp:strategy:read"
    }
$TOKEN = $auth0.access_token

# Run E2E script against live Railway URL
.\scripts\test_gateway_e2e.ps1 -GatewayUrl $BASE -JwtSecret "" -SkipProxyTest
# Note: for production, test 4-5 use the real Auth0 token so JwtSecret is not used
```

### 6. Check logs

In Railway → `mcp-web-gateway` service → Deployments → Logs.

Look for:
- `"oauth_mode":"jwks"` in startup health log (confirms JWKS path is active)
- `"event":"mcp_tool_call","outcome":"allowed"` entries for successful tool calls
- Any `ERROR` lines about JWKS fetch failures (would indicate bad `OAUTH_JWKS_URL`)

---

## Connecting Claude web

Once the gateway is deployed and health-checked:

1. Open [claude.ai](https://claude.ai) → Settings → Integrations (or MCP Connectors).
2. Add a new MCP server with URL: `https://YOUR-GATEWAY.up.railway.app/sse`
3. When prompted for OAuth, Claude web will:
   - Fetch `/.well-known/oauth-protected-resource` to discover the auth server
   - Redirect you to Auth0 (or your chosen provider) for login
   - Receive a JWT with the scopes you granted
   - Send `Authorization: Bearer <jwt>` on the SSE connection
4. **Success looks like:** Claude web shows the 12 read-only tools available.
5. **Failure looks like:** "Unable to connect" or a 401 error — check Railway logs for the `WWW-Authenticate` response and verify `OAUTH_JWKS_URL` matches your auth provider.

**Important:** paste **only** the gateway URL (`https://...gateway.../sse`) into Claude web.
Do **not** paste the internal MCP server URL (`http://mcp-server:8006`) anywhere public —
it uses a long-lived API key and is not OAuth-protected.

---

## Running tests

```powershell
# From repo root
python -m pytest tests/unit/test_mcp_web_gateway.py -v
```

All 29 tests must pass before deploying.

---

## Production gaps (known, intentional for Phase 2)

| Gap | Impact | Fix when |
|---|---|---|
| Sessions in process memory | Cannot scale beyond 1 replica | Add Redis Pub/Sub when traffic warrants |
| No per-client rate limiting | A valid token can open many sessions | Add Redis-backed rate limiter before public launch |
| No token revocation check (RFC 7009) | Revoked tokens valid until expiry | Add revocation list if early session kill is needed |
| No refresh token handling | Clients must re-auth when token expires | Auth0 handles PKCE refresh; M2M tokens don't need it |
| CORS origin is `*` | Any origin can call the gateway | Restrict `allow_origins` once Claude web origin is known |
