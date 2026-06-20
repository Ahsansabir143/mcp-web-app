from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from shared.schemas.analytics import UnifiedDecisionSnapshot
from shared.schemas.enums import OrderSide, OrderType, TimeInForce
from shared.schemas.strategy import TradeIntent
from services.strategy_service.framework.base import BaseStrategy, EvaluationResult


# ── Field accessor ────────────────────────────────────────────────────────────


def get_field(snapshot: UnifiedDecisionSnapshot, path: str) -> float | bool | None:
    """Return a snapshot field by dot-separated path.

    Dict objects (like IndicatorState.by_interval) are traversed via key lookup.
    Decimal values are converted to float.  Missing nodes return None.
    """
    parts = path.split(".")
    val: Any = snapshot
    for part in parts:
        if isinstance(val, dict):
            val = val.get(part)
        else:
            val = getattr(val, part, None)
        if val is None:
            return None
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return float(val)
    return None


# ── Operator evaluator ────────────────────────────────────────────────────────


def evaluate_rule(val: float | bool | None, operator: str, threshold: Any) -> bool:
    """Test a single rule condition."""
    if operator == "is_not_none":
        return val is not None
    if operator == "is_none":
        return val is None
    if val is None:
        return False
    fval = (1.0 if val else 0.0) if isinstance(val, bool) else float(val)
    if operator == "gt":
        return fval > float(threshold)
    if operator == "lt":
        return fval < float(threshold)
    if operator == "gte":
        return fval >= float(threshold)
    if operator == "lte":
        return fval <= float(threshold)
    if operator == "eq":
        return abs(fval - float(threshold)) < 1e-9
    if operator == "neq":
        return abs(fval - float(threshold)) >= 1e-9
    if operator == "between":
        lo, hi = float(threshold[0]), float(threshold[1])
        return lo <= fval <= hi
    return False


# ── Intent builder ────────────────────────────────────────────────────────────


def _build_intent(
    strategy_id: uuid.UUID,
    version: int,
    direction: str,
    parameters: dict,
    snapshot: UnifiedDecisionSnapshot,
    explanation: dict,
) -> TradeIntent:
    side = OrderSide.BUY if direction == "BUY" else OrderSide.SELL
    size_usd = Decimal(str(parameters.get("size_usd", 100.0)))
    price = snapshot.market_state.price

    size = (size_usd / price) if (price and price > 0) else size_usd

    stop_loss: Decimal | None = None
    sl_pct = parameters.get("stop_loss_pct")
    if sl_pct and price:
        factor = (1.0 - float(sl_pct) / 100.0) if side == OrderSide.BUY else (1.0 + float(sl_pct) / 100.0)
        stop_loss = Decimal(str(float(price) * factor))

    take_profit: Decimal | None = None
    tp_pct = parameters.get("take_profit_pct")
    if tp_pct and price:
        factor = (1.0 + float(tp_pct) / 100.0) if side == OrderSide.BUY else (1.0 - float(tp_pct) / 100.0)
        take_profit = Decimal(str(float(price) * factor))

    order_type_str = str(parameters.get("order_type", "MARKET")).upper()
    try:
        order_type = OrderType(order_type_str)
    except ValueError:
        order_type = OrderType.MARKET

    tif_str = str(parameters.get("time_in_force", "GTC")).upper()
    try:
        tif = TimeInForce(tif_str)
    except ValueError:
        tif = TimeInForce.GTC

    return TradeIntent(
        strategy_id=strategy_id,
        strategy_version=version,
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


# ── Strategy ──────────────────────────────────────────────────────────────────


class RuleBasedStrategy(BaseStrategy):
    """Evaluates a list of rule dicts against a UnifiedDecisionSnapshot.

    Rule dict keys:
        field (str)         — dot-path into snapshot
        operator (str)      — gt|lt|gte|lte|eq|neq|between|is_not_none|is_none
        value (float|list)  — threshold or [lo, hi] for between
        weight (float)      — 0.0–1.0, contribution to confidence
        side (str|None)     — "BUY"|"SELL"|None (both sides)
        description (str)   — human-readable label
    """

    def __init__(
        self,
        strategy_id: uuid.UUID,
        version: int,
        rules: list[dict],
        parameters: dict,
    ) -> None:
        super().__init__(strategy_id, version, parameters)
        self._rules = rules

    def evaluate(self, snapshot: UnifiedDecisionSnapshot) -> EvaluationResult:
        buy_weight = 0.0
        buy_total = 0.0
        sell_weight = 0.0
        sell_total = 0.0
        triggered: list[dict] = []
        blocked: list[dict] = []

        for rule in self._rules:
            field_path = str(rule.get("field", ""))
            operator = str(rule.get("operator", "gt"))
            threshold = rule.get("value")
            weight = float(rule.get("weight", 1.0))
            side = rule.get("side")
            description = str(rule.get("description", field_path))

            val = get_field(snapshot, field_path)
            passed = evaluate_rule(val, operator, threshold)

            if side in ("BUY", None):
                buy_total += weight
            if side in ("SELL", None):
                sell_total += weight

            actual = float(val) if isinstance(val, (int, float, Decimal)) else val
            entry = {
                "field": field_path,
                "description": description,
                "operator": operator,
                "threshold": threshold,
                "actual": actual,
            }
            if passed:
                if side in ("BUY", None):
                    buy_weight += weight
                if side in ("SELL", None):
                    sell_weight += weight
                triggered.append(entry)
            else:
                blocked.append(entry)

        buy_confidence = (buy_weight / buy_total) if buy_total > 0 else 0.0
        sell_confidence = (sell_weight / sell_total) if sell_total > 0 else 0.0

        if buy_confidence >= sell_confidence and buy_confidence > 0.0:
            direction: str | None = "BUY"
            confidence = buy_confidence
        elif sell_confidence > buy_confidence and sell_confidence > 0.0:
            direction = "SELL"
            confidence = sell_confidence
        else:
            direction = None
            confidence = 0.0

        signal = confidence > 0.0

        explanation = {
            "signal": signal,
            "direction": direction,
            "confidence": confidence,
            "buy_confidence": buy_confidence,
            "sell_confidence": sell_confidence,
            "triggered": triggered,
            "blocked": blocked,
            "rules_total": len(self._rules),
        }

        trade_intent: TradeIntent | None = None
        if signal and direction:
            trade_intent = _build_intent(
                self.strategy_id, self.version, direction,
                self.parameters, snapshot, explanation,
            )

        return EvaluationResult(
            signal=signal,
            direction=direction,
            confidence=confidence,
            trade_intent=trade_intent,
            explanation=explanation,
        )
