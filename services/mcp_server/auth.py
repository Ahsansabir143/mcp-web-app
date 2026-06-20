"""MCP auth: accepts Bearer token (OAuth 2.0) or X-API-Key (legacy).

Priority:
  1. ``Authorization: Bearer <token>`` → validated against Redis, returns McpIdentity with user_id
  2. ``X-API-Key: <key>``              → validated against settings.mcp_api_key, returns api-key identity
  3. Neither / invalid                 → HTTP 401
"""
from __future__ import annotations

from fastapi import Header, HTTPException, Request

from services.mcp_server.config import settings
from services.mcp_server.identity import McpIdentity
from services.mcp_server.oauth.store import lookup_bearer_token


async def verify_mcp_auth(
    request: Request,
    authorization: str = Header(default=""),
    x_api_key: str = Header(default=""),
) -> McpIdentity:
    """FastAPI dependency — returns caller identity or raises HTTP 401."""
    if authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        redis = request.app.state.redis
        payload = await lookup_bearer_token(redis, token)
        if payload is None:
            raise HTTPException(status_code=401, detail="Bearer token invalid or expired")
        return McpIdentity(
            user_id=payload["user_id"],
            client_id=payload["client_id"],
            auth_method="oauth",
            scope=payload.get("scope", ""),
        )

    if x_api_key and x_api_key == settings.mcp_api_key:
        return McpIdentity(
            user_id="api-key-user",
            client_id="mcp-api-key",
            auth_method="api_key",
        )

    raise HTTPException(status_code=401, detail="Invalid or missing credentials")


# Kept so any external caller that imported the old name still works.
verify_mcp_api_key = verify_mcp_auth
