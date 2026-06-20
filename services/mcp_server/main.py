"""MCP server — Streamable HTTP (SSE) transport.

Claude connects via:
  GET  /sse                      → open SSE session, receive endpoint URL
  POST /messages?session_id=...  → send JSON-RPC requests; responses arrive via SSE

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
from services.mcp_server.auth import verify_mcp_api_key
from services.mcp_server.config import settings
from services.mcp_server.health import router as health_router
from services.mcp_server.session import registry
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
}


# ── App lifecycle ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = get_redis_client()
    app.state.session_factory = async_session_factory
    log.info("mcp-server started")
    yield
    log.info("mcp-server stopped")


app = FastAPI(title="trading-platform-mcp", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)


# ── SSE endpoint ──────────────────────────────────────────────────────────────


@app.get("/sse")
async def sse_endpoint(request: Request):
    """Open an MCP SSE session.

    Sends an ``endpoint`` event with the URL clients should POST to, then
    streams ``message`` events for every JSON-RPC response.
    """
    session_id, queue = registry.create()
    log.info("sse session opened", session_id=session_id)

    async def event_stream():
        # Bootstrap: tell the client which URL to POST to
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
                    yield ": keepalive\n\n"
        finally:
            registry.remove(session_id)
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
    _: None = Depends(verify_mcp_api_key),
):
    """Receive a JSON-RPC request and enqueue the response for the SSE stream."""
    queue = registry.get(session_id)
    if queue is None:
        raise HTTPException(404, detail=f"Session '{session_id}' not found or expired")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, detail="Invalid JSON body")

    method = body.get("method", "")
    id_ = body.get("id")
    params = body.get("params") or {}

    response_json = await _dispatch(method, id_, params, request)
    await queue.put(response_json)
    return Response(status_code=202)


async def _dispatch(method: str, id_, params: dict, request: Request) -> str:
    redis = request.app.state.redis
    session_factory = request.app.state.session_factory

    if method == "initialize":
        return proto.ok(
            id_, proto.initialize_result(settings.server_name, settings.server_version)
        )

    if method == "notifications/initialized":
        # Client acknowledgement — no response needed (but we must return something)
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
