"""Tests for IntentPublisher, SimulationRunner, StrategyRegistry, and consumer routing."""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.schemas.analytics import (
    FlowState,
    MarketState,
    SnapshotMeta,
    UnifiedDecisionSnapshot,
)
from shared.schemas.enums import MarketType, StrategyState
from services.strategy_service.consumer import StrategyConsumer
from services.strategy_service.framework.evaluator import StrategyEvaluator
from services.strategy_service.framework.registry import StrategyRegistry
from services.strategy_service.framework.rule_adapter import RuleBasedStrategy
from services.strategy_service.framework.simulation import SimulationRunner
from services.strategy_service.intent.publisher import IntentPublisher
from services.strategy_service.lifecycle.transitions import LifecycleManager


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _snap(
    price: float = 50_000.0,
    cvd: float | None = 1500.0,
    symbol: str = "BTCUSDT",
) -> UnifiedDecisionSnapshot:
    return UnifiedDecisionSnapshot(
        market_state=MarketState(price=Decimal(str(price))),
        flow_state=FlowState(cvd=Decimal(str(cvd)) if cvd is not None else None),
        meta=SnapshotMeta(
            snapshot_timestamp_ms=1_000_000,
            symbol=symbol,
            market_type=MarketType.FUTURES,
        ),
    )


def _buy_rule():
    return [{"field": "flow_state.cvd", "operator": "gt", "value": 0.0,
             "weight": 1.0, "side": "BUY", "description": "cvd positive"}]


def _make_evaluator(
    state: StrategyState = StrategyState.PAPER_ACTIVE,
    rules: list | None = None,
    symbol_filters: list | None = None,
) -> StrategyEvaluator:
    sid = uuid.uuid4()
    strat = RuleBasedStrategy(
        strategy_id=sid,
        version=1,
        rules=rules or _buy_rule(),
        parameters={"size_usd": 100.0},
    )
    return StrategyEvaluator(
        strategy_id=sid,
        version=1,
        state=state,
        strategy=strat,
        lifecycle=LifecycleManager(),
        symbol_filters=symbol_filters or [],
    )


# ── IntentPublisher ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_intent_publisher_calls_xadd():
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value="1-1")
    publisher = IntentPublisher(redis)

    from shared.schemas.strategy import TradeIntent
    from shared.schemas.enums import OrderSide, OrderType

    intent = TradeIntent(
        strategy_id=uuid.uuid4(),
        strategy_version=1,
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        side=OrderSide.BUY,
        size=Decimal("0.001"),
    )
    eval_id = uuid.uuid4()
    await publisher.publish(intent, eval_id)

    redis.xadd.assert_called_once()
    call_args = redis.xadd.call_args
    stream_name = call_args[0][0]
    from shared.redis.streams import StreamNames
    assert stream_name == StreamNames.STRATEGY_INTENTS


@pytest.mark.asyncio
async def test_intent_publisher_message_contains_intent_json():
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value="1-1")
    publisher = IntentPublisher(redis)

    from shared.schemas.strategy import TradeIntent
    from shared.schemas.enums import OrderSide

    intent = TradeIntent(
        symbol="ETHUSDT",
        market_type=MarketType.FUTURES,
        side=OrderSide.SELL,
        size=Decimal("0.5"),
    )
    await publisher.publish(intent, uuid.uuid4())

    fields = redis.xadd.call_args[0][1]
    assert "intent" in fields
    parsed = json.loads(fields["intent"])
    assert parsed["symbol"] == "ETHUSDT"


@pytest.mark.asyncio
async def test_intent_publisher_includes_evaluation_id():
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value="1-1")
    publisher = IntentPublisher(redis)

    from shared.schemas.strategy import TradeIntent
    from shared.schemas.enums import OrderSide

    intent = TradeIntent(
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        side=OrderSide.BUY,
        size=Decimal("0.001"),
    )
    eval_id = uuid.uuid4()
    await publisher.publish(intent, eval_id)

    fields = redis.xadd.call_args[0][1]
    assert fields["evaluation_id"] == str(eval_id)


# ── StrategyRegistry ──────────────────────────────────────────────────────────

def test_registry_register_and_get():
    reg = StrategyRegistry()
    ev = _make_evaluator()
    reg.register(ev)
    assert reg.get(ev._strategy_id) is ev


def test_registry_remove():
    reg = StrategyRegistry()
    ev = _make_evaluator()
    reg.register(ev)
    reg.remove(ev._strategy_id)
    assert reg.get(ev._strategy_id) is None


def test_registry_symbol_filter():
    reg = StrategyRegistry()
    ev_btc = _make_evaluator(symbol_filters=["BTCUSDT"])
    ev_any = _make_evaluator(symbol_filters=[])
    reg.register(ev_btc)
    reg.register(ev_any)

    btc_matches = reg.evaluators_for_symbol("BTCUSDT")
    eth_matches = reg.evaluators_for_symbol("ETHUSDT")

    assert ev_btc in btc_matches
    assert ev_any in btc_matches
    assert ev_btc not in eth_matches
    assert ev_any in eth_matches


def test_registry_clear():
    reg = StrategyRegistry()
    reg.register(_make_evaluator())
    reg.register(_make_evaluator())
    assert len(reg) == 2
    reg.clear()
    assert len(reg) == 0


# ── SimulationRunner ──────────────────────────────────────────────────────────

def test_simulation_runner_returns_one_result_per_snapshot():
    ev = _make_evaluator(state=StrategyState.SIMULATION)
    runner = SimulationRunner(ev)
    snaps = [_snap(cvd=100.0), _snap(cvd=200.0), _snap(cvd=300.0)]
    results = runner.run(snaps)
    assert len(results) == 3


def test_simulation_runner_all_signal():
    ev = _make_evaluator(state=StrategyState.SIMULATION)
    runner = SimulationRunner(ev)
    snaps = [_snap(cvd=500.0)] * 5
    results = runner.run(snaps)
    assert all(r.signal for r in results)


def test_simulation_runner_empty_snapshots():
    ev = _make_evaluator(state=StrategyState.SIMULATION)
    runner = SimulationRunner(ev)
    results = runner.run([])
    assert results == []


def test_simulation_runner_explanation_present():
    ev = _make_evaluator(state=StrategyState.SIMULATION)
    runner = SimulationRunner(ev)
    results = runner.run([_snap(cvd=100.0)])
    assert "triggered" in results[0].explanation
    assert "blocked" in results[0].explanation
    assert results[0].explanation.get("context") == "simulation"


# ── Consumer routing ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_consumer_emits_intent_for_paper_active():
    """PAPER_ACTIVE strategy with signal → publisher called."""
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value="1-1")
    redis.xack = AsyncMock()

    consumer = StrategyConsumer(redis=redis, repository=None)
    ev = _make_evaluator(state=StrategyState.PAPER_ACTIVE)
    consumer.registry.register(ev)

    snap = _snap(cvd=500.0)
    snap_json = snap.model_dump_json()

    with patch.object(consumer._publisher, "publish", new_callable=AsyncMock) as mock_pub:
        await consumer._process("1-1", {"snapshot": snap_json})
        mock_pub.assert_called_once()


@pytest.mark.asyncio
async def test_consumer_does_not_emit_for_simulation():
    """SIMULATION strategy → publisher NOT called (only records)."""
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value="1-1")
    redis.xack = AsyncMock()

    consumer = StrategyConsumer(redis=redis, repository=None)
    ev = _make_evaluator(state=StrategyState.SIMULATION)
    consumer.registry.register(ev)

    snap = _snap(cvd=500.0)
    snap_json = snap.model_dump_json()

    with patch.object(consumer._publisher, "publish", new_callable=AsyncMock) as mock_pub:
        await consumer._process("1-1", {"snapshot": snap_json})
        mock_pub.assert_not_called()


@pytest.mark.asyncio
async def test_consumer_does_not_emit_for_paused():
    """PAUSED strategy → signal may be produced, but publisher NOT called."""
    redis = AsyncMock()
    redis.xack = AsyncMock()

    consumer = StrategyConsumer(redis=redis, repository=None)
    ev = _make_evaluator(state=StrategyState.PAUSED)
    consumer.registry.register(ev)

    snap = _snap(cvd=500.0)

    with patch.object(consumer._publisher, "publish", new_callable=AsyncMock) as mock_pub:
        await consumer._process("1-1", {"snapshot": snap.model_dump_json()})
        mock_pub.assert_not_called()


@pytest.mark.asyncio
async def test_consumer_skips_empty_snapshot_field():
    redis = AsyncMock()
    redis.xack = AsyncMock()
    consumer = StrategyConsumer(redis=redis, repository=None)
    consumer.registry.register(_make_evaluator())

    with patch.object(consumer._publisher, "publish", new_callable=AsyncMock) as mock_pub:
        await consumer._process("1-1", {"snapshot": ""})
        mock_pub.assert_not_called()


@pytest.mark.asyncio
async def test_consumer_no_matching_strategy_for_symbol():
    """Strategy filtered to BTCUSDT → no evaluation for ETHUSDT snapshot."""
    redis = AsyncMock()
    redis.xack = AsyncMock()
    consumer = StrategyConsumer(redis=redis, repository=None)
    consumer.registry.register(_make_evaluator(symbol_filters=["BTCUSDT"]))

    snap = _snap(symbol="ETHUSDT")

    with patch.object(consumer._publisher, "publish", new_callable=AsyncMock) as mock_pub:
        await consumer._process("1-1", {"snapshot": snap.model_dump_json()})
        mock_pub.assert_not_called()
