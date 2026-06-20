from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from shared.schemas.analytics import UnifiedDecisionSnapshot
from shared.schemas.strategy import TradeIntent


@dataclass
class EvaluationResult:
    signal: bool
    direction: str | None = None
    confidence: float | None = None
    trade_intent: TradeIntent | None = None
    explanation: dict = field(default_factory=dict)
    degraded: bool = False
    failure_reason: str | None = None


class BaseStrategy(ABC):
    """Abstract base for all strategy implementations.

    Subclasses receive a UnifiedDecisionSnapshot and return an EvaluationResult.
    No raw Binance payload access, no direct order placement, no exchange imports.
    """

    def __init__(
        self,
        strategy_id: uuid.UUID,
        version: int,
        parameters: dict[str, Any],
    ) -> None:
        self.strategy_id = strategy_id
        self.version = version
        self.parameters = parameters

    @abstractmethod
    def evaluate(self, snapshot: UnifiedDecisionSnapshot) -> EvaluationResult: ...

    def validate_snapshot(self, snapshot: UnifiedDecisionSnapshot) -> list[str]:
        """Return list of missing required section names."""
        missing: list[str] = []
        if snapshot.meta.snapshot_timestamp_ms == 0:
            missing.append("meta.snapshot_timestamp_ms")
        if snapshot.market_state.price is None:
            missing.append("market_state.price")
        return missing

    def matches_symbol(self, symbol_filters: list[str], symbol: str) -> bool:
        if not symbol_filters:
            return True
        return symbol in symbol_filters
