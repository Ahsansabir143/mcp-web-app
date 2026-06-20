#!/bin/sh
# MCP server smoke test
#
# Tests the SSE + JSON-RPC 2.0 protocol handshake and tools/list call.
# Response JSON for initialize and tools/list arrives via the SSE stream
# (the same curl that opens /sse). Watch the SSE terminal for responses
# while this script sends JSON-RPC messages.
#
# Usage:
#   export MCP_URL=https://mcp-server-xxx.up.railway.app
#   export MCP_API_KEY=<your mcp key>
#   bash scripts/smoke_mcp.sh
#
set -e

: "${MCP_URL:?Set MCP_URL to your Railway mcp-server public URL}"
: "${MCP_API_KEY:?Set MCP_API_KEY}"

echo "=== MCP server smoke test: $MCP_URL ==="
echo ""

# ── Step 1: Open SSE stream and capture the session endpoint ──────────────────
echo "[1/4] Opening SSE connection (3-second window)..."
SSE_RAW=$(curl -sN \
    -H "X-API-Key: $MCP_API_KEY" \
    --max-time 3 \
    "$MCP_URL/sse" 2>/dev/null || true)

SESSION_PATH=$(echo "$SSE_RAW" | grep "^data:" | head -1 | sed 's/^data: //')
SESSION_ID=$(echo "$SESSION_PATH" | grep -o 'session_id=[^&]*' | cut -d= -f2)

if [ -z "$SESSION_ID" ]; then
    echo "[FAIL] No session_id received from SSE stream."
    echo "       SSE response was:"
    echo "$SSE_RAW" | head -10
    exit 1
fi
echo "[PASS] Got session_id: $SESSION_ID"
echo ""

# ── Step 2: Initialize ────────────────────────────────────────────────────────
echo "[2/4] Sending initialize..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    -H "X-API-Key: $MCP_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke-test", "version": "0.0.1"}
        }
    }' \
    "$MCP_URL/messages?session_id=$SESSION_ID")
if [ "$STATUS" = "202" ]; then
    echo "[PASS] initialize → 202 Accepted"
else
    echo "[FAIL] initialize → $STATUS (expected 202)"
    exit 1
fi

# ── Step 3: Send initialized notification ─────────────────────────────────────
echo "[3/4] Sending notifications/initialized..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    -H "X-API-Key: $MCP_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
    "$MCP_URL/messages?session_id=$SESSION_ID")
if [ "$STATUS" = "202" ]; then
    echo "[PASS] notifications/initialized → 202 Accepted"
else
    echo "[FAIL] notifications/initialized → $STATUS (expected 202)"
    exit 1
fi

# ── Step 4: tools/list ────────────────────────────────────────────────────────
echo "[4/4] Calling tools/list..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    -H "X-API-Key: $MCP_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
    "$MCP_URL/messages?session_id=$SESSION_ID")
if [ "$STATUS" = "202" ]; then
    echo "[PASS] tools/list → 202 Accepted"
else
    echo "[FAIL] tools/list → $STATUS (expected 202)"
    exit 1
fi

echo ""
echo "=== MCP protocol handshake complete ==="
echo ""
echo "Note: JSON-RPC responses (initialize result, tools list) are delivered"
echo "      via the SSE stream, not in these HTTP responses."
echo "      To see tool responses, open the SSE stream in a separate terminal:"
echo "      curl -N -H 'X-API-Key: $MCP_API_KEY' '$MCP_URL/sse'"
echo ""
echo "Expected tools in tools/list response (9 total):"
echo "  get_market_snapshot, get_recent_executions, get_strategy_status,"
echo "  get_analytics_snapshot, get_incidents,"
echo "  simulate_strategy, request_paper_trade,"
echo "  update_strategy_state, set_emergency_stop"
