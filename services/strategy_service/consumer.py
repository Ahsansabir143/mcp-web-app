from __future__ import annotations

import asyncio
import time

from shared.redis.client import RedisClient, get_redis_client, stream_read_group
from shared.redis.streams import StreamNames
from shared.schemas.analytics import UnifiedDecisionSnapshot
from shared.schemas.enums import StrategyState
from shared.utils.logging import get_logger
from services.strategy_service.config import StrategyServiceSettings
from services.strategy_service.framework.evaluator import StrategyEvaluator
from services.strategy_service.framework.registry import StrategyRegistry
from services.strategy_service.framework.factory import build_strategy
from services.strategy_service.intent.publisher import IntentPublisher
from services.strategy_service.lifecycle.transitions import LifecycleManager
from services.strategy_service.persistence.repository import StrategyRepository

log = get_logger("strategy_service.consumer")


class StrategyConsumer:
    """Reads UnifiedDecisionSnapshots from stream:analytics:derived, evaluates
    all active strategies, and publishes TradeIntents to stream:strategy:intents.

    DB writes (evaluation + action records) are gated on a repository being
    provided; the consumer is fully functional without one for testing.
    """

    def __init__(
        self,
        settings: StrategyServiceSettings | None = None,
        redis: RedisClient | None = None,
        repository: StrategyRepository | None = None,
    ) -> None:
        self._settings = settings or StrategyServiceSettings()
        self._redis = redis or get_redis_client()
        self._repository = repository
        self._registry = StrategyRegistry()
        self._lifecycle = LifecycleManager()
        self._publisher = IntentPublisher(self._redis)
        self._last_reload_at: float = 0.0
        self._running = False

    # ── Public ────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        log.info("strategy consumer starting")
        await self._reload_strategies()
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("consumer tick error", exc_info=exc)
                await asyncio.sleep(1.0)

    async def stop(self) -> None:
        self._running = False

    @property
    def registry(self) -> StrategyRegistry:
        return self._registry

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        now = time.monotonic()
        if now - self._last_reload_at >= self._settings.strategy_reload_interval_s:
            await self._reload_strategies()
            self._last_reload_at = now

        messages = await stream_read_group(
            self._redis,
            StreamNames.ANALYTICS_DERIVED,
            self._settings.consumer_group,
            self._settings.consumer_name,
            count=self._settings.batch_size,
            block_ms=self._settings.block_ms,
        )

        for _stream, entries in (messages or []):
            for msg_id, fields in entries:
                await self._process(msg_id, fields)

    async def _process(self, msg_id: str, fields: dict) -> None:
        raw = fields.get("snapshot", "")
        if not raw:
            await self._ack(msg_id)
            return

        try:
            snapshot = UnifiedDecisionSnapshot.model_validate_json(raw)
        except Exception as exc:
            log.warning("failed to parse snapshot", exc_info=exc)
            await self._ack(msg_id)
            return

        symbol = snapshot.meta.symbol
        evaluators = self._registry.evaluators_for_symbol(symbol)

        for evaluator in evaluators:
            await self._evaluate_one(evaluator, snapshot)

        await self._ack(msg_id)

    async def _evaluate_one(
        self,
        evaluator: StrategyEvaluator,
        snapshot: UnifiedDecisionSnapshot,
    ) -> None:
        try:
            eval_record = evaluator.evaluate(snapshot)

            if self._repository:
                try:
                    await self._repository.save_evaluation(eval_record)
                except Exception as exc:
                    log.error("evaluation persist error", exc_info=exc)

            if (
                evaluator.can_emit_intent()
                and eval_record.signal
                and eval_record.trade_intent is not None
            ):
                await self._publisher.publish(eval_record.trade_intent, eval_record.id)
                if self._repository:
                    try:
                        await self._repository.save_action(
                            strategy_id=evaluator._strategy_id,
                            version=evaluator._version,
                            action_type="intent_published",
                            triggered_by="consumer",
                            details={
                                "intent_id": str(eval_record.trade_intent.intent_id),
                                "symbol": snapshot.meta.symbol,
                                "direction": eval_record.direction,
                            },
                        )
                    except Exception as exc:
                        log.error("action persist error", exc_info=exc)

        except Exception as exc:
            log.error(
                "evaluation error",
                strategy_id=str(evaluator._strategy_id),
                exc_info=exc,
            )

    async def _ack(self, msg_id: str) -> None:
        try:
            await self._redis.xack(
                StreamNames.ANALYTICS_DERIVED,
                self._settings.consumer_group,
                msg_id,
            )
        except Exception as exc:
            log.warning("xack failed", exc_info=exc)

    async def _reload_strategies(self) -> None:
        if not self._repository:
            return
        try:
            rows = await self._repository.list_active_strategies()
            self._registry.clear()
            for strategy_model, version_model in rows:
                strategy_obj = build_strategy(
                    strategy_id=strategy_model.id,
                    version=version_model.version,
                    rules=version_model.rules or [],
                    parameters=version_model.parameters or {},
                )
                try:
                    state = StrategyState(strategy_model.state)
                except ValueError:
                    log.warning(
                        "unknown strategy state, skipping",
                        strategy_id=str(strategy_model.id),
                        state=strategy_model.state,
                    )
                    continue
                evaluator = StrategyEvaluator(
                    strategy_id=strategy_model.id,
                    version=version_model.version,
                    state=state,
                    strategy=strategy_obj,
                    lifecycle=self._lifecycle,
                    symbol_filters=strategy_model.symbol_filters or [],
                )
                self._registry.register(evaluator)
            log.info("strategies loaded", count=len(self._registry))
        except Exception as exc:
            log.error("strategy reload error", exc_info=exc)
