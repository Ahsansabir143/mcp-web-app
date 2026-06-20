from __future__ import annotations

import time
import uuid

from shared.schemas.analytics import UnifiedDecisionSnapshot
from shared.schemas.enums import StrategyState
from shared.schemas.strategy import StrategyEvaluation, TradeIntent
from services.strategy_service.framework.base import BaseStrategy, EvaluationResult
from services.strategy_service.lifecycle.transitions import LifecycleManager


class StrategyEvaluator:
    """Wraps a BaseStrategy with lifecycle state checking and evaluation record building.

    Responsibilities:
    - Guard non-active states (returns a zero-signal blocked evaluation)
    - Detect degraded context (missing account/risk state) and annotate it
    - Build a StrategyEvaluation schema record from the EvaluationResult
    """

    def __init__(
        self,
        strategy_id: uuid.UUID,
        version: int,
        state: StrategyState,
        strategy: BaseStrategy,
        lifecycle: LifecycleManager,
        symbol_filters: list[str] | None = None,
    ) -> None:
        self._strategy_id = strategy_id
        self._version = version
        self._state = state
        self._strategy = strategy
        self._lifecycle = lifecycle
        self.symbol_filters: list[str] = symbol_filters or []

    # ── Public ────────────────────────────────────────────────────────────────

    def evaluate(
        self,
        snapshot: UnifiedDecisionSnapshot,
        run_id: uuid.UUID | None = None,
    ) -> StrategyEvaluation:
        """Evaluate the strategy against a snapshot and produce a persisted record.

        Returns a blocked evaluation if the strategy state does not allow evaluation.
        Returns a zero-signal evaluation (with degraded flag) if required snapshot
        sections are missing.
        """
        now_ms = int(time.time() * 1000)

        if not self._lifecycle.can_simulate(self._state):
            return self._blocked_eval(snapshot, run_id, "strategy_not_active")

        missing = self._strategy.validate_snapshot(snapshot)
        result = self._strategy.evaluate(snapshot)

        # Detect degraded account context
        has_account = bool(
            snapshot.account_state.balances
            or snapshot.account_state.total_equity_usd is not None
        )
        if not has_account:
            result.degraded = True
            result.explanation["degraded"] = True
            result.explanation["degraded_reason"] = "account_state_absent"

        result.explanation["context"] = self._state.value
        result.explanation["snapshot_missing_sections"] = missing

        # Missing critical sections → zero-signal, no intent
        if missing:
            return StrategyEvaluation(
                run_id=run_id,
                strategy_id=self._strategy_id,
                version=self._version,
                symbol=snapshot.meta.symbol,
                market_type=snapshot.meta.market_type,
                snapshot_timestamp_ms=snapshot.meta.snapshot_timestamp_ms,
                signal=False,
                direction=None,
                confidence=None,
                explanation={**result.explanation, "blocked": True, "reason": "missing_snapshot_sections"},
                trade_intent=None,
            )

        return StrategyEvaluation(
            run_id=run_id,
            strategy_id=self._strategy_id,
            version=self._version,
            symbol=snapshot.meta.symbol,
            market_type=snapshot.meta.market_type,
            snapshot_timestamp_ms=snapshot.meta.snapshot_timestamp_ms,
            signal=result.signal,
            direction=result.direction,
            confidence=result.confidence,
            explanation=result.explanation,
            trade_intent=result.trade_intent,
        )

    def can_emit_intent(self) -> bool:
        """True when the strategy is in a live-publishing state."""
        return self._lifecycle.can_emit_intents(self._state)

    def matches_symbol(self, symbol: str) -> bool:
        return self._strategy.matches_symbol(self.symbol_filters, symbol)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _blocked_eval(
        self,
        snapshot: UnifiedDecisionSnapshot,
        run_id: uuid.UUID | None,
        reason: str,
    ) -> StrategyEvaluation:
        return StrategyEvaluation(
            run_id=run_id,
            strategy_id=self._strategy_id,
            version=self._version,
            symbol=snapshot.meta.symbol,
            market_type=snapshot.meta.market_type,
            snapshot_timestamp_ms=snapshot.meta.snapshot_timestamp_ms,
            signal=False,
            direction=None,
            confidence=None,
            explanation={
                "signal": False,
                "blocked": True,
                "reason": reason,
                "state": self._state.value,
            },
            trade_intent=None,
        )
