"""Async SSE proxy: forwards approved tool calls to the internal MCP server.

Each tool call opens a *one-shot* SSE session to the internal server:
  GET /sse  → receive session endpoint
  POST /messages  initialize
  POST /messages  notifications/initialized
  POST /messages  tools/call  (or tools/list)
  read SSE stream for the response
  close

Three HTTP round-trips per call is acceptable for Phase 2 read-only tools.
No persistent connection state is required; error isolation is clean.

The internal server is reached using the private X-API-Key header; callers of
the gateway never see that key.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from .policy import ALLOWED_TOOLS

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "mcp-web-gateway", "version": "1.0"}


class UpstreamError(Exception):
    """Raised when the internal MCP server returns an error or times out."""
    def __init__(self, message: str, code: int = -32000) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class InternalMcpClient:
    """One-shot SSE proxy client for the internal trading-platform MCP server."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_filtered_tools(self) -> list[dict[str, Any]]:
        """Return only the tools from the internal server that are in ALLOWED_TOOLS."""
        result = await self._one_shot_rpc(method="tools/list", params={}, msg_id=2)
        all_tools: list[dict] = result.get("result", {}).get("tools", [])
        return [t for t in all_tools if t.get("name") in ALLOWED_TOOLS]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Forward a single tool call to the internal MCP server."""
        return await self._one_shot_rpc(
            method="tools/call",
            params={"name": name, "arguments": arguments},
            msg_id=2,
        )

    # ── Core SSE session logic ────────────────────────────────────────────────

    async def _one_shot_rpc(
        self,
        method: str,
        params: dict[str, Any],
        msg_id: int,
    ) -> dict[str, Any]:
        """Open an SSE session, initialize it, send one RPC call, return the response."""
        pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        session_ready = asyncio.Event()
        session_path: list[str] = []
        loop = asyncio.get_running_loop()

        async def _read_sse(sse: httpx.Response) -> None:
            """Background task: parse SSE lines and route responses to waiting futures."""
            event_type: str | None = None
            async for line in sse.aiter_lines():
                line = line.rstrip()
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data = line[5:].strip()
                    if not session_path and data.startswith("/"):
                        # First data line is the session endpoint path.
                        session_path.append(data)
                        session_ready.set()
                    elif event_type == "message" and data:
                        try:
                            msg = json.loads(data)
                            mid = msg.get("id")
                            if mid is not None and mid in pending:
                                fut = pending[mid]
                                if not fut.done():
                                    fut.set_result(msg)
                        except (json.JSONDecodeError, Exception) as exc:
                            logger.debug("SSE parse error: %s", exc)
                    event_type = None

        timeout = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)
        sse_headers = {"X-API-Key": self._api_key, "Accept": "text/event-stream"}
        post_headers = {"X-API-Key": self._api_key, "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=timeout) as http:
            async with http.stream("GET", f"{self._base}/sse", headers=sse_headers) as sse:
                reader = asyncio.create_task(_read_sse(sse))
                try:
                    # Wait for the session endpoint event.
                    await asyncio.wait_for(session_ready.wait(), timeout=8.0)
                    msg_url = self._base + session_path[0]

                    # initialize
                    init_fut: asyncio.Future[dict[str, Any]] = loop.create_future()
                    pending[1] = init_fut
                    await http.post(msg_url, json={
                        "jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {
                            "protocolVersion": _PROTOCOL_VERSION,
                            "capabilities": {},
                            "clientInfo": _CLIENT_INFO,
                        },
                    }, headers=post_headers)
                    await asyncio.wait_for(init_fut, timeout=5.0)

                    # notifications/initialized (fire-and-forget; no id)
                    await http.post(msg_url, json={
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    }, headers=post_headers)

                    # actual RPC call
                    rpc_fut: asyncio.Future[dict[str, Any]] = loop.create_future()
                    pending[msg_id] = rpc_fut
                    await http.post(msg_url, json={
                        "jsonrpc": "2.0", "id": msg_id,
                        "method": method, "params": params,
                    }, headers=post_headers)
                    response = await asyncio.wait_for(rpc_fut, timeout=15.0)

                    if "error" in response:
                        err = response["error"]
                        raise UpstreamError(
                            err.get("message", "upstream MCP error"),
                            err.get("code", -32000),
                        )
                    return response

                except asyncio.TimeoutError as exc:
                    raise UpstreamError(
                        "Timeout waiting for internal MCP server response"
                    ) from exc
                except UpstreamError:
                    raise
                except Exception as exc:
                    raise UpstreamError(f"Internal MCP communication error: {exc}") from exc
                finally:
                    reader.cancel()
                    try:
                        await reader
                    except asyncio.CancelledError:
                        pass
