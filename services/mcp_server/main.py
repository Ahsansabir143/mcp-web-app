"""MCP server — Streamable HTTP (SSE) transport.

Claude connects via:
  GET  /sse                      → open SSE session, receive endpoint URL
  POST /messages?session_id=...  → send JSON-RPC requests; responses arrive via SSE

OAuth 2.0 PKCE endpoints (for Claude custom remote connector):
  GET  /.well-known/oauth-authorization-server  → RFC 8414 discovery metadata
  GET  /oauth/authorize                         → PKCE authorize, returns auth code
  POST /oauth/token                             → exchange code+verifier for bearer token

Auth:
  GET /sse and POST /messages both require either:
    Authorization: Bearer <token>   (issued by /oauth/token)
    X-API-Key: <key>                (legacy — backwards-compatible)

Tool call responses follow the MCP 2024-11-05 content format:
  {"content": [{"type": "text", "text": "<json>"}], "isError": false}
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from shared.db.session import async_session_factory
from shared.redis.client import get_redis_client
from shared.utils.logging import get_logger, setup_logging
from services.mcp_server import protocol as proto
from services.mcp_server.auth import verify_mcp_auth
from services.mcp_server.config import settings
from services.mcp_server.health import router as health_router
from services.mcp_server.identity import McpIdentity
from services.mcp_server.login import router as login_router
from services.mcp_server.oauth.discovery import router as discovery_router
from services.mcp_server.oauth.handlers import router as oauth_router
from services.mcp_server.session import RedisSessionRegistry
from services.mcp_server.tools import account as account_tools
from services.mcp_server.tools import control as control_tools
from services.mcp_server.tools import read as read_tools
from services.mcp_server.tools import simulation as sim_tools

setup_logging("mcp-server", settings.log_level)
log = get_logger("mcp-server.main")

# ── Tool dispatch table ────────────────────────────────────────────────────────

_HANDLERS = {
    "get_symbol_snapshot": read_tools.get_symbol_snapshot,
    "list_strategies": read_tools.list_strategies,
    "get_strategy_details": read_tools.get_strategy_details,
    "get_recent_executions": read_tools.get_recent_executions,
    "get_incidents": read_tools.get_incidents,
    "simulate_strategy_on_snapshot": sim_tools.simulate_strategy_on_snapshot,
    "simulate_strategy_on_range": sim_tools.simulate_strategy_on_range,
    "request_paper_trade": control_tools.request_paper_trade,
    "update_strategy_state": control_tools.update_strategy_state,
    # Live account observability (read-only)
    "get_account_connection_status": account_tools.get_account_connection_status,
    "get_account_balances": account_tools.get_account_balances,
    "get_account_positions": account_tools.get_account_positions,
    "get_open_orders": account_tools.get_open_orders,
    "get_recent_fills": account_tools.get_recent_fills,
    "check_live_trade_policy": account_tools.check_live_trade_policy,
}


# ── App lifecycle ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = get_redis_client()
    app.state.session_factory = async_session_factory
    app.state.session_registry = RedisSessionRegistry(
        redis=app.state.redis,
        timeout_s=settings.session_timeout_s,
    )
    log.info("mcp-server started")
    yield
    log.info("mcp-server stopped")


app = FastAPI(title="trading-platform-mcp", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
app.include_router(login_router)
app.include_router(discovery_router)
app.include_router(oauth_router)


# ── SSE endpoint ──────────────────────────────────────────────────────────────


@app.get("/sse")
async def sse_endpoint(
    request: Request,
    identity: McpIdentity = Depends(verify_mcp_auth),
):
    """Open an MCP SSE session.

    Requires auth (Bearer or X-API-Key).  Sends an ``endpoint`` event with the
    URL clients should POST to, then streams ``message`` events for every
    JSON-RPC response.  Session metadata (user_id, TTL) is stored in Redis.
    """
    session_registry: RedisSessionRegistry = request.app.state.session_registry
    session_id, queue = await session_registry.create(
        user_id=identity.user_id,
        client_id=identity.client_id,
    )
    log.info(
        "sse session opened",
        session_id=session_id,
        user_id=identity.user_id,
        auth_method=identity.auth_method,
    )

    async def event_stream():
        yield f"event: endpoint\ndata: /messages?session_id={session_id}\n\n"
        try:
            while True:
                disconnected = await request.is_disconnected()
                if disconnected:
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: message\ndata: {message}\n\n"
                except asyncio.TimeoutError:
                    # Refresh Redis TTL so active sessions don't expire mid-flight
                    await session_registry.refresh_ttl(session_id)
                    yield ": keepalive\n\n"
        finally:
            await session_registry.remove(session_id)
            log.info("sse session closed", session_id=session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── JSON-RPC message handler ──────────────────────────────────────────────────


@app.post("/messages")
async def handle_message(
    request: Request,
    session_id: str,
    identity: McpIdentity = Depends(verify_mcp_auth),
):
    """Receive a JSON-RPC request and enqueue the response for the SSE stream."""
    session_registry: RedisSessionRegistry = request.app.state.session_registry
    queue = await session_registry.get(session_id)
    if queue is None:
        raise HTTPException(404, detail=f"Session '{session_id}' not found or expired")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, detail="Invalid JSON body")

    method = body.get("method", "")
    id_ = body.get("id")
    params = body.get("params") or {}

    response_json = await _dispatch(method, id_, params, request, identity)
    await queue.put(response_json)
    return Response(status_code=202)


async def _dispatch(
    method: str,
    id_,
    params: dict,
    request: Request,
    identity: McpIdentity | None = None,
) -> str:
    redis = request.app.state.redis
    session_factory = request.app.state.session_factory

    if method == "initialize":
        return proto.ok(
            id_, proto.initialize_result(settings.server_name, settings.server_version)
        )

    if method == "notifications/initialized":
        return proto.ok(id_, {})

    if method == "tools/list":
        return proto.ok(id_, proto.tools_list_result())

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}

        if tool_name not in _HANDLERS:
            return proto.ok(
                id_,
                proto.tool_error(
                    f"Unknown tool '{tool_name}'. "
                    f"Available: {list(_HANDLERS.keys())}"
                ),
            )

        try:
            result = await _HANDLERS[tool_name](
                arguments,
                redis=redis,
                session_factory=session_factory,
                user_identity=identity,
            )
            return proto.ok(id_, proto.tool_content(result))
        except Exception as exc:
            log.error("tool execution error", tool=tool_name, exc_info=exc)
            return proto.ok(id_, proto.tool_error(f"Internal error: {exc}"))

    if method == "ping":
        return proto.ok(id_, {})

    return proto.error(id_, -32601, f"Method not found: {method}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
