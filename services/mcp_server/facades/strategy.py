"""Strategy façade — DB reads/writes + simulation engine."""
from __future__ import annotations

import time
import uuid
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.db.models.strategy import (
    Strategy as StrategyModel,
    StrategyAction as StrategyActionModel,
    StrategyEvaluation as StrategyEvaluationModel,
    StrategyVersion as StrategyVersionModel,
)
from shared.redis.client import RedisClient
from shared.redis.keys import RedisKeys
from shared.schemas.analytics import UnifiedDecisionSnapshot
from shared.schemas.enums import ApprovalLevel, StrategyState
from shared.utils.logging import get_logger

log = get_logger("mcp_server.facades.strategy")


# ── List + detail ─────────────────────────────────────────────────────────────


async def list_strategies(
    session_factory: async_sessionmaker[AsyncSession],
    symbol_filter: str | None = None,
    state_filter: str | None = None,
    limit: int = 50,
) -> list[dict]:
    async with session_factory() as session:
        stmt = select(StrategyModel)
        if state_filter:
            stmt = stmt.where(StrategyModel.state == state_filter)
        stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        strategies = result.scalars().all()

    rows = []
    for s in strategies:
        # Apply symbol filter in Python (JSON array column)
        if symbol_filter and symbol_filter not in (s.symbol_filters or []):
            continue
        rows.append(_strategy_summary(s))
    return rows


async def get_strategy_details(
    session_factory: async_sessionmaker[AsyncSession],
    strategy_id: str,
) -> dict | None:
    try:
        sid = uuid.UUID(strategy_id)
    except ValueError:
        return None

    async with session_factory() as session:
        strategy = await session.get(StrategyModel, sid)
        if strategy is None:
            return None

        # Current version
        ver_result = await session.execute(
            select(StrategyVersionModel).where(
                StrategyVersionModel.strategy_id == sid,
                StrategyVersionModel.version == strategy.current_version,
            )
        )
        version = ver_result.scalar_one_or_none()

        # Last evaluation
        eval_result = await session.execute(
            select(StrategyEvaluationModel)
            .where(StrategyEvaluationModel.strategy_id == sid)
            .order_by(StrategyEvaluationModel.created_at_ms.desc())
            .limit(1)
        )
        last_eval = eval_result.scalar_one_or_none()

    detail: dict = {
        **_strategy_summary(strategy),
        "description": strategy.description,
        "current_version": strategy.current_version,
    }
    if version:
        detail["version_detail"] = {
            "version": version.version,
            "rules": version.rules,
            "parameters": version.parameters,
            "approval_required": version.approval_required,
            "change_note": version.change_note,
            "created_by": version.created_by,
        }
    if last_eval:
        detail["last_evaluation"] = {
            "snapshot_timestamp_ms": last_eval.snapshot_timestamp_ms,
            "signal": last_eval.signal,
            "direction": last_eval.direction,
            "confidence": last_eval.confidence,
            "created_at_ms": last_eval.created_at_ms,
        }
    return detail


# ── Lifecycle update ──────────────────────────────────────────────────────────


async def update_strategy_state(
    session_factory: async_sessionmaker[AsyncSession],
    strategy_id: str,
    target_state: str,
    justification: str,
    user_approval_level: str | None = None,
) -> dict:
    try:
        sid = uuid.UUID(strategy_id)
    except ValueError:
        return {"success": False, "error": f"Invalid strategy_id: {strategy_id}"}

    try:
        target = StrategyState(target_state)
    except ValueError:
        valid = [s.value for s in StrategyState]
        return {"success": False, "error": f"Unknown state '{target_state}'. Valid: {valid}"}

    from services.strategy_service.lifecycle.transitions import LifecycleManager, LifecycleError

    lifecycle = LifecycleManager()

    approval: ApprovalLevel | None = None
    if user_approval_level:
        try:
            approval = ApprovalLevel(user_approval_level)
        except ValueError:
            pass

    async with session_factory() as session:
        strategy = await session.get(StrategyModel, sid)
        if strategy is None:
            return {"success": False, "error": f"Strategy {strategy_id} not found"}

        previous_state = strategy.state
        try:
            from_state = StrategyState(previous_state)
        except ValueError:
            return {"success": False, "error": f"Strategy has unknown state '{previous_state}'"}

        try:
            lifecycle.transition(from_state, target, approval)
        except LifecycleError as exc:
            return {"success": False, "error": str(exc)}

        strategy.state = target.value
        action = StrategyActionModel(
            strategy_id=sid,
            version=strategy.current_version,
            action_type="state_change",
            triggered_by="mcp_tool",
            details={
                "previous_state": previous_state,
                "new_state": target.value,
                "justification": justification,
            },
            created_at_ms=int(time.time() * 1000),
        )
        session.add(action)
        await session.commit()

    return {
        "success": True,
        "strategy_id": strategy_id,
        "previous_state": previous_state,
        "new_state": target.value,
        "justification": justification,
    }


# ── Simulation ────────────────────────────────────────────────────────────────


async def simulate_strategy_on_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    redis: RedisClient,
    strategy_id: str,
    symbol: str,
    market_type: str = "futures",
) -> dict:
    """Evaluate a strategy against the latest analytics snapshot (dry run).

    Evaluation is run via the same StrategyEvaluator used by the strategy
    consumer. No TradeIntent is published. The hypothetical intent (if the
    signal fires) is returned as data only.
    """
    try:
        sid = uuid.UUID(strategy_id)
    except ValueError:
        return {"error": "invalid_strategy_id", "message": f"Invalid UUID: {strategy_id}"}

    async with session_factory() as session:
        strategy = await session.get(StrategyModel, sid)
        if strategy is None:
            return {"error": "not_found", "message": f"Strategy {strategy_id} not found"}

        ver_result = await session.execute(
            select(StrategyVersionModel).where(
                and_(
                    StrategyVersionModel.strategy_id == sid,
                    StrategyVersionModel.version == strategy.current_version,
                )
            )
        )
        version = ver_result.scalar_one_or_none()

    if version is None:
        return {
            "error": "version_not_found",
            "message": f"No version record for strategy {strategy_id} v{strategy.current_version}",
        }

    raw = await redis.get(RedisKeys.analytics_snapshot(market_type, symbol))
    if not raw:
        return {
            "error": "no_snapshot",
            "message": (
                f"No analytics snapshot for {symbol}/{market_type}. "
                "Ensure analytics service is running and has processed data for this symbol."
            ),
        }

    try:
        snapshot = UnifiedDecisionSnapshot.model_validate_json(raw)
    except Exception as exc:
        return {"error": "snapshot_parse_error", "message": str(exc)}

    try:
        from services.strategy_service.framework.evaluator import StrategyEvaluator
        from services.strategy_service.framework.factory import build_strategy
        from services.strategy_service.lifecycle.transitions import LifecycleManager
    except ImportError as exc:
        return {"error": "import_error", "message": str(exc)}

    strategy_obj = build_strategy(
        strategy_id=sid,
        version=version.version,
        rules=version.rules or [],
        parameters=version.parameters or {},
    )
    try:
        state = StrategyState(strategy.state)
    except ValueError:
        state = StrategyState.SIMULATION

    evaluator = StrategyEvaluator(
        strategy_id=sid,
        version=version.version,
        state=state,
        strategy=strategy_obj,
        lifecycle=LifecycleManager(),
        symbol_filters=strategy.symbol_filters or [],
    )

    eval_record = evaluator.evaluate(snapshot)

    result: dict = {
        "strategy_id": strategy_id,
        "symbol": symbol,
        "market_type": market_type,
        "strategy_state": strategy.state,
        "snapshot_timestamp_ms": eval_record.snapshot_timestamp_ms,
        "signal": eval_record.signal,
        "direction": eval_record.direction,
        "confidence": eval_record.confidence,
        "explanation": eval_record.explanation,
        "simulation_note": "Dry-run only. No intent was published to the execution stream.",
    }

    if eval_record.trade_intent is not None:
        intent = eval_record.trade_intent
        result["hypothetical_intent"] = {
            "symbol": intent.symbol,
            "side": intent.side.value,
            "order_type": intent.order_type.value,
            "size": str(intent.size),
            "size_usd": str(intent.size_usd) if intent.size_usd else None,
            "intent_type": intent.intent_type,
        }

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────


def _strategy_summary(s: StrategyModel) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "state": s.state,
        "market_type": s.market_type,
        "symbol_filters": s.symbol_filters or [],
        "current_version": s.current_version,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }
