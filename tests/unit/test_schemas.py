"""Unit tests for canonical schemas."""
import uuid
from decimal import Decimal

import pytest

from shared.schemas.enums import (
    ApprovalLevel,
    EventType,
    MarketType,
    OrderSide,
    OrderType,
    StrategyState,
    TradingMode,
    Venue,
)
from shared.schemas.events import NormalizedEvent
from shared.schemas.analytics import (
    BookState,
    FlowState,
    FuturesState,
    IndicatorState,
    MarketState,
    SnapshotMeta,
    UnifiedDecisionSnapshot,
)
from shared.schemas.strategy import StrategyDefinition, TradeIntent
from shared.schemas.execution import ApprovalPolicy, ExecutionRequest, RiskDecision
from shared.schemas.risk import IncidentRecord, RiskLimits


class TestNormalizedEvent:
    def test_basic_construction(self):
        event = NormalizedEvent(
            event_type=EventType.TRADE,
            venue=Venue.BINANCE,
            market_type=MarketType.FUTURES,
            symbol="BTCUSDT",
            timestamp_ms=1_700_000_000_000,
            received_ms=1_700_000_000_001,
            data={"price": "50000", "qty": "0.1"},
        )
        assert event.symbol == "BTCUSDT"
        assert event.venue == Venue.BINANCE
        assert event.raw is None

    def test_with_raw(self):
        event = NormalizedEvent(
            event_type=EventType.KLINE,
            venue=Venue.BINANCE,
            market_type=MarketType.SPOT,
            symbol="ETHUSDT",
            timestamp_ms=1_700_000_000_000,
            received_ms=1_700_000_000_001,
            data={"interval": "1m"},
            raw={"e": "kline", "s": "ETHUSDT"},
        )
        assert event.raw is not None
        assert event.raw["e"] == "kline"

    def test_immutable(self):
        event = NormalizedEvent(
            event_type=EventType.BOOK_TICKER,
            venue=Venue.BINANCE,
            market_type=MarketType.FUTURES,
            symbol="BTCUSDT",
            timestamp_ms=1_700_000_000_000,
            received_ms=1_700_000_000_001,
            data={},
        )
        with pytest.raises(Exception):
            event.symbol = "ETHUSDT"


class TestUnifiedDecisionSnapshot:
    def test_default_sections(self):
        snap = UnifiedDecisionSnapshot(
            meta=SnapshotMeta(
                snapshot_timestamp_ms=1_700_000_000_000,
                symbol="BTCUSDT",
                market_type=MarketType.FUTURES,
            )
        )
        assert snap.market_state is not None
        assert snap.book_state is not None
        assert snap.flow_state is not None
        assert snap.futures_state is not None
        assert snap.indicator_state is not None
        assert snap.account_state is not None
        assert snap.risk_state is not None
        assert snap.strategy_state is not None
        assert snap.execution_state is not None
        assert snap.meta.symbol == "BTCUSDT"

    def test_market_state_fields(self):
        ms = MarketState(
            price=Decimal("50000"),
            bid=Decimal("49999"),
            ask=Decimal("50001"),
            spread=Decimal("2"),
            spread_bps=Decimal("0.04"),
        )
        assert ms.spread == Decimal("2")

    def test_flow_state_cvd(self):
        fs = FlowState(
            cvd=Decimal("150000"),
            delta=Decimal("5000"),
            aggression_ratio=Decimal("0.6"),
        )
        assert fs.aggression_ratio == Decimal("0.6")


class TestTradeIntent:
    def test_construction(self):
        intent = TradeIntent(
            strategy_id=uuid.uuid4(),
            symbol="BTCUSDT",
            market_type=MarketType.FUTURES,
            side=OrderSide.BUY,
            size=Decimal("0.01"),
            explanation={"reason": "RSI oversold"},
        )
        assert intent.reduce_only is False
        assert intent.order_type == OrderType.MARKET

    def test_auto_intent_id(self):
        i1 = TradeIntent(
            symbol="BTCUSDT",
            market_type=MarketType.FUTURES,
            side=OrderSide.SELL,
            size=Decimal("0.01"),
        )
        i2 = TradeIntent(
            symbol="BTCUSDT",
            market_type=MarketType.FUTURES,
            side=OrderSide.SELL,
            size=Decimal("0.01"),
        )
        assert i1.intent_id != i2.intent_id


class TestRiskDecision:
    def test_failed_decision(self):
        rd = RiskDecision(
            passed=False,
            checks={"max_position_size": True, "kill_switch": False},
            failures=["kill_switch"],
            warnings=[],
        )
        assert rd.passed is False
        assert "kill_switch" in rd.failures

    def test_passed_decision(self):
        rd = RiskDecision(passed=True, checks={"max_position_size": True})
        assert rd.passed is True
        assert rd.failures == []


class TestApprovalPolicy:
    def test_defaults(self):
        policy = ApprovalPolicy(
            user_id="user-1",
            account_id="acct-1",
            level=ApprovalLevel.L2_PAPER,
        )
        assert policy.paper_only is True
        assert policy.live_enabled is False
        assert policy.max_leverage == 5.0


class TestRiskLimits:
    def test_defaults(self):
        limits = RiskLimits()
        assert limits.max_position_size_usd == Decimal("1000")
        assert limits.max_concurrent_positions == 3


class TestStrategyDefinition:
    def test_construction(self):
        s = StrategyDefinition(
            user_id="user-1",
            name="RSI Mean Reversion",
            market_type=MarketType.FUTURES,
        )
        assert s.state == StrategyState.DRAFT
        assert s.current_version == 1
        assert s.id is not None
