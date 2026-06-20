"""Track 3 tests — account context loading, live risk data, limits_override."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.risk.limits import RiskLimits
from shared.schemas.enums import ApprovalLevel, MarketType, OrderSide, OrderType, TradingMode
from shared.schemas.execution import ExecutionRequest
from shared.schemas.strategy import TradeIntent
from services.execution.risk.checks import (
    check_max_concurrent_exposure_placeholder,
    check_max_daily_loss_placeholder,
)
from services.execution.risk.engine import ExecutionRiskEngine


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _intent(
    symbol: str = "BTCUSDT",
    size_usd: Decimal = Decimal("100"),
) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        market_type=MarketType.SPOT,
        side=OrderSide.BUY,
        size=Decimal("0.001"),
        size_usd=size_usd,
        order_type=OrderType.MARKET,
    )


def _request(intent: TradeIntent | None = None) -> ExecutionRequest:
    return ExecutionRequest(
        trade_intent=intent or _intent(),
        user_id="u1",
        account_id="acct-1",
        trading_mode=TradingMode.PAPER,
        approval_level=ApprovalLevel.L2_PAPER,
    )


def _mock_redis(
    kill_switch: bool = False,
    user_paused: bool = False,
    symbol_paused: bool = False,
    circuit_breaker: bool = False,
    symbol_cooldown: bool = False,
) -> AsyncMock:
    redis = AsyncMock()

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
    redis.ttl = AsyncMock(return_value=0)
    return redis


def _engine(redis=None, limits=None, paper_only=True) -> ExecutionRiskEngine:
    return ExecutionRiskEngine(
        redis=redis or _mock_redis(),
        limits=limits or RiskLimits(),
        paper_only=paper_only,
    )


# ── check_max_daily_loss_placeholder with real data ───────────────────────────


def test_daily_loss_check_passes_below_limit():
    r = check_max_daily_loss_placeholder(Decimal("100"), Decimal("500"))
    assert r.passed is True
    assert r.name == "max_daily_loss"


def test_daily_loss_check_fails_at_limit():
    r = check_max_daily_loss_placeholder(Decimal("500"), Decimal("500"))
    assert r.passed is False


def test_daily_loss_check_fails_above_limit():
    r = check_max_daily_loss_placeholder(Decimal("600"), Decimal("500"))
    assert r.passed is False
    assert "daily_loss" in r.reason


def test_daily_loss_check_passes_when_none():
    r = check_max_daily_loss_placeholder(None, Decimal("500"))
    assert r.passed is True
    assert r.reason == "no_daily_loss_data"


def test_daily_loss_check_zero_loss_passes():
    r = check_max_daily_loss_placeholder(Decimal("0"), Decimal("500"))
    assert r.passed is True


# ── check_max_concurrent_exposure_placeholder with real data ──────────────────


def test_concurrent_exposure_check_passes_below_limit():
    r = check_max_concurrent_exposure_placeholder(2, 3)
    assert r.passed is True


def test_concurrent_exposure_check_fails_at_limit():
    r = check_max_concurrent_exposure_placeholder(3, 3)
    assert r.passed is False


def test_concurrent_exposure_check_fails_above_limit():
    r = check_max_concurrent_exposure_placeholder(5, 3)
    assert r.passed is False
    assert "open_positions" in r.reason


def test_concurrent_exposure_check_passes_when_none():
    r = check_max_concurrent_exposure_placeholder(None, 3)
    assert r.passed is True
    assert r.reason == "no_position_data"


def test_concurrent_exposure_check_zero_positions_passes():
    r = check_max_concurrent_exposure_placeholder(0, 3)
    assert r.passed is True


# ── ExecutionRiskEngine.evaluate with limits_override ────────────────────────


@pytest.mark.asyncio
async def test_engine_uses_limits_override_not_default():
    default_limits = RiskLimits(max_position_size_usd=Decimal("50"))
    override_limits = RiskLimits(max_position_size_usd=Decimal("500"))

    engine = _engine(limits=default_limits)
    req = _request(intent=_intent(size_usd=Decimal("100")))

    # Without override: 100 > 50 → fails
    decision_no_override = await engine.evaluate(req)
    assert decision_no_override.passed is False
    assert decision_no_override.checks.get("max_position_size") is False

    # With override: 100 ≤ 500 → passes
    decision_with_override = await engine.evaluate(req, limits_override=override_limits)
    assert decision_with_override.passed is True
    assert decision_with_override.checks.get("max_position_size") is True


@pytest.mark.asyncio
async def test_engine_uses_override_for_daily_loss():
    tight_limits = RiskLimits(max_daily_loss_usd=Decimal("10"))
    loose_limits = RiskLimits(max_daily_loss_usd=Decimal("1000"))

    engine = _engine(limits=tight_limits)
    req = _request()

    decision_tight = await engine.evaluate(req, daily_loss_usd=Decimal("50"))
    assert decision_tight.passed is False

    decision_loose = await engine.evaluate(
        req, daily_loss_usd=Decimal("50"), limits_override=loose_limits
    )
    assert decision_loose.passed is True


@pytest.mark.asyncio
async def test_engine_uses_override_for_concurrent_positions():
    tight_limits = RiskLimits(max_concurrent_positions=1)
    loose_limits = RiskLimits(max_concurrent_positions=10)

    engine = _engine(limits=tight_limits)
    req = _request()

    decision_tight = await engine.evaluate(req, open_positions=2)
    assert decision_tight.passed is False

    decision_loose = await engine.evaluate(req, open_positions=2, limits_override=loose_limits)
    assert decision_loose.passed is True


@pytest.mark.asyncio
async def test_engine_evaluate_with_all_real_data():
    limits = RiskLimits(
        max_position_size_usd=Decimal("1000"),
        max_daily_loss_usd=Decimal("500"),
        max_concurrent_positions=5,
    )
    engine = _engine(limits=limits)
    req = _request(intent=_intent(size_usd=Decimal("100")))

    decision = await engine.evaluate(
        req,
        daily_loss_usd=Decimal("50"),
        open_positions=2,
    )
    assert decision.passed is True
    assert decision.checks.get("max_daily_loss") is True
    assert decision.checks.get("max_concurrent_exposure") is True


@pytest.mark.asyncio
async def test_engine_evaluate_daily_loss_blocks():
    limits = RiskLimits(max_daily_loss_usd=Decimal("100"))
    engine = _engine(limits=limits)
    req = _request()

    decision = await engine.evaluate(req, daily_loss_usd=Decimal("150"))
    assert decision.passed is False
    assert decision.checks.get("max_daily_loss") is False


@pytest.mark.asyncio
async def test_engine_evaluate_concurrent_positions_blocks():
    limits = RiskLimits(max_concurrent_positions=2)
    engine = _engine(limits=limits)
    req = _request()

    decision = await engine.evaluate(req, open_positions=3)
    assert decision.passed is False
    assert decision.checks.get("max_concurrent_exposure") is False


# ── ExecutionConsumer wires context loader and risk data ──────────────────────


@pytest.mark.asyncio
async def test_consumer_calls_risk_engine_with_live_data(monkeypatch):
    """Verify the consumer fetches daily_loss + open_positions and passes them through."""
    from services.execution.consumer import ExecutionConsumer
    from services.execution.account.context import AccountContext
    from shared.redis.keys import RedisKeys
    import json

    # Minimal trade intent published on stream
    from shared.schemas.strategy import TradeIntent as TI
    intent = TI(
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        side=OrderSide.BUY,
        size=Decimal("0.001"),
        order_type=OrderType.MARKET,
    )
    intent_json = intent.model_dump_json()

    # Single-message stream response followed by empty
    call_count = 0

    async def _stream_read(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [("stream", [("msg-1", {"intent": intent_json})])]
        return None

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.xack = AsyncMock()

    risk_engine = AsyncMock()
    from shared.schemas.execution import RiskDecision
    risk_engine.evaluate = AsyncMock(return_value=RiskDecision(
        passed=False,
        checks={},
        failures=["daily_loss_exceeded"],
    ))

    # Mock repository with risk data
    repo = AsyncMock()
    repo.create_job = AsyncMock()
    repo.get_daily_realized_loss_usd = AsyncMock(return_value=Decimal("250"))
    repo.get_open_positions_count = AsyncMock(return_value=3)

    # Mock context loader with per-account limits
    ctx_limits = RiskLimits(max_daily_loss_usd=Decimal("300"))
    mock_ctx = AccountContext(
        account_id="acct-1",
        user_id="u1",
        trading_mode=TradingMode.PAPER,
        approval_level=ApprovalLevel.L2_PAPER,
        has_credentials=False,
        paper_only=True,
        limits=ctx_limits,
    )
    ctx_loader = AsyncMock()
    ctx_loader.load = AsyncMock(return_value=mock_ctx)

    from services.execution.config import ExecutionSettings

    settings = ExecutionSettings(
        default_account_id="acct-1",
        default_user_id="u1",
    )

    consumer = ExecutionConsumer(
        settings=settings,
        redis=mock_redis,
        repository=repo,
        risk_engine=risk_engine,
        context_loader=ctx_loader,
    )

    with patch(
        "services.execution.consumer.stream_read_group",
        side_effect=_stream_read,
    ):
        await consumer._process("msg-1", {"intent": intent_json})

    # Verify context was loaded
    ctx_loader.load.assert_awaited_once()

    # Verify repo methods called
    repo.get_daily_realized_loss_usd.assert_awaited_once()
    repo.get_open_positions_count.assert_awaited_once()

    # Verify risk engine received the live data and limits override
    risk_engine.evaluate.assert_awaited_once()
    call_kwargs = risk_engine.evaluate.call_args
    assert call_kwargs.kwargs.get("daily_loss_usd") == Decimal("250")
    assert call_kwargs.kwargs.get("open_positions") == 3
    assert call_kwargs.kwargs.get("limits_override") == ctx_limits
