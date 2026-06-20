"""API key auth for the MCP server."""
from __future__ import annotations

from fastapi import Header, HTTPException

from services.mcp_server.config import settings


async def verify_mcp_api_key(x_api_key: str = Header(default="")) -> None:
    if x_api_key != settings.mcp_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
