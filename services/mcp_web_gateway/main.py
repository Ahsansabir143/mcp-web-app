"""MCP Web Gateway — OAuth 2.1 protected SSE endpoint for remote MCP clients.

What this service does
─────────────────────
1.  Exposes GET /sse (requires Bearer JWT) — opens an MCP SSE session for the
    authenticated client.
2.  Exposes POST /messages?session_id=... — receives JSON-RPC 2.0 messages from
    the client and dispatches them:
      - initialize / notifications/*  handled internally
      - tools/list                     fetches from internal MCP, returns filtered list
      - tools/call                     policy-checked, then proxied to internal MCP
3.  Exposes GET /.well-known/oauth-protected-resource — RFC 8707 metadata so
    MCP clients can auto-discover the authorization server.
4.  Writes a structured audit log line for every tool/call attempt.

What this service does NOT do
──────────────────────────────
- It does not issue OAuth tokens (resource server only, not authorization server).
- It does not expose write/control/simulation tools (blocked by policy.py).
- It does not touch the internal MCP server's X-API-Key or config.
- It does not enable live trading.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .audit import (
    OUTCOME_ALLOWED,
    OUTCOME_DENIED,
    OUTCOME_UPSTREAM_ERROR,
    audit_context,
)
from .auth import TokenClaims, require_token
from .config import settings
from .health import router as health_router
from .policy import ALL_SCOPES, ALLOWED_TOOLS, TOOL_SCOPE_MAP, PolicyDenied, check_tool_call
from .proxy import InternalMcpClient, UpstreamError
from .session import GatewaySession, create_session, get_session, remove_session, session_count

logger = logging.getLogger(__name__)

app = FastAPI(
    title="MCP Web Gateway",
    version="1.0.0",
    docs_url=None,   # no Swagger UI in this service
    redoc_url=None,
)
app.include_router(health_router)

# CORS — required for browser-based MCP clients (Claude web connects from claude.ai origin).
# Credentials are NOT used (auth is via Authorization header, not cookies) so
# allow_origins=["*"] is safe here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID", "WWW-Authenticate"],
)


# ── Protected-resource metadata ───────────────────────────────────────────────


@app.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata() -> JSONResponse:
    """RFC 8707 protected-resource metadata.

    Publicly accessible (no auth required) so MCP clients can discover the
    authorization server before they have a token.
    """
    auth_servers = [
        s.strip()
        for s in settings.mcp_authorization_servers.split(",")
        if s.strip()
    ]
    return JSONResponse({
        "resource": settings.mcp_resource_url,
        "authorization_servers": auth_servers,
        "bearer_methods_supported": ["header"],
        "scopes_supported": sorted(ALL_SCOPES),
        "resource_documentation": (
            f"{settings.mcp_resource_url.rstrip('/')}/docs/gateway"
        ),
    })


# ── SSE session endpoint ──────────────────────────────────────────────────────


@app.get("/sse")
async def sse_endpoint(
    claims: TokenClaims = Depends(require_token),
) -> StreamingResponse:
    """Open an MCP SSE session.

    Requires ``Authorization: Bearer <jwt>``.  On success, the first SSE event
    is ``event: endpoint`` with the /messages URL for this session.
    """
    if session_count() >= settings.max_sessions:
        raise HTTPException(
            status_code=503,
            detail="Gateway at session capacity — try again later",
        )

    session = create_session(claims)

    async def event_stream() -> AsyncGenerator[str, None]:
        yield f"event: endpoint\ndata: /messages?session_id={session.session_id}\n\n"
        deadline = time.monotonic() + settings.session_timeout_s
        try:
            while time.monotonic() < deadline:
                try:
                    msg = await asyncio.wait_for(session.queue.get(), timeout=15.0)
                    payload = json.dumps(msg, default=str)
                    yield f"event: message\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            remove_session(session.session_id)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Message handler ───────────────────────────────────────────────────────────


@app.post("/messages")
async def messages_endpoint(
    request: Request,
    session_id: str = Query(...),
) -> JSONResponse:
    """Receive a JSON-RPC 2.0 message from an MCP client.

    The gateway dispatches internally for protocol methods (initialize,
    tools/list) and proxies tool calls to the internal MCP server after
    policy and scope checks.

    Auth on this endpoint is the session itself — the Bearer token was already
    validated when the SSE session was created.
    """
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    method: str = body.get("method", "")
    msg_id: Any = body.get("id")

    if method == "initialize":
        await _handle_initialize(session, msg_id)
    elif method == "notifications/initialized":
        session.mark_initialized()
    elif method == "tools/list":
        await _handle_tools_list(session, msg_id)
    elif method == "tools/call":
        await _handle_tools_call(session, body, msg_id)
    elif method.startswith("notifications/"):
        pass  # client notifications — no response required
    elif method == "ping":
        await session.queue.put({"jsonrpc": "2.0", "id": msg_id, "result": {}})
    else:
        await session.queue.put(
            _error_response(msg_id, -32601, f"Method not found: {method}")
        )

    req_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    return JSONResponse({}, status_code=202, headers={"X-Request-ID": req_id})


# ── Method handlers ───────────────────────────────────────────────────────────


async def _handle_initialize(session: GatewaySession, msg_id: Any) -> None:
    await session.queue.put({
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mcp-web-gateway", "version": "1.0.0"},
        },
    })


async def _handle_tools_list(session: GatewaySession, msg_id: Any) -> None:
    proxy = InternalMcpClient(settings.mcp_internal_url, settings.mcp_internal_api_key)
    try:
        tools = await proxy.get_filtered_tools()
    except UpstreamError as exc:
        logger.warning("tools/list upstream error: %s", exc)
        await session.queue.put(_error_response(msg_id, -32000, str(exc)))
        return
    await session.queue.put({
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": {"tools": tools},
    })


async def _handle_tools_call(
    session: GatewaySession, body: dict[str, Any], msg_id: Any
) -> None:
    params: dict = body.get("params", {})
    tool_name: str = params.get("name", "")
    arguments: dict = params.get("arguments", {})
    claims = session.claims
    scope_for_tool = TOOL_SCOPE_MAP.get(tool_name, "")

    with audit_context(claims.sub, claims.client_id, tool_name, scope_for_tool) as ctx:
        # ── Policy gate ───────────────────────────────────────────────────
        try:
            check_tool_call(tool_name, claims.granted_scopes)
        except PolicyDenied as exc:
            ctx["outcome"] = OUTCOME_DENIED
            ctx["detail"] = exc.reason
            await session.queue.put(
                _error_response(msg_id, -32603, f"Policy denied: {exc.reason}")
            )
            return

        # ── Proxy to internal MCP ─────────────────────────────────────────
        proxy = InternalMcpClient(settings.mcp_internal_url, settings.mcp_internal_api_key)
        try:
            upstream = await proxy.call_tool(tool_name, arguments)
            ctx["outcome"] = OUTCOME_ALLOWED
            await session.queue.put({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": upstream.get("result", {}),
            })
        except UpstreamError as exc:
            ctx["outcome"] = OUTCOME_UPSTREAM_ERROR
            ctx["detail"] = str(exc)
            await session.queue.put(_error_response(msg_id, exc.code, str(exc)))


# ── Helpers ───────────────────────────────────────────────────────────────────


def _error_response(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
