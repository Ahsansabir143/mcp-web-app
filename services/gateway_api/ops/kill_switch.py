"""Ops: kill switch and symbol/user pause controls.

POST /api/ops/kill-switch — wraps KillSwitchControl from the execution
service to allow operators to block trading for an account or symbol without
restarting services.

All mutations are stored in Redis with configurable TTLs. The execution
consumer checks these keys before processing any intent.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from services.gateway_api.ops.auth import verify_admin_api_key
from services.execution.controls.kill_switch import KillSwitchControl
from services.execution.controls.cooldown import CooldownControl

router = APIRouter(dependencies=[Depends(verify_admin_api_key)])

_VALID_ACTIONS = {
    "activate",
    "clear",
    "pause_user",
    "resume_user",
    "pause_symbol",
    "resume_symbol",
    "trip_circuit_breaker",
    "reset_circuit_breaker",
}


class KillSwitchRequest(BaseModel):
    action: str
    account_id: str
    symbol: str | None = None
    ttl_s: int = 86400


@router.post("/api/ops/kill-switch")
async def kill_switch(body: KillSwitchRequest, request: Request):
    """Activate/clear kill switches and symbol pauses for an account.

    Actions:
    - ``activate`` / ``clear``: account-level kill switch (blocks all intents)
    - ``pause_user`` / ``resume_user``: user-level pause (softer than kill switch)
    - ``pause_symbol`` / ``resume_symbol``: symbol-level pause (requires ``symbol``)
    - ``trip_circuit_breaker`` / ``reset_circuit_breaker``: circuit breaker

    All activations use ``ttl_s`` (default 86400 = 24h). Redis TTL means
    controls expire automatically even if not explicitly cleared.
    """
    if body.action not in _VALID_ACTIONS:
        raise HTTPException(
            422,
            detail=f"action must be one of {sorted(_VALID_ACTIONS)}",
        )

    # symbol required for symbol-scoped actions
    if body.action in ("pause_symbol", "resume_symbol") and not body.symbol:
        raise HTTPException(422, detail=f"'symbol' is required for action '{body.action}'")

    redis = request.app.state.redis
    ks = KillSwitchControl(redis)

    match body.action:
        case "activate":
            await ks.activate(body.account_id, body.ttl_s)
        case "clear":
            await ks.clear(body.account_id)
        case "pause_user":
            await ks.pause_user(body.account_id, body.ttl_s)
        case "resume_user":
            await ks.resume_user(body.account_id)
        case "pause_symbol":
            await ks.pause_symbol(body.account_id, body.symbol, body.ttl_s)
        case "resume_symbol":
            await ks.resume_symbol(body.account_id, body.symbol)
        case "trip_circuit_breaker":
            await ks.trip_circuit_breaker(body.account_id, body.ttl_s)
        case "reset_circuit_breaker":
            await ks.reset_circuit_breaker(body.account_id)

    return {
        "action": body.action,
        "account_id": body.account_id,
        "symbol": body.symbol,
        "ttl_s": body.ttl_s if body.action in ("activate", "pause_user", "pause_symbol", "trip_circuit_breaker") else None,
    }


@router.get("/api/ops/kill-switch/{account_id}")
async def kill_switch_status(account_id: str, request: Request):
    """Read current kill switch and pause states for an account."""
    redis = request.app.state.redis
    ks = KillSwitchControl(redis)

    return {
        "account_id": account_id,
        "kill_switch_active": await ks.is_kill_switch_active(account_id),
        "user_paused": await ks.is_user_paused(account_id),
        "circuit_breaker_active": await ks.is_circuit_breaker_active(account_id),
    }
