from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from services.gateway_api.auth import verify_gateway_api_key
from services.mcp_server.facades.market import get_symbol_snapshot

router = APIRouter(dependencies=[Depends(verify_gateway_api_key)])


@router.get("/api/market/snapshot/{market_type}/{symbol}")
async def market_snapshot(market_type: str, symbol: str, request: Request):
    redis = request.app.state.redis
    result = await get_symbol_snapshot(redis, market_type, symbol)
    if result is None:
        raise HTTPException(404, detail=f"No snapshot for {market_type}/{symbol}")
    return result
