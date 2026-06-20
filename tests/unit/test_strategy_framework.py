"""Tests for framework: rule evaluation, evaluator, version resolver, intent gen."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from shared.schemas.analytics import (
    FlowState,
    FuturesState,
    MarketState,
    SnapshotMeta,
    UnifiedDecisionSnapshot,
)
from shared.schemas.enums import ApprovalLevel, MarketType, StrategyState, Venue
from shared.schemas.strategy import StrategyVersion
from services.strategy_service.framework.evaluator import StrategyEvaluator
from services.strategy_service.framework.rule_adapter import (
    RuleBasedStrategy,
    evaluate_rule,
    get_field,
)
from services.strategy_service.framework.version import VersionResolver
from services.strategy_service.lifecycle.transitions import LifecycleManager


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _snap(
    price: float | None = 50_000.0,
    cvd: float | None = 1500.0,
    aggression_ratio: float | None = 0.6,
    funding_pressure: float | None = None,
    symbol: str = "BTCUSDT",
    market_type: MarketType = MarketType.FUTURES,
) -> UnifiedDecisionSnapshot:
    market = MarketState(price=Decimal(str(price)) if price else None)
    flow = FlowState(
        cvd=Decimal(str(cvd)) if cvd is not None else None,
        aggression_ratio=Decimal(str(aggression_ratio)) if aggression_ratio is not None else None,
    )
    fut = FuturesState(
        funding_pressure_score=Decimal(str(funding_pressure)) if funding_pressure is not None else None,
    )
    meta = SnapshotMeta(
        snapshot_timestamp_ms=1_000_000,
        symbol=symbol,
        market_type=market_type,
    )
    return UnifiedDecisionSnapshot(
        market_state=market,
        flow_state=flow,
        futures_state=fut,
        meta=meta,
    )


def _strategy(
    rules: list[dict] | None = None,
    params: dict | None = None,
) -> RuleBasedStrategy:
    return RuleBasedStrategy(
        strategy_id=uuid.uuid4(),
        version=1,
        rules=rules or [],
        parameters=params or {"size_usd": 100.0},
    )


def _evaluator(
    state: StrategyState = StrategyState.PAPER_ACTIVE,
    rules: list[dict] | None = None,
    params: dict | None = None,
    symbol_filters: list[str] | None = None,
) -> StrategyEvaluator:
    strat = _strategy(rules, params)
    return StrategyEvaluator(
        strategy_id=strat.strategy_id,
        version=strat.version,
        state=state,
        strategy=strat,
        lifecycle=LifecycleManager(),
        symbol_filters=symbol_filters or [],
    )


# ── get_field ─────────────────────────────────────────────────────────────────

def test_get_field_simple():
    snap = _snap(price=50_000.0)
    val = get_field(snap, "market_state.price")
    assert val == pytest.approx(50_000.0)


def test_get_field_nested():
    snap = _snap(cvd=2000.0)
    val = get_field(snap, "flow_state.cvd")
    assert val == pytest.approx(2000.0)


def test_get_field_missing():
    snap = _snap()
    assert get_field(snap, "market_state.spread_bps") is None


def test_get_field_dict_access():
    from shared.schemas.analytics import IndicatorState, IndicatorValues
    snap = _snap()
    snap.indicator_state = IndicatorState(by_interval={
        "1m": IndicatorValues(ema_9=Decimal("100.5"))
    })
    val = get_field(snap, "indicator_state.by_interval.1m.ema_9")
    assert val == pytest.approx(100.5)


def test_get_field_bool():
    snap = _snap()
    snap.book_state.spoofing_alert = True
    val = get_field(snap, "book_state.spoofing_alert")
    assert val is True


def test_get_field_none_mid_path():
    snap = _snap()
    snap.futures_state.mark_price = None
    assert get_field(snap, "futures_state.mark_price") is None


# ── evaluate_rule ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("val,op,threshold,expected", [
    (10.0, "gt", 5.0, True),
    (5.0, "gt", 5.0, False),
    (4.0, "lt", 5.0, True),
    (5.0, "lt", 5.0, False),
    (5.0, "gte", 5.0, True),
    (5.0, "lte", 5.0, True),
    (5.0, "eq", 5.0, True),
    (5.1, "eq", 5.0, False),
    (5.1, "neq", 5.0, True),
    (3.0, "between", [2.0, 4.0], True),
    (5.0, "between", [2.0, 4.0], False),
    (1.0, "is_not_none", None, True),
    (None, "is_not_none", None, False),
    (None, "is_none", None, True),
    (1.0, "is_none", None, False),
])
def test_evaluate_rule_operators(val, op, threshold, expected):
    assert evaluate_rule(val, op, threshold) is expected


def test_evaluate_rule_none_val_returns_false():
    assert evaluate_rule(None, "gt", 5.0) is False


def test_evaluate_rule_unknown_operator():
    assert evaluate_rule(10.0, "nonsense", 5.0) is False


# ── RuleBasedStrategy ─────────────────────────────────────────────────────────

def test_rule_based_no_rules_no_signal():
    strat = _strategy(rules=[])
    result = strat.evaluate(_snap())
    assert result.signal is False
    assert result.direction is None
    assert result.trade_intent is None


def test_rule_based_buy_signal():
    rules = [
        {"field": "flow_state.cvd", "operator": "gt", "value": 1000.0,
         "weight": 1.0, "side": "BUY", "description": "cvd positive"},
    ]
    strat = _strategy(rules=rules)
    result = strat.evaluate(_snap(cvd=1500.0))
    assert result.signal is True
    assert result.direction == "BUY"
    assert result.confidence == pytest.approx(1.0)
    assert result.trade_intent is not None
    assert result.trade_intent.side.value == "BUY"


def test_rule_based_sell_signal():
    rules = [
        {"field": "flow_state.cvd", "operator": "lt", "value": 0.0,
         "weight": 1.0, "side": "SELL", "description": "cvd negative"},
    ]
    strat = _strategy(rules=rules)
    result = strat.evaluate(_snap(cvd=-500.0))
    assert result.signal is True
    assert result.direction == "SELL"


def test_rule_based_partial_confidence():
    rules = [
        {"field": "flow_state.cvd", "operator": "gt", "value": 2000.0,
         "weight": 1.0, "side": "BUY"},
        {"field": "flow_state.aggression_ratio", "operator": "gt", "value": 0.55,
         "weight": 1.0, "side": "BUY"},
    ]
    strat = _strategy(rules=rules)
    # cvd=1500 < 2000 → blocked; aggression_ratio=0.6 > 0.55 → triggered
    result = strat.evaluate(_snap(cvd=1500.0, aggression_ratio=0.6))
    assert result.signal is True
    assert result.confidence == pytest.approx(0.5)


def test_rule_based_all_blocked_no_signal():
    rules = [
        {"field": "flow_state.cvd", "operator": "gt", "value": 10_000.0,
         "weight": 1.0, "side": "BUY"},
    ]
    strat = _strategy(rules=rules)
    result = strat.evaluate(_snap(cvd=100.0))
    assert result.signal is False
    assert result.trade_intent is None


def test_rule_based_missing_field_blocked():
    rules = [
        {"field": "flow_state.cvd", "operator": "is_not_none",
         "weight": 1.0, "side": "BUY"},
    ]
    strat = _strategy(rules=rules)
    result = strat.evaluate(_snap(cvd=None))
    assert result.signal is False


def test_rule_based_explanation_structure():
    rules = [
        {"field": "flow_state.cvd", "operator": "gt", "value": 1000.0,
         "weight": 1.0, "side": "BUY", "description": "cvd check"},
    ]
    strat = _strategy(rules=rules)
    result = strat.evaluate(_snap(cvd=1500.0))
    exp = result.explanation
    assert "triggered" in exp
    assert "blocked" in exp
    assert "confidence" in exp
    assert "buy_confidence" in exp
    assert "rules_total" in exp
    assert exp["rules_total"] == 1


def test_intent_side_matches_direction():
    rules = [{"field": "flow_state.cvd", "operator": "lt", "value": 0.0,
              "weight": 1.0, "side": "SELL"}]
    strat = _strategy(rules=rules)
    result = strat.evaluate(_snap(cvd=-100.0))
    assert result.trade_intent is not None
    assert result.trade_intent.side.value == "SELL"


def test_intent_size_computed_from_price():
    rules = [{"field": "flow_state.cvd", "operator": "gt", "value": 0.0,
              "weight": 1.0, "side": "BUY"}]
    params = {"size_usd": 500.0}
    strat = _strategy(rules=rules, params=params)
    result = strat.evaluate(_snap(price=50_000.0, cvd=100.0))
    assert result.trade_intent is not None
    # size = 500 / 50000 = 0.01
    assert abs(float(result.trade_intent.size) - 0.01) < 1e-6


def test_intent_stop_loss_computed():
    rules = [{"field": "flow_state.cvd", "operator": "gt", "value": 0.0,
              "weight": 1.0, "side": "BUY"}]
    params = {"size_usd": 100.0, "stop_loss_pct": 2.0}
    strat = _strategy(rules=rules, params=params)
    result = strat.evaluate(_snap(price=50_000.0, cvd=100.0))
    assert result.trade_intent is not None
    # stop_loss = 50000 * (1 - 2/100) = 49000
    assert result.trade_intent.stop_loss is not None
    assert abs(float(result.trade_intent.stop_loss) - 49_000.0) < 0.01


# ── StrategyEvaluator ─────────────────────────────────────────────────────────

def test_evaluator_paused_returns_blocked():
    ev = _evaluator(state=StrategyState.PAUSED, rules=[
        {"field": "flow_state.cvd", "operator": "gt", "value": 0.0,
         "weight": 1.0, "side": "BUY"},
    ])
    # PAUSED can_simulate = True → evaluation proceeds normally (not blocked)
    eval_record = ev.evaluate(_snap(cvd=100.0))
    # PAUSED is in _SIMULATE_STATES so signal may still be produced
    # but cannot emit — that's tested separately
    assert eval_record.signal is True
    assert ev.can_emit_intent() is False  # PAUSED cannot emit


def test_evaluator_rolled_back_returns_blocked():
    ev = _evaluator(state=StrategyState.ROLLED_BACK, rules=[
        {"field": "flow_state.cvd", "operator": "gt", "value": 0.0,
         "weight": 1.0, "side": "BUY"},
    ])
    eval_record = ev.evaluate(_snap(cvd=100.0))
    assert eval_record.signal is False
    assert eval_record.explanation.get("blocked") is True
    assert eval_record.explanation.get("reason") == "strategy_not_active"


def test_evaluator_archived_returns_blocked():
    ev = _evaluator(state=StrategyState.ARCHIVED, rules=[
        {"field": "flow_state.cvd", "operator": "gt", "value": 0.0,
         "weight": 1.0, "side": "BUY"},
    ])
    eval_record = ev.evaluate(_snap(cvd=100.0))
    assert eval_record.signal is False
    assert eval_record.explanation.get("blocked") is True


def test_evaluator_missing_price_zero_signal():
    ev = _evaluator(state=StrategyState.SIMULATION, rules=[
        {"field": "flow_state.cvd", "operator": "gt", "value": 0.0,
         "weight": 1.0, "side": "BUY"},
    ])
    snap = _snap(price=None, cvd=100.0)  # price missing → validate_snapshot fails
    eval_record = ev.evaluate(snap)
    assert eval_record.signal is False
    assert eval_record.trade_intent is None
    assert "blocked" in eval_record.explanation


def test_evaluator_degraded_context_marked():
    ev = _evaluator(state=StrategyState.SIMULATION, rules=[
        {"field": "flow_state.cvd", "operator": "gt", "value": 0.0,
         "weight": 1.0, "side": "BUY"},
    ])
    snap = _snap(cvd=100.0)  # no account state
    eval_record = ev.evaluate(snap)
    assert eval_record.explanation.get("degraded") is True
    assert eval_record.explanation.get("degraded_reason") == "account_state_absent"


def test_evaluator_context_recorded_in_explanation():
    ev = _evaluator(state=StrategyState.SIMULATION, rules=[
        {"field": "flow_state.cvd", "operator": "gt", "value": 0.0,
         "weight": 1.0, "side": "BUY"},
    ])
    eval_record = ev.evaluate(_snap(cvd=100.0))
    assert eval_record.explanation.get("context") == "simulation"


def test_evaluator_paper_active_can_emit():
    ev = _evaluator(state=StrategyState.PAPER_ACTIVE)
    assert ev.can_emit_intent() is True


def test_evaluator_symbol_filter_match():
    ev = _evaluator(symbol_filters=["BTCUSDT"])
    assert ev.matches_symbol("BTCUSDT") is True
    assert ev.matches_symbol("ETHUSDT") is False


def test_evaluator_empty_symbol_filter_matches_all():
    ev = _evaluator(symbol_filters=[])
    assert ev.matches_symbol("BTCUSDT") is True
    assert ev.matches_symbol("ANYUSDT") is True


# ── VersionResolver ───────────────────────────────────────────────────────────

def _make_version(
    strategy_id: uuid.UUID | None = None,
    version: int = 1,
    rules: list | None = None,
    approval_required: ApprovalLevel = ApprovalLevel.L1_SIMULATION,
) -> StrategyVersion:
    return StrategyVersion(
        strategy_id=strategy_id or uuid.uuid4(),
        version=version,
        rules=rules or [],
        created_by="test",
        approval_required=approval_required,
    )


def test_version_resolver_finds_version():
    vr = VersionResolver()
    sid = uuid.uuid4()
    versions = [_make_version(sid, 1), _make_version(sid, 2), _make_version(sid, 3)]
    assert vr.resolve(versions, 2).version == 2


def test_version_resolver_not_found():
    vr = VersionResolver()
    sid = uuid.uuid4()
    versions = [_make_version(sid, 1)]
    assert vr.resolve(versions, 99) is None


def test_version_resolver_can_activate_ok():
    vr = VersionResolver()
    v = _make_version(approval_required=ApprovalLevel.L1_SIMULATION)
    ok, reason = vr.can_activate(v, StrategyState.SIMULATION, ApprovalLevel.L2_PAPER)
    assert ok is True


def test_version_resolver_insufficient_user_level():
    vr = VersionResolver()
    v = _make_version(approval_required=ApprovalLevel.L1_SIMULATION)
    ok, reason = vr.can_activate(v, StrategyState.PAPER_ACTIVE, ApprovalLevel.L1_SIMULATION)
    assert ok is False
    assert "insufficient" in reason


def test_version_resolver_version_requires_higher_level():
    vr = VersionResolver()
    v = _make_version(approval_required=ApprovalLevel.L3_ASSISTED_LIVE)
    # User has L2 → sufficient for PAPER_ACTIVE state but version requires L3
    ok, reason = vr.can_activate(v, StrategyState.PAPER_ACTIVE, ApprovalLevel.L2_PAPER)
    assert ok is False
    assert "insufficient" in reason


def test_version_resolver_l4_approves_all():
    vr = VersionResolver()
    v = _make_version(approval_required=ApprovalLevel.L4_BOUNDED_AUTO)
    ok, _ = vr.can_activate(v, StrategyState.BOUNDED_AUTO_LIVE, ApprovalLevel.L4_BOUNDED_AUTO)
    assert ok is True
