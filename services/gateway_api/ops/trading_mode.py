"""Ops: global trading mode control.

POST /api/ops/trading-mode — switch between paper_only, mixed, and
emergency_stop. The value is stored in Redis and advisory for running services.

Note: mixed mode is accepted and stored but live trading is not yet
implemented — paper enforcement in the execution service's AccountContext
remains active regardless of this setting.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from services.gateway_api.ops.auth import verify_admin_api_key
from shared.redis.keys import RedisKeys

router = APIRouter(dependencies=[Depends(verify_admin_api_key)])

_VALID_MODES = {"paper_only", "mixed", "emergency_stop"}

_MODE_NOTES = {
    "paper_only": "All execution enforced to paper mode. Live trading is blocked.",
    "mixed": "Accepted but live trading is not yet implemented. Paper mode remains enforced.",
    "emergency_stop": "Emergency stop active. All intent processing should halt.",
}


class TradingModeRequest(BaseModel):
    mode: str
    reason: str = ""


@router.post("/api/ops/trading-mode")
async def set_trading_mode(body: TradingModeRequest, request: Request):
    """Set the global trading mode.

    Writes ``global:trading_mode`` to Redis. Services that respect this key
    should read it before processing intents.

    ``emergency_stop`` additionally sets ``global:emergency_stop`` which is
    checked by the execution consumer before processing each intent.
    ``paper_only`` and ``mixed`` clear the emergency stop key.
    """
    if body.mode not in _VALID_MODES:
        raise HTTPException(
            422, detail=f"mode must be one of {sorted(_VALID_MODES)}"
        )

    redis = request.app.state.redis
    await redis.set(RedisKeys.global_trading_mode(), body.mode)

    if body.mode == "emergency_stop":
        await redis.set(RedisKeys.global_emergency_stop(), "1")
    else:
        await redis.delete(RedisKeys.global_emergency_stop())

    return {
        "mode": body.mode,
        "reason": body.reason,
        "note": _MODE_NOTES[body.mode],
        "set_at": int(time.time()),
    }


@router.get("/api/ops/trading-mode")
async def get_trading_mode(request: Request):
    """Read the current global trading mode from Redis."""
    redis = request.app.state.redis
    mode = await redis.get(RedisKeys.global_trading_mode())
    emergency = await redis.exists(RedisKeys.global_emergency_stop())
    return {
        "mode": mode or "paper_only",
        "emergency_stop_active": bool(emergency),
        "note": _MODE_NOTES.get(mode or "paper_only", ""),
    }
