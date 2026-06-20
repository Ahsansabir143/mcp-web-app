#!/bin/sh
# Paper trade path smoke test
#
# Tests: strategy list → paper trade submission → execution job lookup
#
# Usage:
#   export GATEWAY_URL=https://gateway-api-xxx.up.railway.app
#   export GATEWAY_API_KEY=<your gateway key>
#   export ADMIN_API_KEY=<your admin key>
#   bash scripts/smoke_paper_trade.sh
#
# Optional — pass a specific strategy UUID to skip the auto-lookup:
#   export STRATEGY_ID=<uuid>
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
        echo "[PASS] $LABEL"
        PASS=$((PASS+1))
    else
        echo "[FAIL] $LABEL — expected $EXPECTED, got $ACTUAL"
        FAIL=$((FAIL+1))
    fi
}

echo "=== Paper trade smoke test: $GATEWAY_URL ==="
echo ""

# ── Step 1: Get strategy list ─────────────────────────────────────────────────
echo "[1] Fetching strategy list..."
STRATS_BODY=$(curl -s -H "X-API-Key: $GATEWAY_API_KEY" "$GATEWAY_URL/api/strategies")
STRATS_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "X-API-Key: $GATEWAY_API_KEY" "$GATEWAY_URL/api/strategies")
check "GET /api/strategies → 200" "200" "$STRATS_STATUS"
echo "    response: $STRATS_BODY"
echo ""

# ── Step 2: Resolve STRATEGY_ID ───────────────────────────────────────────────
if [ -z "$STRATEGY_ID" ]; then
    STRATEGY_ID=$(echo "$STRATS_BODY" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
fi

if [ -z "$STRATEGY_ID" ]; then
    echo "[SKIP] No strategies found. Create a strategy in the database first."
    echo "       You can use POST /api/strategies (not exposed) or seed the DB directly."
    echo "       Re-run with: export STRATEGY_ID=<uuid>"
    echo ""
else
    echo "    Using strategy_id: $STRATEGY_ID"
    echo ""

    # ── Step 3: GET single strategy ───────────────────────────────────────────
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "X-API-Key: $GATEWAY_API_KEY" \
        "$GATEWAY_URL/api/strategies/$STRATEGY_ID")
    check "GET /api/strategies/$STRATEGY_ID → 200" "200" "$STATUS"

    # ── Step 4: Validation — bad side → 422 ──────────────────────────────────
    echo ""
    echo "[4] Testing validation (bad 'side' field → expect 422)..."
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
        -H "X-API-Key: $GATEWAY_API_KEY" \
        -H "Content-Type: application/json" \
        -d "{\"strategy_id\":\"$STRATEGY_ID\",\"symbol\":\"BTCUSDT\",\"side\":\"INVALID\",\"size_usd\":50}" \
        "$GATEWAY_URL/api/paper-trade")
    check "POST /api/paper-trade (bad side → 422)" "422" "$STATUS"

    # ── Step 5: Paper trade submission ────────────────────────────────────────
    echo ""
    echo "[5] Submitting paper trade (BUY BTCUSDT \$50)..."
    TRADE_BODY=$(curl -s -X POST \
        -H "X-API-Key: $GATEWAY_API_KEY" \
        -H "Content-Type: application/json" \
        -d "{
            \"strategy_id\": \"$STRATEGY_ID\",
            \"symbol\": \"BTCUSDT\",
            \"side\": \"BUY\",
            \"size_usd\": 50.0,
            \"reason\": \"smoke-test\"
        }" \
        "$GATEWAY_URL/api/paper-trade")
    TRADE_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
        -H "X-API-Key: $GATEWAY_API_KEY" \
        -H "Content-Type: application/json" \
        -d "{
            \"strategy_id\": \"$STRATEGY_ID\",
            \"symbol\": \"BTCUSDT\",
            \"side\": \"BUY\",
            \"size_usd\": 50.0,
            \"reason\": \"smoke-test\"
        }" \
        "$GATEWAY_URL/api/paper-trade")
    echo "    response body: $TRADE_BODY"

    if [ "$TRADE_STATUS" = "200" ]; then
        check "POST /api/paper-trade → 200 queued" "200" "$TRADE_STATUS"
    elif [ "$TRADE_STATUS" = "404" ]; then
        echo "[INFO] 404 — strategy may not be in paper_active state."
        echo "       To emit paper trades, strategy.state must be 'paper_active'."
        echo "       Use POST /api/strategies/$STRATEGY_ID/state to transition."
    elif [ "$TRADE_STATUS" = "400" ]; then
        echo "[INFO] 400 — execution facade rejected the intent."
        echo "       Body: $TRADE_BODY"
        echo "       Check execution service logs for details."
    else
        check "POST /api/paper-trade" "200" "$TRADE_STATUS"
    fi
fi

# ── Step 6: Recent executions ──────────────────────────────────────────────────
echo ""
echo "[6] Fetching recent executions..."
EXEC_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "X-API-Key: $GATEWAY_API_KEY" \
    "$GATEWAY_URL/api/executions/recent")
check "GET /api/executions/recent → 200" "200" "$EXEC_STATUS"

# ── Step 7: Ops jobs ───────────────────────────────────────────────────────────
JOBS_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "X-API-Key: $ADMIN_API_KEY" \
    "$GATEWAY_URL/api/ops/execution/jobs")
check "GET /api/ops/execution/jobs → 200" "200" "$JOBS_STATUS"

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] || exit 1
