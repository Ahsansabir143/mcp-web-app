"""Ops: per-strategy operational status.

GET /api/ops/strategy/{id}/status returns the full operational picture for a
strategy: current lifecycle state, last evaluation result, last state
transition action, and whether it is emitting intents.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, select

from services.gateway_api.ops.auth import verify_admin_api_key
from shared.db.models.strategy import (
    Strategy as StrategyModel,
    StrategyAction as StrategyActionModel,
    StrategyEvaluation as StrategyEvaluationModel,
)
from shared.schemas.enums import StrategyState

router = APIRouter(dependencies=[Depends(verify_admin_api_key)])

_EMIT_STATES = frozenset({
    StrategyState.PAPER_ACTIVE.value,
    StrategyState.ASSISTED_LIVE.value,
    StrategyState.BOUNDED_AUTO_LIVE.value,
})


@router.get("/api/ops/strategy/{strategy_id}/status")
async def strategy_status(strategy_id: str, request: Request):
    """Operational status for a strategy.

    Returns lifecycle state, last evaluation signal, last state-change action,
    and whether the strategy is in an intent-emitting state.
    """
    try:
        sid = uuid.UUID(strategy_id)
    except ValueError:
        raise HTTPException(422, detail=f"Invalid strategy UUID: {strategy_id}")

    session_factory = request.app.state.session_factory

    async with session_factory() as session:
        strategy = await session.get(StrategyModel, sid)
        if strategy is None:
            raise HTTPException(404, detail=f"Strategy '{strategy_id}' not found")

        # Last evaluation
        eval_res = await session.execute(
            select(StrategyEvaluationModel)
            .where(StrategyEvaluationModel.strategy_id == sid)
            .order_by(desc(StrategyEvaluationModel.created_at_ms))
            .limit(1)
        )
        last_eval = eval_res.scalar_one_or_none()

        # Last state action
        action_res = await session.execute(
            select(StrategyActionModel)
            .where(StrategyActionModel.strategy_id == sid)
            .order_by(desc(StrategyActionModel.created_at_ms))
            .limit(1)
        )
        last_action = action_res.scalar_one_or_none()

        # Last intent emitted (eval with intent_id set)
        intent_res = await session.execute(
            select(StrategyEvaluationModel)
            .where(
                StrategyEvaluationModel.strategy_id == sid,
                StrategyEvaluationModel.intent_id.is_not(None),
            )
            .order_by(desc(StrategyEvaluationModel.created_at_ms))
            .limit(1)
        )
        last_intent_eval = intent_res.scalar_one_or_none()

    result: dict = {
        "strategy_id": strategy_id,
        "name": strategy.name,
        "state": strategy.state,
        "current_version": strategy.current_version,
        "market_type": strategy.market_type,
        "symbol_filters": strategy.symbol_filters or [],
        "is_emitting_intents": strategy.state in _EMIT_STATES,
        "created_at": strategy.created_at.isoformat() if strategy.created_at else None,
        "updated_at": strategy.updated_at.isoformat() if strategy.updated_at else None,
    }

    if last_eval:
        result["last_evaluation"] = {
            "created_at_ms": last_eval.created_at_ms,
            "symbol": last_eval.symbol,
            "signal": last_eval.signal,
            "direction": last_eval.direction,
            "confidence": last_eval.confidence,
            "intent_emitted": last_eval.intent_id is not None,
        }

    if last_action:
        result["last_action"] = {
            "action_type": last_action.action_type,
            "triggered_by": last_action.triggered_by,
            "details": last_action.details,
            "created_at_ms": last_action.created_at_ms,
        }

    if last_intent_eval:
        result["last_intent_emission"] = {
            "intent_id": str(last_intent_eval.intent_id),
            "symbol": last_intent_eval.symbol,
            "created_at_ms": last_intent_eval.created_at_ms,
        }

    return result
