"""Track 1+2 tests — canonical price source alignment and incident logging."""
from __future__ import annotations

import json
import time
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.redis.keys import RedisKeys
from shared.schemas.enums import ApprovalLevel, MarketType, OrderSide, OrderType, TradingMode
from shared.schemas.execution import ExecutionRequest
from shared.schemas.strategy import TradeIntent
from services.execution.adapter.paper import PaperExecutionAdapter


# ── Helpers ───────────────────────────────────────────────────────────────────


def _intent(
    symbol: str = "BTCUSDT",
    market_type: MarketType = MarketType.SPOT,
    size: Decimal = Decimal("0.001"),
    size_usd: Decimal | None = None,
) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        market_type=market_type,
        side=OrderSide.BUY,
        size=size,
        size_usd=size_usd,
        order_type=OrderType.MARKET,
    )


def _request(intent: TradeIntent) -> ExecutionRequest:
    return ExecutionRequest(
        trade_intent=intent,
        user_id="test-user",
        account_id="acct-test",
        trading_mode=TradingMode.PAPER,
        approval_level=ApprovalLevel.L2_PAPER,
    )


NOW_MS = int(time.time() * 1000)


def _analytics_payload(price: float) -> str:
    return json.dumps({
        "market_state": {"price": price, "bid": price - 5, "ask": price + 5},
        "indicator_state": {},
    })


def _price_payload(price: float) -> str:
    return json.dumps({"price": price, "ts": NOW_MS})


def _book_payload(bid: float, ask: float) -> str:
    return json.dumps({
        "bid_price": bid,
        "bid_qty": 0.5,
        "ask_price": ask,
        "ask_qty": 0.5,
        "update_id": 1,
        "ts": NOW_MS,
    })


# ── Track 1: canonical price source alignment ─────────────────────────────────


@pytest.mark.asyncio
async def test_adapter_reads_analytics_snapshot_when_raw_price_absent():
    """If market_price key is absent but analytics snapshot exists, adapter fills."""
    market_type = MarketType.SPOT
    symbol = "BTCUSDT"
    analytics_key = RedisKeys.analytics_snapshot(market_type.value, symbol)
    price_key = RedisKeys.market_price(market_type.value, symbol)
    book_key = RedisKeys.market_book_ticker(market_type.value, symbol)

    redis = AsyncMock()

    async def get(key: str):
        if key == analytics_key:
            return _analytics_payload(70000.0)
        return None  # price and book_ticker absent

    redis.get = AsyncMock(side_effect=get)

    adapter = PaperExecutionAdapter(redis=redis)
    resp = await adapter.submit(_request(_intent()), "coid-analytics-fallback")

    assert resp.success is True
    assert resp.fill_price == Decimal("70000.00")


@pytest.mark.asyncio
async def test_adapter_analytics_snapshot_beats_raw_price():
    """analytics snapshot (priority 1) wins over market_price key (priority 2)."""
    market_type = MarketType.SPOT
    symbol = "BTCUSDT"
    analytics_key = RedisKeys.analytics_snapshot(market_type.value, symbol)
    price_key = RedisKeys.market_price(market_type.value, symbol)

    redis = AsyncMock()

    async def get(key: str):
        if key == analytics_key:
            return _analytics_payload(70000.0)
        if key == price_key:
            return _price_payload(65000.0)
        return None

    redis.get = AsyncMock(side_effect=get)

    adapter = PaperExecutionAdapter(redis=redis)
    resp = await adapter.submit(_request(_intent()), "coid-analytics-wins")

    assert resp.success is True
    assert resp.fill_price == Decimal("70000.00")  # analytics snapshot first


@pytest.mark.asyncio
async def test_adapter_falls_back_to_book_ticker_when_analytics_absent():
    """When analytics snapshot absent, book_ticker mid is used."""
    market_type = MarketType.SPOT
    symbol = "BTCUSDT"
    book_key = RedisKeys.market_book_ticker(market_type.value, symbol)

    redis = AsyncMock()

    async def get(key: str):
        if key == book_key:
            return _book_payload(59900.0, 60100.0)
        return None  # analytics and market_price both absent

    redis.get = AsyncMock(side_effect=get)

    adapter = PaperExecutionAdapter(redis=redis)
    resp = await adapter.submit(_request(_intent()), "coid-book-fallback")

    assert resp.success is True
    assert resp.fill_price == Decimal("60000.00")  # (59900 + 60100) / 2


@pytest.mark.asyncio
async def test_snapshot_and_adapter_see_same_price_from_analytics_key():
    """Writing analytics snapshot → both get_symbol_snapshot AND adapter return same price."""
    from services.mcp_server.facades.market import get_symbol_snapshot

    market_type = "spot"
    symbol = "BTCUSDT"
    analytics_key = RedisKeys.analytics_snapshot(market_type, symbol)

    redis = AsyncMock()

    async def get(key: str):
        if key == analytics_key:
            return _analytics_payload(72000.0)
        return None

    redis.get = AsyncMock(side_effect=get)

    # MCP snapshot
    snap = await get_symbol_snapshot(redis, market_type, symbol)
    assert snap["last_price"] == "72000.0"
    assert snap["source"] == "analytics_snapshot"

    # Paper adapter
    adapter = PaperExecutionAdapter(redis=redis)
    resp = await adapter.submit(
        _request(_intent(market_type=MarketType.SPOT)), "coid-canonical"
    )
    assert resp.success is True
    assert resp.fill_price == Decimal("72000.00")


@pytest.mark.asyncio
async def test_analytics_snapshot_zero_price_is_ignored():
    """Analytics snapshot with price=0 must NOT be used (same as raw price=0 rule)."""
    market_type = MarketType.SPOT
    symbol = "BTCUSDT"
    analytics_key = RedisKeys.analytics_snapshot(market_type.value, symbol)

    redis = AsyncMock()

    async def get(key: str):
        if key == analytics_key:
            return json.dumps({"market_state": {"price": 0}, "indicator_state": {}})
        return None

    redis.get = AsyncMock(side_effect=get)

    adapter = PaperExecutionAdapter(redis=redis)
    resp = await adapter.submit(_request(_intent()), "coid-zero-analytics")
    assert resp.success is False
    assert "paper_price_unavailable" in (resp.error or "")


# ── Track 2: incident logging via request_paper_trade ─────────────────────────


@pytest.mark.asyncio
async def test_request_paper_trade_logs_incident_when_all_prices_absent():
    """When all three price sources are absent, an incident must be persisted."""
    from services.mcp_server.facades.execution import request_paper_trade
    from shared.db.models.audit import IncidentLog

    strategy_id = str(uuid.uuid4())
    strategy_mock = MagicMock()
    strategy_mock.state = "paper_active"
    strategy_mock.market_type = "spot"
    strategy_mock.current_version = 1

    # All Redis gets return None
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    saved_incidents = []

    # Mock session_factory
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=strategy_mock)
    mock_session.add = MagicMock(side_effect=lambda obj: saved_incidents.append(obj))
    mock_session.commit = AsyncMock()

    session_factory = MagicMock(return_value=mock_session)

    result = await request_paper_trade(
        session_factory=session_factory,
        redis=redis,
        strategy_id=strategy_id,
        symbol="BTCUSDT",
        side="BUY",
        size_usd=100.0,
    )

    assert result.get("error") == "price_unavailable"
    assert len(saved_incidents) == 1
    incident = saved_incidents[0]
    assert incident.incident_type == "paper_price_unavailable"
    assert incident.context["symbol"] == "BTCUSDT"
    assert "analytics_snapshot" in incident.context["sources_tried"]


@pytest.mark.asyncio
async def test_request_paper_trade_no_incident_when_size_provided():
    """When explicit size (not size_usd) is given, no price lookup → no incident."""
    from services.mcp_server.facades.execution import request_paper_trade

    strategy_id = str(uuid.uuid4())
    strategy_mock = MagicMock()
    strategy_mock.state = "paper_active"
    strategy_mock.market_type = "spot"
    strategy_mock.current_version = 1

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    saved_incidents = []

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=strategy_mock)
    mock_session.add = MagicMock(side_effect=lambda obj: saved_incidents.append(obj))
    mock_session.commit = AsyncMock()

    stream_calls = []
    session_factory = MagicMock(return_value=mock_session)

    with patch("services.mcp_server.facades.execution.stream_publish", new_callable=AsyncMock) as mock_publish:
        result = await request_paper_trade(
            session_factory=session_factory,
            redis=redis,
            strategy_id=strategy_id,
            symbol="BTCUSDT",
            side="BUY",
            size=0.001,
        )

    assert result.get("status") == "queued"
    assert len(saved_incidents) == 0


@pytest.mark.asyncio
async def test_request_paper_trade_uses_analytics_snapshot_fallback():
    """When market_price and book_ticker absent but analytics snapshot present, no error."""
    from services.mcp_server.facades.execution import request_paper_trade

    strategy_id = str(uuid.uuid4())
    strategy_mock = MagicMock()
    strategy_mock.state = "paper_active"
    strategy_mock.market_type = "spot"
    strategy_mock.current_version = 1

    analytics_key = RedisKeys.analytics_snapshot("spot", "BTCUSDT")

    async def get(key: str):
        if key == analytics_key:
            return _analytics_payload(68000.0)
        return None

    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=get)

    saved_incidents = []

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=strategy_mock)
    mock_session.add = MagicMock(side_effect=lambda obj: saved_incidents.append(obj))
    mock_session.commit = AsyncMock()

    session_factory = MagicMock(return_value=mock_session)

    with patch("services.mcp_server.facades.execution.stream_publish", new_callable=AsyncMock):
        result = await request_paper_trade(
            session_factory=session_factory,
            redis=redis,
            strategy_id=strategy_id,
            symbol="BTCUSDT",
            side="BUY",
            size_usd=100.0,
        )

    assert result.get("status") == "queued"
    assert len(saved_incidents) == 0  # no incident — price was found


# ── Track 2: incident logging via ExecutionConsumer ───────────────────────────


@pytest.mark.asyncio
async def test_consumer_logs_incident_on_paper_price_unavailable():
    """Consumer logs incident when adapter returns paper_price_unavailable."""
    from services.execution.consumer import ExecutionConsumer
    from services.execution.config import ExecutionSettings

    intent = TradeIntent(
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        side=OrderSide.BUY,
        size=Decimal("0.001"),
        order_type=OrderType.MARKET,
    )

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)  # lock acquired
    redis.get = AsyncMock(return_value=None)
    redis.xack = AsyncMock()

    # Adapter that always returns price_unavailable
    adapter = AsyncMock()
    from services.execution.adapter.base import AdapterResponse
    adapter.submit = AsyncMock(return_value=AdapterResponse(
        success=False,
        client_order_id="coid-test",
        exchange_order_id=None,
        error="paper_price_unavailable: no price for BTCUSDT/spot",
    ))
    adapter.adapter_name = MagicMock(return_value="paper")

    # Risk engine always passes
    risk_engine = AsyncMock()
    from shared.schemas.execution import RiskDecision
    risk_engine.evaluate = AsyncMock(return_value=RiskDecision(passed=True, checks={}, failures=[]))

    # Incident logger spy
    incident_logger = AsyncMock()
    incident_logger.log_incident = AsyncMock()

    repo = AsyncMock()
    repo.create_job = AsyncMock()
    repo.update_job_status = AsyncMock()
    repo.get_daily_realized_loss_usd = AsyncMock(return_value=Decimal("0"))
    repo.get_open_positions_count = AsyncMock(return_value=0)

    settings = ExecutionSettings(default_account_id="acct-1", default_user_id="u1")

    consumer = ExecutionConsumer(
        settings=settings,
        redis=redis,
        repository=repo,
        adapter=adapter,
        risk_engine=risk_engine,
        incident_logger=incident_logger,
    )

    with patch("services.execution.consumer.ExecutionEventPublisher") as mock_pub_cls:
        mock_pub = AsyncMock()
        mock_pub.publish = AsyncMock()
        mock_pub_cls.return_value = mock_pub
        consumer._publisher = mock_pub

        await consumer._process("msg-1", {"intent": intent.model_dump_json()})

    incident_logger.log_incident.assert_awaited_once()
    call_kwargs = incident_logger.log_incident.call_args
    assert call_kwargs.kwargs.get("incident_type") == "paper_price_unavailable"
    assert call_kwargs.kwargs["context"]["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_consumer_logs_execution_failure_incident_for_non_price_errors():
    """Any adapter failure (not just price_unavailable) logs an execution_failure incident."""
    from services.execution.consumer import ExecutionConsumer
    from services.execution.config import ExecutionSettings
    from services.execution.adapter.base import AdapterResponse

    intent = TradeIntent(
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        side=OrderSide.BUY,
        size=Decimal("0.001"),
        order_type=OrderType.MARKET,
    )

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    redis.get = AsyncMock(return_value=None)
    redis.xack = AsyncMock()

    adapter = AsyncMock()
    adapter.submit = AsyncMock(return_value=AdapterResponse(
        success=False,
        client_order_id="coid-x",
        exchange_order_id=None,
        error="some_other_error",
    ))
    adapter.adapter_name = MagicMock(return_value="paper")

    from shared.schemas.execution import RiskDecision
    risk_engine = AsyncMock()
    risk_engine.evaluate = AsyncMock(return_value=RiskDecision(passed=True, checks={}, failures=[]))

    incident_logger = AsyncMock()
    incident_logger.log_incident = AsyncMock()

    repo = AsyncMock()
    repo.create_job = AsyncMock()
    repo.update_job_status = AsyncMock()
    repo.get_daily_realized_loss_usd = AsyncMock(return_value=Decimal("0"))
    repo.get_open_positions_count = AsyncMock(return_value=0)

    settings = ExecutionSettings(default_account_id="acct-1", default_user_id="u1")

    consumer = ExecutionConsumer(
        settings=settings,
        redis=redis,
        repository=repo,
        adapter=adapter,
        risk_engine=risk_engine,
        incident_logger=incident_logger,
    )

    mock_pub = AsyncMock()
    mock_pub.publish = AsyncMock()
    consumer._publisher = mock_pub

    await consumer._process("msg-1", {"intent": intent.model_dump_json()})

    incident_logger.log_incident.assert_awaited_once()
    call_kwargs = incident_logger.log_incident.call_args
    assert call_kwargs.kwargs.get("incident_type") == "execution_failure"
    assert call_kwargs.kwargs["context"]["symbol"] == "BTCUSDT"


# ── Cross-market fallback (spot ↔ futures) ────────────────────────────────────


@pytest.mark.asyncio
async def test_paper_adapter_falls_back_to_futures_when_spot_empty():
    """Spot intent with empty spot Redis keys resolves price from futures keys."""
    from shared.redis.keys import RedisKeys as RK

    spot_analytics = RK.analytics_snapshot("spot", "BTCUSDT")
    spot_price = RK.market_price("spot", "BTCUSDT")
    spot_book = RK.market_book_ticker("spot", "BTCUSDT")
    futures_analytics = RK.analytics_snapshot("futures", "BTCUSDT")

    redis = AsyncMock()

    async def get(key: str):
        if key == futures_analytics:
            return _analytics_payload(63500.0)
        # All spot keys return None
        return None

    redis.get = AsyncMock(side_effect=get)

    intent = _intent(market_type=MarketType.SPOT, size=Decimal("0.001"))
    adapter = PaperExecutionAdapter(redis=redis)
    resp = await adapter.submit(_request(intent), "coid-spot-futures-fallback")

    assert resp.success is True
    assert resp.fill_price == Decimal("63500.00")


@pytest.mark.asyncio
async def test_paper_adapter_uses_spot_price_before_futures_fallback():
    """Spot intent uses spot keys when available; futures fallback is NOT used."""
    from shared.redis.keys import RedisKeys as RK

    spot_analytics = RK.analytics_snapshot("spot", "BTCUSDT")
    futures_analytics = RK.analytics_snapshot("futures", "BTCUSDT")

    redis = AsyncMock()

    async def get(key: str):
        if key == spot_analytics:
            return _analytics_payload(62000.0)
        if key == futures_analytics:
            return _analytics_payload(63500.0)
        return None

    redis.get = AsyncMock(side_effect=get)

    intent = _intent(market_type=MarketType.SPOT, size=Decimal("0.001"))
    adapter = PaperExecutionAdapter(redis=redis)
    resp = await adapter.submit(_request(intent), "coid-spot-direct")

    assert resp.success is True
    assert resp.fill_price == Decimal("62000.00")  # spot wins, not futures fallback


@pytest.mark.asyncio
async def test_request_paper_trade_uses_futures_fallback_when_spot_empty():
    """size_usd paper trade for a spot strategy falls back to futures analytics for price."""
    from services.mcp_server.facades.execution import request_paper_trade
    from shared.redis.keys import RedisKeys as RK
    from unittest.mock import patch

    strategy_id = str(uuid.uuid4())
    strategy_mock = MagicMock()
    strategy_mock.state = "paper_active"
    strategy_mock.market_type = "spot"
    strategy_mock.current_version = 1

    futures_analytics = RK.analytics_snapshot("futures", "BTCUSDT")

    async def get(key: str):
        if key == futures_analytics:
            return _analytics_payload(64000.0)
        return None  # all spot keys absent

    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=get)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=strategy_mock)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    session_factory = MagicMock(return_value=mock_session)

    with patch("services.mcp_server.facades.execution.stream_publish", new_callable=AsyncMock):
        result = await request_paper_trade(
            session_factory=session_factory,
            redis=redis,
            strategy_id=strategy_id,
            symbol="BTCUSDT",
            side="BUY",
            size_usd=64.0,  # should derive size = 64 / 64000 = 0.001
        )

    assert result.get("status") == "queued"
    assert result.get("size") == "0.00100"  # 64 / 64000 = 0.001
