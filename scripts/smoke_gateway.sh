#!/bin/sh
# Gateway API smoke test
#
# Usage:
#   export GATEWAY_URL=https://gateway-api-xxx.up.railway.app
#   export GATEWAY_API_KEY=<your gateway key>
#   export ADMIN_API_KEY=<your admin key>
#   bash scripts/smoke_gateway.sh
#
set -e

: "${GATEWAY_URL:?Set GATEWAY_URL to your Railway gateway-api public URL}"
: "${GATEWAY_API_KEY:?Set GATEWAY_API_KEY}"
: "${ADMIN_API_KEY:?Set ADMIN_API_KEY}"

PASS=0
FAIL=0

check() {
    LABEL="$1"; EXPECTED="$2"; ACTUAL="$3"
    if [ "$ACTUAL" = "$EXPECTED" ]; then
        echo "[PASS] $LABEL (HTTP $ACTUAL)"
        PASS=$((PASS+1))
    else
        echo "[FAIL] $LABEL — expected HTTP $EXPECTED, got HTTP $ACTUAL"
        FAIL=$((FAIL+1))
    fi
}

echo "=== Gateway smoke test: $GATEWAY_URL ==="
echo ""

# ── Health (no auth) ──────────────────────────────────────────────────────────
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$GATEWAY_URL/api/health")
check "GET /api/health (no auth)" "200" "$STATUS"

STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$GATEWAY_URL/api/health/detail")
check "GET /api/health/detail (no auth)" "200" "$STATUS"

echo ""
BODY=$(curl -s "$GATEWAY_URL/api/health/detail")
echo "    health/detail body: $BODY"
echo ""

# ── Auth enforcement ──────────────────────────────────────────────────────────
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$GATEWAY_URL/api/strategies")
check "GET /api/strategies (no key → 401)" "401" "$STATUS"

STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "X-API-Key: wrong-key" "$GATEWAY_URL/api/strategies")
check "GET /api/strategies (bad key → 401)" "401" "$STATUS"

STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "X-API-Key: $GATEWAY_API_KEY" "$GATEWAY_URL/api/strategies")
check "GET /api/strategies (valid key → 200)" "200" "$STATUS"

# ── Admin auth enforcement ────────────────────────────────────────────────────
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "X-API-Key: $GATEWAY_API_KEY" "$GATEWAY_URL/api/ops/metrics")
check "GET /api/ops/metrics (gateway key on admin route → 401)" "401" "$STATUS"

STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "X-API-Key: $ADMIN_API_KEY" "$GATEWAY_URL/api/ops/metrics")
check "GET /api/ops/metrics (admin key → 200)" "200" "$STATUS"

# ── Trading mode safety check ─────────────────────────────────────────────────
echo ""
TRADING_MODE_BODY=$(curl -s -H "X-API-Key: $ADMIN_API_KEY" "$GATEWAY_URL/api/ops/trading-mode")
echo "    trading-mode: $TRADING_MODE_BODY"
if echo "$TRADING_MODE_BODY" | grep -q "paper"; then
    echo "[PASS] trading mode contains 'paper'"
    PASS=$((PASS+1))
else
    echo "[FAIL] trading mode does NOT contain 'paper' — STOP DEPLOYMENT"
    FAIL=$((FAIL+1))
fi

# ── Ops metrics ───────────────────────────────────────────────────────────────
echo ""
METRICS=$(curl -s -H "X-API-Key: $ADMIN_API_KEY" "$GATEWAY_URL/api/ops/metrics")
echo "    ops/metrics streams:"
echo "$METRICS" | grep -o '"stream:[^"]*":[0-9]*' | head -10 || echo "    (no stream lengths visible — pipeline may need time to start)"

# ── Market snapshot (may 404 if pipeline not warmed up yet) ──────────────────
echo ""
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "X-API-Key: $GATEWAY_API_KEY" \
    "$GATEWAY_URL/api/market/snapshot/spot/BTCUSDT")
if [ "$STATUS" = "200" ]; then
    echo "[PASS] GET /api/market/snapshot/spot/BTCUSDT → 200"
    PASS=$((PASS+1))
elif [ "$STATUS" = "404" ]; then
    echo "[WARN] GET /api/market/snapshot/spot/BTCUSDT → 404 (pipeline may still be warming up — retry in 30s)"
else
    echo "[FAIL] GET /api/market/snapshot/spot/BTCUSDT → $STATUS"
    FAIL=$((FAIL+1))
fi

# ── Ops streams ───────────────────────────────────────────────────────────────
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "X-API-Key: $ADMIN_API_KEY" "$GATEWAY_URL/api/ops/streams")
check "GET /api/ops/streams (admin → 200)" "200" "$STATUS"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] || exit 1
