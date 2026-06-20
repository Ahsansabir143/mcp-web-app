from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from services.gateway_api.auth import verify_gateway_api_key
from services.mcp_server.facades.strategy import (
    get_strategy_details,
    list_strategies,
    simulate_strategy_on_snapshot,
    update_strategy_state,
)

router = APIRouter(dependencies=[Depends(verify_gateway_api_key)])


@router.get("/api/strategies")
async def list_strategies_route(
    request: Request,
    symbol: str | None = Query(default=None),
    state: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
):
    return await list_strategies(
        request.app.state.session_factory, symbol, state, limit
    )


@router.get("/api/strategies/{strategy_id}")
async def get_strategy(strategy_id: str, request: Request):
    result = await get_strategy_details(
        request.app.state.session_factory, strategy_id
    )
    if result is None:
        raise HTTPException(404, detail=f"Strategy '{strategy_id}' not found")
    return result


class SimulateRequest(BaseModel):
    symbol: str
    market_type: str = "futures"


@router.post("/api/strategies/{strategy_id}/simulate")
async def simulate_strategy(
    strategy_id: str, body: SimulateRequest, request: Request
):
    return await simulate_strategy_on_snapshot(
        request.app.state.session_factory,
        request.app.state.redis,
        strategy_id,
        body.symbol,
        body.market_type,
    )


class StateRequest(BaseModel):
    target_state: str
    justification: str
    approval_level: str | None = None


@router.post("/api/strategies/{strategy_id}/state")
async def update_state(
    strategy_id: str, body: StateRequest, request: Request
):
    return await update_strategy_state(
        request.app.state.session_factory,
        strategy_id,
        body.target_state,
        body.justification,
        body.approval_level,
    )
