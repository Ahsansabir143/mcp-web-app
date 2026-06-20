from __future__ import annotations

from fastapi import APIRouter

from services.mcp_server.session import registry

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "mcp-server"}


@router.get("/health/detail")
async def health_detail():
    return {
        "status": "ok",
        "service": "mcp-server",
        "active_sessions": len(registry),
    }
