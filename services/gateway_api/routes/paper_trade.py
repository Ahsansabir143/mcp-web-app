from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from services.gateway_api.auth import verify_gateway_api_key
from services.mcp_server.facades.execution import request_paper_trade

router = APIRouter(dependencies=[Depends(verify_gateway_api_key)])


class PaperTradeRequest(BaseModel):
    strategy_id: str
    symbol: str
    side: str
    size_usd: float | None = None
    size: float | None = None
    reason: str | None = None


@router.post("/api/paper-trade")
async def paper_trade(body: PaperTradeRequest, request: Request):
    if body.side not in ("BUY", "SELL"):
        raise HTTPException(422, detail="side must be BUY or SELL")

    result = await request_paper_trade(
        request.app.state.session_factory,
        request.app.state.redis,
        body.strategy_id,
        body.symbol,
        body.side,
        body.size_usd,
        body.size,
        body.reason,
    )

    if result.get("error"):
        status = 400
        if result["error"] in ("strategy_not_found",):
            status = 404
        raise HTTPException(status, detail=result)

    return result
