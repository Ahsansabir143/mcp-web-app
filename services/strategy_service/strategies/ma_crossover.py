"""EMA9/EMA21 moving-average crossover strategy — paper mode only.

Signal logic:
  BUY  when EMA9 > EMA21 (short MA crossed above long MA)
  SELL when EMA9 < EMA21 (short MA crossed below long MA)

Confidence is the relative gap between the two EMAs, capped at 1.0.
Both EMAs must be present in the indicator_state for the configured interval
(default: "1m").  If they are absent the evaluation degrades gracefully with
signal=False rather than raising.

Parameters (stored in strategy_versions.parameters JSON):
  strategy_type   str    "ma_crossover"   — picked up by the factory
  interval        str    "1m"             — candle interval for indicators
  size_usd        float  100.0            — notional per trade
  order_type      str    "MARKET"
  stop_loss_pct   float  optional         — % below entry for BUY stop
  take_profit_pct float  optional         — % above entry for BUY target
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from shared.schemas.analytics import UnifiedDecisionSnapshot
from shared.schemas.enums import OrderSide, OrderType, TimeInForce
from shared.schemas.strategy import TradeIntent
from services.strategy_service.framework.base import BaseStrategy, EvaluationResult


class MACrossoverStrategy(BaseStrategy):
    """EMA9/EMA21 crossover with configurable interval."""

    DEFAULT_INTERVAL = "1m"

    def __init__(
        self,
        strategy_id: uuid.UUID,
        version: int,
        parameters: dict[str, Any],
        rules: list[dict] | None = None,  # accepted but ignored — interface compat
    ) -> None:
        super().__init__(strategy_id, version, parameters)

    # ── BaseStrategy ──────────────────────────────────────────────────────────

    def validate_snapshot(self, snapshot: UnifiedDecisionSnapshot) -> list[str]:
        missing = super().validate_snapshot(snapshot)
        interval = str(self.parameters.get("interval", self.DEFAULT_INTERVAL))
        by_interval = (snapshot.indicator_state.by_interval or {}) if snapshot.indicator_state else {}
        inds = by_interval.get(interval)
        if not inds or inds.ema_9 is None or inds.ema_21 is None:
            missing.append(f"indicator_state.by_interval.{interval}.ema_9/ema_21")
        return missing

    def evaluate(self, snapshot: UnifiedDecisionSnapshot) -> EvaluationResult:
        interval = str(self.parameters.get("interval", self.DEFAULT_INTERVAL))
        by_interval = (snapshot.indicator_state.by_interval or {}) if snapshot.indicator_state else {}
        inds = by_interval.get(interval)

        if not inds or inds.ema_9 is None or inds.ema_21 is None:
            return EvaluationResult(
                signal=False,
                degraded=True,
                failure_reason="missing_ema_indicators",
                explanation={
                    "signal": False,
                    "reason": f"EMA9/EMA21 not available for interval={interval!r}",
                    "strategy_type": "ma_crossover",
                },
            )

        ema9 = float(inds.ema_9)
        ema21 = float(inds.ema_21)

        if ema9 > ema21:
            direction = "BUY"
            relative_gap = (ema9 - ema21) / ema21
        elif ema9 < ema21:
            direction = "SELL"
            relative_gap = (ema21 - ema9) / ema21
        else:
            return EvaluationResult(
                signal=False,
                direction=None,
                confidence=0.0,
                explanation={
                    "signal": False,
                    "reason": "ema9_equals_ema21",
                    "ema9": ema9,
                    "ema21": ema21,
                    "strategy_type": "ma_crossover",
                },
            )

        confidence = min(relative_gap * 100.0, 1.0)

        explanation = {
            "signal": True,
            "direction": direction,
            "confidence": confidence,
            "ema9": ema9,
            "ema21": ema21,
            "relative_gap_pct": relative_gap * 100.0,
            "interval": interval,
            "strategy_type": "ma_crossover",
        }

        trade_intent = self._build_intent(direction, snapshot, explanation)

        return EvaluationResult(
            signal=True,
            direction=direction,
            confidence=confidence,
            trade_intent=trade_intent,
            explanation=explanation,
        )

    # ── Intent builder ────────────────────────────────────────────────────────

    def _build_intent(
        self,
        direction: str,
        snapshot: UnifiedDecisionSnapshot,
        explanation: dict,
    ) -> TradeIntent:
        side = OrderSide.BUY if direction == "BUY" else OrderSide.SELL
        size_usd = Decimal(str(self.parameters.get("size_usd", 100.0)))
        price = snapshot.market_state.price
        size = (size_usd / price) if (price and price > 0) else size_usd

        order_type_str = str(self.parameters.get("order_type", "MARKET")).upper()
        try:
            order_type = OrderType(order_type_str)
        except ValueError:
            order_type = OrderType.MARKET

        tif_str = str(self.parameters.get("time_in_force", "GTC")).upper()
        try:
            tif = TimeInForce(tif_str)
        except ValueError:
            tif = TimeInForce.GTC

        stop_loss: Decimal | None = None
        sl_pct = self.parameters.get("stop_loss_pct")
        if sl_pct and price:
            factor = (1.0 - float(sl_pct) / 100.0) if side == OrderSide.BUY else (1.0 + float(sl_pct) / 100.0)
            stop_loss = Decimal(str(float(price) * factor))

        take_profit: Decimal | None = None
        tp_pct = self.parameters.get("take_profit_pct")
        if tp_pct and price:
            factor = (1.0 + float(tp_pct) / 100.0) if side == OrderSide.BUY else (1.0 - float(tp_pct) / 100.0)
            take_profit = Decimal(str(float(price) * factor))

        return TradeIntent(
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            symbol=snapshot.meta.symbol,
            market_type=snapshot.meta.market_type,
            side=side,
            order_type=order_type,
            size=size,
            size_usd=size_usd,
            limit_price=price if order_type == OrderType.LIMIT else None,
            stop_loss=stop_loss,
            take_profit=take_profit,
            time_in_force=tif,
            explanation=explanation,
        )
