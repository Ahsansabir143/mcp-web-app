from __future__ import annotations

import uuid

from services.strategy_service.framework.evaluator import StrategyEvaluator


class StrategyRegistry:
    """In-memory map of strategy_id → StrategyEvaluator.

    Thread-safety is not required; the consumer is a single-task asyncio loop.
    """

    def __init__(self) -> None:
        self._evaluators: dict[uuid.UUID, StrategyEvaluator] = {}

    def register(self, evaluator: StrategyEvaluator) -> None:
        self._evaluators[evaluator._strategy_id] = evaluator

    def remove(self, strategy_id: uuid.UUID) -> None:
        self._evaluators.pop(strategy_id, None)

    def get(self, strategy_id: uuid.UUID) -> StrategyEvaluator | None:
        return self._evaluators.get(strategy_id)

    def all_evaluators(self) -> list[StrategyEvaluator]:
        return list(self._evaluators.values())

    def evaluators_for_symbol(self, symbol: str) -> list[StrategyEvaluator]:
        return [ev for ev in self._evaluators.values() if ev.matches_symbol(symbol)]

    def clear(self) -> None:
        self._evaluators.clear()

    def __len__(self) -> int:
        return len(self._evaluators)
