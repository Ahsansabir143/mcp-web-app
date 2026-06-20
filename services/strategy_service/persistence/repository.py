from __future__ import annotations

import time
import uuid

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.db.models.strategy import (
    StrategyAction as StrategyActionModel,
    StrategyEvaluation as StrategyEvaluationModel,
    StrategyRollback as StrategyRollbackModel,
    StrategyRun as StrategyRunModel,
    StrategyVersion as StrategyVersionModel,
    Strategy as StrategyModel,
)
from shared.schemas.enums import StrategyState
from shared.schemas.strategy import StrategyEvaluation as StrategyEvaluationSchema

_ACTIVE_STATES = [
    StrategyState.SIMULATION.value,
    StrategyState.PAPER_ACTIVE.value,
    StrategyState.ASSISTED_LIVE.value,
    StrategyState.BOUNDED_AUTO_LIVE.value,
]


class StrategyRepository:
    """All DB operations for the strategy service.

    Each method opens its own short-lived session via the factory to stay
    compatible with the asyncio consumer loop (no shared session state).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def list_active_strategies(
        self,
    ) -> list[tuple[StrategyModel, StrategyVersionModel]]:
        """Return all active strategies joined with their current version."""
        async with self._factory() as session:
            stmt = (
                select(StrategyModel, StrategyVersionModel)
                .join(
                    StrategyVersionModel,
                    and_(
                        StrategyVersionModel.strategy_id == StrategyModel.id,
                        StrategyVersionModel.version == StrategyModel.current_version,
                    ),
                )
                .where(StrategyModel.state.in_(_ACTIVE_STATES))
            )
            result = await session.execute(stmt)
            return [(row[0], row[1]) for row in result.all()]

    async def get_strategy(self, strategy_id: uuid.UUID) -> StrategyModel | None:
        async with self._factory() as session:
            return await session.get(StrategyModel, strategy_id)

    async def get_version(
        self,
        strategy_id: uuid.UUID,
        version: int,
    ) -> StrategyVersionModel | None:
        async with self._factory() as session:
            stmt = select(StrategyVersionModel).where(
                StrategyVersionModel.strategy_id == strategy_id,
                StrategyVersionModel.version == version,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def save_evaluation(self, eval_schema: StrategyEvaluationSchema) -> None:
        async with self._factory() as session:
            record = StrategyEvaluationModel(
                id=eval_schema.id,
                run_id=eval_schema.run_id,
                strategy_id=eval_schema.strategy_id,
                version=eval_schema.version,
                symbol=eval_schema.symbol,
                market_type=eval_schema.market_type.value,
                snapshot_timestamp_ms=eval_schema.snapshot_timestamp_ms,
                signal=eval_schema.signal,
                direction=eval_schema.direction,
                confidence=(
                    str(round(eval_schema.confidence, 6))
                    if eval_schema.confidence is not None
                    else None
                ),
                explanation=eval_schema.explanation,
                intent_id=(
                    eval_schema.trade_intent.intent_id
                    if eval_schema.trade_intent
                    else None
                ),
                created_at_ms=int(time.time() * 1000),
            )
            session.add(record)
            await session.commit()

    async def save_action(
        self,
        strategy_id: uuid.UUID,
        version: int,
        action_type: str,
        triggered_by: str,
        details: dict,
    ) -> None:
        async with self._factory() as session:
            action = StrategyActionModel(
                strategy_id=strategy_id,
                version=version,
                action_type=action_type,
                triggered_by=triggered_by,
                details=details,
                created_at_ms=int(time.time() * 1000),
            )
            session.add(action)
            await session.commit()

    async def update_strategy_state(
        self,
        strategy_id: uuid.UUID,
        new_state: StrategyState,
    ) -> None:
        async with self._factory() as session:
            strategy = await session.get(StrategyModel, strategy_id)
            if strategy:
                strategy.state = new_state.value
                await session.commit()

    async def save_rollback(
        self,
        strategy_id: uuid.UUID,
        from_version: int,
        to_version: int,
        reason: str,
        rolled_back_by: str,
    ) -> None:
        async with self._factory() as session:
            rollback = StrategyRollbackModel(
                strategy_id=strategy_id,
                from_version=from_version,
                to_version=to_version,
                reason=reason,
                rolled_back_by=rolled_back_by,
                created_at_ms=int(time.time() * 1000),
            )
            session.add(rollback)
            await session.commit()

    async def create_run(
        self,
        strategy_id: uuid.UUID,
        version: int,
        run_type: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> uuid.UUID:
        async with self._factory() as session:
            run = StrategyRunModel(
                strategy_id=strategy_id,
                version=version,
                run_type=run_type,
                status="running",
                start_ms=start_ms,
                end_ms=end_ms,
                stats={},
            )
            session.add(run)
            await session.commit()
            return run.id

    async def update_run_status(
        self,
        run_id: uuid.UUID,
        status: str,
        stats: dict | None = None,
    ) -> None:
        async with self._factory() as session:
            run = await session.get(StrategyRunModel, run_id)
            if run:
                run.status = status
                if stats:
                    run.stats = stats
                await session.commit()
