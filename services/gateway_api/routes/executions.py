from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from services.gateway_api.auth import verify_gateway_api_key
from services.mcp_server.facades.execution import get_incidents, get_recent_executions

router = APIRouter(dependencies=[Depends(verify_gateway_api_key)])


@router.get("/api/executions/recent")
async def recent_executions(
    request: Request,
    strategy_id: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    return await get_recent_executions(
        request.app.state.session_factory, strategy_id, symbol, limit
    )


@router.get("/api/incidents")
async def incidents(
    request: Request,
    symbol: str | None = Query(default=None),
    since_ts: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    return await get_incidents(
        request.app.state.session_factory, symbol, since_ts, limit
    )
