"""Tests for risk checks, controls, and ExecutionRiskEngine."""
from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.risk.limits import RiskLimits
from shared.schemas.enums import ApprovalLevel, MarketType, OrderSide, OrderType, TradingMode
from shared.schemas.execution import ExecutionRequest
from shared.schemas.strategy import TradeIntent
from services.execution.controls.cooldown import CooldownControl
from services.execution.controls.kill_switch import KillSwitchControl
from services.execution.risk.checks import (
    CheckResult,
    check_max_concurrent_exposure_placeholder,
    check_max_daily_loss_placeholder,
    check_max_position_size,
    check_missing_account_context,
    check_symbol_policy,
    check_trading_mode,
    check_unsupported_order_type,
)
from services.execution.risk.engine import ExecutionRiskEngine


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _intent(
    symbol: str = "BTCUSDT",
    side: OrderSide = OrderSide.BUY,
    size: Decimal = Decimal("0.01"),
    size_usd: Decimal | None = Decimal("500"),
    order_type: OrderType = OrderType.MARKET,
    limit_price: Decimal | None = None,
) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        market_type=MarketType.FUTURES,
        side=side,
        size=size,
        size_usd=size_usd,
        order_type=order_type,
        limit_price=limit_price,
    )


def _request(
    intent: TradeIntent | None = None,
    account_id: str = "acct-1",
    trading_mode: TradingMode = TradingMode.PAPER,
    approval_level: ApprovalLevel = ApprovalLevel.L2_PAPER,
) -> ExecutionRequest:
    return ExecutionRequest(
        trade_intent=intent or _intent(),
        user_id="user-1",
        account_id=account_id,
        trading_mode=trading_mode,
        approval_level=approval_level,
    )


def _mock_redis(
    kill_switch: bool = False,
    user_paused: bool = False,
    symbol_paused: bool = False,
    circuit_breaker: bool = False,
    symbol_cooldown: bool = False,
) -> AsyncMock:
    redis = AsyncMock()
    # exists() returns 1 (truthy) or 0 (falsy) per Redis convention
    async def _exists(key: str) -> int:
        if "kill_switch" in key:
            return 1 if kill_switch else 0
        if "pause:user" in key:
            return 1 if user_paused else 0
        if "pause:symbol" in key:
            return 1 if symbol_paused else 0
        if "circuit_breaker" in key:
            return 1 if circuit_breaker else 0
        if "cooldown" in key:
            return 1 if symbol_cooldown else 0
        return 0

    redis.exists = AsyncMock(side_effect=_exists)
    redis.ttl = AsyncMock(return_value=120)
    return redis


def _engine(
    redis=None,
    kill_switch=False,
    user_paused=False,
    symbol_paused=False,
    circuit_breaker=False,
    symbol_cooldown=False,
    paper_only=True,
    has_credentials=False,
    allowed_symbols=None,
    denied_symbols=None,
    limits=None,
) -> ExecutionRiskEngine:
    r = redis or _mock_redis(kill_switch, user_paused, symbol_paused, circuit_breaker, symbol_cooldown)
    return ExecutionRiskEngine(
        redis=r,
        limits=limits or RiskLimits(),
        paper_only=paper_only,
        has_credentials=has_credentials,
        allowed_symbols=allowed_symbols,
        denied_symbols=denied_symbols or [],
    )


# ── Individual check functions ────────────────────────────────────────────────


def test_check_max_position_size_passes():
    r = check_max_position_size(_request(intent=_intent(size_usd=Decimal("400"))), Decimal("1000"))
    assert r.passed is True
    assert r.name == "max_position_size"


def test_check_max_position_size_fails():
    r = check_max_position_size(_request(intent=_intent(size_usd=Decimal("2000"))), Decimal("1000"))
    assert r.passed is False
    assert "size_usd" in r.reason


def test_check_max_position_size_at_boundary():
    r = check_max_position_size(_request(intent=_intent(size_usd=Decimal("1000"))), Decimal("1000"))
    assert r.passed is True


def test_check_max_position_size_no_size_usd_passes():
    intent = _intent(size_usd=None, limit_price=None)
    r = check_max_position_size(_request(intent=intent), Decimal("1000"))
    assert r.passed is True  # no data available → pass
    assert r.reason == "size_usd_unavailable"


def test_check_max_position_size_derived_from_price():
    intent = _intent(size=Decimal("0.02"), size_usd=None, limit_price=Decimal("50000"))
    r = check_max_position_size(_request(intent=intent), Decimal("500"))
    # notional = 0.02 * 50000 = 1000 > 500
    assert r.passed is False


def test_check_unsupported_order_type_market_ok():
    assert check_unsupported_order_type(_request()).passed is True


def test_check_unsupported_order_type_limit_ok():
    r = check_unsupported_order_type(_request(intent=_intent(order_type=OrderType.LIMIT)))
    assert r.passed is True


def test_check_unsupported_order_type_stop_blocked():
    r = check_unsupported_order_type(_request(intent=_intent(order_type=OrderType.STOP_MARKET)))
    assert r.passed is False
    assert "STOP_MARKET" in r.reason


def test_check_symbol_policy_no_restriction():
    r = check_symbol_policy(_request(), allowed_symbols=None, denied_symbols=[])
    assert r.passed is True


def test_check_symbol_policy_allowed_list_match():
    r = check_symbol_policy(_request(), allowed_symbols=["BTCUSDT", "ETHUSDT"], denied_symbols=[])
    assert r.passed is True


def test_check_symbol_policy_allowed_list_miss():
    r = check_symbol_policy(_request(), allowed_symbols=["ETHUSDT"], denied_symbols=[])
    assert r.passed is False
    assert "allowed" in r.reason


def test_check_symbol_policy_denied():
    r = check_symbol_policy(_request(), allowed_symbols=None, denied_symbols=["BTCUSDT"])
    assert r.passed is False
    assert "denied" in r.reason


def test_check_trading_mode_paper_ok():
    r = check_trading_mode(_request(trading_mode=TradingMode.PAPER), paper_only=True)
    assert r.passed is True


def test_check_trading_mode_live_blocked_when_paper_only():
    r = check_trading_mode(_request(trading_mode=TradingMode.LIVE), paper_only=True)
    assert r.passed is False
    assert "paper_only" in r.reason


def test_check_trading_mode_live_ok_when_not_paper_only():
    r = check_trading_mode(_request(trading_mode=TradingMode.LIVE), paper_only=False)
    assert r.passed is True


def test_check_missing_credentials_paper_ok():
    r = check_missing_account_context(_request(trading_mode=TradingMode.PAPER), has_credentials=False)
    assert r.passed is True


def test_check_missing_credentials_live_no_creds_blocked():
    r = check_missing_account_context(_request(trading_mode=TradingMode.LIVE), has_credentials=False)
    assert r.passed is False
    assert "credentials" in r.reason


def test_check_missing_credentials_live_with_creds_ok():
    r = check_missing_account_context(_request(trading_mode=TradingMode.LIVE), has_credentials=True)
    assert r.passed is True


def test_check_max_daily_loss_no_data_passes():
    r = check_max_daily_loss_placeholder(None, Decimal("500"))
    assert r.passed is True
    assert r.reason == "no_daily_loss_data"


def test_check_max_daily_loss_under_limit():
    r = check_max_daily_loss_placeholder(Decimal("100"), Decimal("500"))
    assert r.passed is True


def test_check_max_daily_loss_over_limit():
    r = check_max_daily_loss_placeholder(Decimal("600"), Decimal("500"))
    assert r.passed is False
    assert "daily_loss" in r.reason


def test_check_concurrent_exposure_no_data():
    r = check_max_concurrent_exposure_placeholder(None, 3)
    assert r.passed is True


def test_check_concurrent_exposure_under():
    r = check_max_concurrent_exposure_placeholder(2, 3)
    assert r.passed is True


def test_check_concurrent_exposure_at_max_blocked():
    r = check_max_concurrent_exposure_placeholder(3, 3)
    assert r.passed is False


# ── KillSwitchControl ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kill_switch_initially_inactive():
    redis = AsyncMock()
    redis.exists = AsyncMock(return_value=0)
    ks = KillSwitchControl(redis)
    assert await ks.is_kill_switch_active("acct") is False


@pytest.mark.asyncio
async def test_kill_switch_active_after_set():
    redis = AsyncMock()
    redis.exists = AsyncMock(return_value=1)
    ks = KillSwitchControl(redis)
    assert await ks.is_kill_switch_active("acct") is True


@pytest.mark.asyncio
async def test_user_pause_check():
    redis = AsyncMock()
    redis.exists = AsyncMock(return_value=1)
    ks = KillSwitchControl(redis)
    assert await ks.is_user_paused("acct") is True


@pytest.mark.asyncio
async def test_symbol_pause_check():
    redis = AsyncMock()
    redis.exists = AsyncMock(return_value=1)
    ks = KillSwitchControl(redis)
    assert await ks.is_symbol_paused("acct", "BTCUSDT") is True


# ── CooldownControl ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cooldown_inactive():
    redis = AsyncMock()
    redis.exists = AsyncMock(return_value=0)
    cd = CooldownControl(redis)
    assert await cd.is_on_cooldown("acct", "BTCUSDT") is False


@pytest.mark.asyncio
async def test_cooldown_active():
    redis = AsyncMock()
    redis.exists = AsyncMock(return_value=1)
    cd = CooldownControl(redis)
    assert await cd.is_on_cooldown("acct", "BTCUSDT") is True


@pytest.mark.asyncio
async def test_cooldown_set_calls_redis_set():
    redis = AsyncMock()
    cd = CooldownControl(redis)
    await cd.set_cooldown("acct", "BTCUSDT", 300)
    redis.set.assert_called_once()
    call_kwargs = redis.set.call_args
    assert call_kwargs[1]["ex"] == 300


@pytest.mark.asyncio
async def test_cooldown_remaining_ttl():
    redis = AsyncMock()
    redis.ttl = AsyncMock(return_value=180)
    cd = CooldownControl(redis)
    ttl = await cd.remaining_ttl("acct", "BTCUSDT")
    assert ttl == 180


# ── ExecutionRiskEngine ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_engine_all_checks_pass():
    engine = _engine()
    decision = await engine.evaluate(_request())
    assert decision.passed is True
    assert len(decision.failures) == 0


@pytest.mark.asyncio
async def test_engine_kill_switch_blocks():
    engine = _engine(kill_switch=True)
    decision = await engine.evaluate(_request())
    assert decision.passed is False
    assert "kill_switch_active" in decision.failures
    assert decision.checks.get("kill_switch") is False


@pytest.mark.asyncio
async def test_engine_user_pause_blocks():
    engine = _engine(user_paused=True)
    decision = await engine.evaluate(_request())
    assert decision.passed is False
    assert "user_paused" in decision.failures


@pytest.mark.asyncio
async def test_engine_symbol_pause_blocks():
    engine = _engine(symbol_paused=True)
    decision = await engine.evaluate(_request())
    assert decision.passed is False
    assert "symbol_paused" in decision.failures


@pytest.mark.asyncio
async def test_engine_circuit_breaker_blocks():
    engine = _engine(circuit_breaker=True)
    decision = await engine.evaluate(_request())
    assert decision.passed is False
    assert "circuit_breaker_active" in decision.failures


@pytest.mark.asyncio
async def test_engine_symbol_cooldown_blocks():
    engine = _engine(symbol_cooldown=True)
    decision = await engine.evaluate(_request())
    assert decision.passed is False
    assert "symbol_on_cooldown" in decision.failures
    assert "cooldown_remaining_s" in decision.metadata


@pytest.mark.asyncio
async def test_engine_symbol_denied_blocks():
    engine = _engine(denied_symbols=["BTCUSDT"])
    decision = await engine.evaluate(_request())
    assert decision.passed is False
    assert any("denied" in f for f in decision.failures)


@pytest.mark.asyncio
async def test_engine_position_size_exceeded_blocks():
    limits = RiskLimits(max_position_size_usd=Decimal("100"))
    engine = _engine(limits=limits)
    req = _request(intent=_intent(size_usd=Decimal("500")))
    decision = await engine.evaluate(req)
    assert decision.passed is False
    assert any("size_usd" in f for f in decision.failures)


@pytest.mark.asyncio
async def test_engine_live_mode_paper_only_blocks():
    engine = _engine(paper_only=True)
    req = _request(trading_mode=TradingMode.LIVE)
    decision = await engine.evaluate(req)
    assert decision.passed is False
    assert any("paper_only" in f for f in decision.failures)


@pytest.mark.asyncio
async def test_engine_live_mode_no_credentials_blocks():
    engine = _engine(paper_only=False, has_credentials=False)
    req = _request(trading_mode=TradingMode.LIVE)
    decision = await engine.evaluate(req)
    assert decision.passed is False
    assert any("credentials" in f for f in decision.failures)


@pytest.mark.asyncio
async def test_engine_kill_switch_short_circuits_other_checks():
    """Kill switch returns immediately without evaluating subsequent checks."""
    engine = _engine(kill_switch=True, symbol_cooldown=True, user_paused=True)
    decision = await engine.evaluate(_request())
    assert decision.passed is False
    assert list(decision.checks.keys()) == ["kill_switch"]


@pytest.mark.asyncio
async def test_engine_checks_dict_populated_on_pass():
    engine = _engine()
    decision = await engine.evaluate(_request())
    expected_checks = {
        "kill_switch", "user_paused", "symbol_paused", "circuit_breaker",
        "symbol_cooldown", "symbol_policy", "trading_mode",
        "missing_account_context", "max_position_size", "unsupported_order_type",
        "max_daily_loss", "max_concurrent_exposure",
    }
    assert expected_checks == set(decision.checks.keys())
    assert all(v is True for v in decision.checks.values())
