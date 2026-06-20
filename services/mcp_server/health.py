from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "mcp-server"}


@router.get("/health/detail")
async def health_detail(request: Request):
    session_registry = getattr(request.app.state, "session_registry", None)
    active = len(session_registry) if session_registry is not None else 0
    return {
        "status": "ok",
        "service": "mcp-server",
        "active_sessions": active,
    }
