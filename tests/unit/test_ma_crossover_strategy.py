"""Track 2 tests — MACrossoverStrategy evaluation logic and factory dispatch."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from shared.schemas.analytics import (
    IndicatorState,
    IndicatorValues,
    MarketState,
    SnapshotMeta,
    UnifiedDecisionSnapshot,
)
from shared.schemas.enums import MarketType, OrderSide
from services.strategy_service.framework.factory import build_strategy
from services.strategy_service.framework.rule_adapter import RuleBasedStrategy
from services.strategy_service.strategies.ma_crossover import MACrossoverStrategy


# ── Fixture helpers ───────────────────────────────────────────────────────────


def _sid() -> uuid.UUID:
    return uuid.uuid4()


def _snap(
    ema9: float | None = None,
    ema21: float | None = None,
    price: float = 65_000.0,
    interval: str = "1m",
    symbol: str = "BTCUSDT",
    market_type: MarketType = MarketType.SPOT,
) -> UnifiedDecisionSnapshot:
    ind_values = IndicatorValues(
        ema_9=Decimal(str(ema9)) if ema9 is not None else None,
        ema_21=Decimal(str(ema21)) if ema21 is not None else None,
    )
    ind_state = IndicatorState(by_interval={interval: ind_values})
    market = MarketState(price=Decimal(str(price)))
    meta = SnapshotMeta(
        snapshot_timestamp_ms=1_000_000,
        symbol=symbol,
        market_type=market_type,
    )
    return UnifiedDecisionSnapshot(
        market_state=market,
        indicator_state=ind_state,
        meta=meta,
    )


def _strategy(
    interval: str = "1m",
    size_usd: float = 100.0,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
) -> MACrossoverStrategy:
    params: dict = {
        "strategy_type": "ma_crossover",
        "interval": interval,
        "size_usd": size_usd,
    }
    if stop_loss_pct is not None:
        params["stop_loss_pct"] = stop_loss_pct
    if take_profit_pct is not None:
        params["take_profit_pct"] = take_profit_pct
    return MACrossoverStrategy(strategy_id=_sid(), version=1, parameters=params)


# ── BUY signal ────────────────────────────────────────────────────────────────


def test_buy_signal_when_ema9_above_ema21():
    strat = _strategy()
    snap = _snap(ema9=100.0, ema21=95.0)
    result = strat.evaluate(snap)
    assert result.signal is True
    assert result.direction == "BUY"
    assert result.confidence is not None and result.confidence > 0


def test_buy_signal_generates_trade_intent():
    strat = _strategy()
    snap = _snap(ema9=100.0, ema21=95.0)
    result = strat.evaluate(snap)
    assert result.trade_intent is not None
    assert result.trade_intent.side == OrderSide.BUY
    assert result.trade_intent.symbol == "BTCUSDT"
    assert result.trade_intent.market_type == MarketType.SPOT


def test_buy_size_usd_in_intent():
    strat = _strategy(size_usd=250.0)
    snap = _snap(ema9=100.0, ema21=95.0)
    result = strat.evaluate(snap)
    assert result.trade_intent is not None
    assert result.trade_intent.size_usd == Decimal("250.0")


# ── SELL signal ───────────────────────────────────────────────────────────────


def test_sell_signal_when_ema9_below_ema21():
    strat = _strategy()
    snap = _snap(ema9=90.0, ema21=95.0)
    result = strat.evaluate(snap)
    assert result.signal is True
    assert result.direction == "SELL"
    assert result.confidence is not None and result.confidence > 0


def test_sell_generates_sell_intent():
    strat = _strategy()
    snap = _snap(ema9=90.0, ema21=95.0)
    result = strat.evaluate(snap)
    assert result.trade_intent is not None
    assert result.trade_intent.side == OrderSide.SELL


# ── No signal when equal ──────────────────────────────────────────────────────


def test_no_signal_when_ema9_equals_ema21():
    strat = _strategy()
    snap = _snap(ema9=100.0, ema21=100.0)
    result = strat.evaluate(snap)
    assert result.signal is False
    assert result.trade_intent is None


# ── Degraded when indicators missing ─────────────────────────────────────────


def test_degraded_when_ema9_missing():
    strat = _strategy()
    snap = _snap(ema9=None, ema21=95.0)
    result = strat.evaluate(snap)
    assert result.signal is False
    assert result.degraded is True
    assert result.failure_reason == "missing_ema_indicators"


def test_degraded_when_ema21_missing():
    strat = _strategy()
    snap = _snap(ema9=100.0, ema21=None)
    result = strat.evaluate(snap)
    assert result.signal is False
    assert result.degraded is True


def test_degraded_when_both_missing():
    strat = _strategy()
    snap = _snap(ema9=None, ema21=None)
    result = strat.evaluate(snap)
    assert result.signal is False
    assert result.degraded is True


def test_degraded_when_wrong_interval():
    strat = _strategy(interval="5m")
    snap = _snap(ema9=100.0, ema21=95.0, interval="1m")
    result = strat.evaluate(snap)
    assert result.signal is False
    assert result.degraded is True


# ── Confidence calculation ────────────────────────────────────────────────────


def test_confidence_relative_gap():
    strat = _strategy()
    ema9, ema21 = 110.0, 100.0
    snap = _snap(ema9=ema9, ema21=ema21)
    result = strat.evaluate(snap)
    assert result.signal is True
    expected_gap = (ema9 - ema21) / ema21 * 100.0
    expected_conf = min(expected_gap, 1.0)
    assert abs((result.confidence or 0) - expected_conf) < 0.0001


def test_confidence_capped_at_1():
    strat = _strategy()
    snap = _snap(ema9=200.0, ema21=100.0)
    result = strat.evaluate(snap)
    assert result.confidence == 1.0


# ── Stop loss / take profit ───────────────────────────────────────────────────


def test_stop_loss_set_on_buy_intent():
    strat = _strategy(stop_loss_pct=2.0)
    snap = _snap(ema9=100.0, ema21=95.0, price=60000.0)
    result = strat.evaluate(snap)
    assert result.trade_intent is not None
    sl = result.trade_intent.stop_loss
    assert sl is not None
    expected_sl = Decimal(str(60000.0 * (1.0 - 2.0 / 100.0)))
    assert abs(float(sl) - float(expected_sl)) < 0.01


def test_take_profit_set_on_buy_intent():
    strat = _strategy(take_profit_pct=3.0)
    snap = _snap(ema9=100.0, ema21=95.0, price=60000.0)
    result = strat.evaluate(snap)
    assert result.trade_intent is not None
    tp = result.trade_intent.take_profit
    assert tp is not None
    expected_tp = Decimal(str(60000.0 * (1.0 + 3.0 / 100.0)))
    assert abs(float(tp) - float(expected_tp)) < 0.01


# ── validate_snapshot ─────────────────────────────────────────────────────────


def test_validate_snapshot_passes_when_complete():
    strat = _strategy()
    snap = _snap(ema9=100.0, ema21=95.0)
    missing = strat.validate_snapshot(snap)
    assert missing == []


def test_validate_snapshot_reports_missing_indicators():
    strat = _strategy()
    snap = _snap(ema9=None, ema21=None)
    missing = strat.validate_snapshot(snap)
    assert any("ema" in m for m in missing)


def test_validate_snapshot_reports_missing_price():
    strat = _strategy()
    snap = _snap(ema9=100.0, ema21=95.0)
    snap.market_state.price = None
    missing = strat.validate_snapshot(snap)
    assert "market_state.price" in missing


# ── Factory dispatch ──────────────────────────────────────────────────────────


def test_factory_returns_rule_based_by_default():
    sid = _sid()
    strat = build_strategy(
        strategy_id=sid, version=1, rules=[], parameters={}
    )
    assert isinstance(strat, RuleBasedStrategy)


def test_factory_returns_rule_based_explicit():
    sid = _sid()
    strat = build_strategy(
        strategy_id=sid, version=1, rules=[], parameters={"strategy_type": "rule_based"}
    )
    assert isinstance(strat, RuleBasedStrategy)


def test_factory_returns_ma_crossover():
    sid = _sid()
    strat = build_strategy(
        strategy_id=sid, version=1, rules=[], parameters={"strategy_type": "ma_crossover"}
    )
    assert isinstance(strat, MACrossoverStrategy)


def test_factory_unknown_type_falls_back_to_rule_based():
    sid = _sid()
    strat = build_strategy(
        strategy_id=sid, version=1, rules=[], parameters={"strategy_type": "does_not_exist"}
    )
    assert isinstance(strat, RuleBasedStrategy)


def test_factory_preserves_parameters():
    sid = _sid()
    params = {"strategy_type": "ma_crossover", "interval": "5m", "size_usd": 200.0}
    strat = build_strategy(strategy_id=sid, version=2, rules=[], parameters=params)
    assert isinstance(strat, MACrossoverStrategy)
    assert strat.parameters["interval"] == "5m"
    assert strat.version == 2


def test_factory_preserves_strategy_id():
    sid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    strat = build_strategy(
        strategy_id=sid, version=1, rules=[], parameters={"strategy_type": "ma_crossover"}
    )
    assert strat.strategy_id == sid


# ── Explanation content ───────────────────────────────────────────────────────


def test_explanation_contains_ema_values():
    strat = _strategy()
    snap = _snap(ema9=105.0, ema21=100.0)
    result = strat.evaluate(snap)
    assert result.explanation.get("ema9") == 105.0
    assert result.explanation.get("ema21") == 100.0
    assert result.explanation.get("strategy_type") == "ma_crossover"


def test_explanation_has_relative_gap_pct():
    strat = _strategy()
    snap = _snap(ema9=105.0, ema21=100.0)
    result = strat.evaluate(snap)
    pct = result.explanation.get("relative_gap_pct")
    assert pct is not None
    assert abs(pct - 5.0) < 0.0001
