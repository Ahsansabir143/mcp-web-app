"""Track 1 tests — paper adapter price resolution and market snapshot freshness."""
from __future__ import annotations

import json
import time
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from shared.schemas.enums import ApprovalLevel, MarketType, OrderSide, OrderType, TradingMode
from shared.schemas.execution import ExecutionRequest
from shared.schemas.strategy import TradeIntent
from services.execution.adapter.paper import PaperExecutionAdapter


# ── Helpers ───────────────────────────────────────────────────────────────────


def _intent(
    symbol: str = "BTCUSDT",
    market_type: MarketType = MarketType.SPOT,
    side: OrderSide = OrderSide.BUY,
    size: Decimal = Decimal("0.001"),
    size_usd: Decimal | None = None,
    limit_price: Decimal | None = None,
) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        market_type=market_type,
        side=side,
        size=size,
        size_usd=size_usd,
        limit_price=limit_price,
        order_type=OrderType.LIMIT if limit_price else OrderType.MARKET,
    )


def _request(intent: TradeIntent) -> ExecutionRequest:
    return ExecutionRequest(
        trade_intent=intent,
        user_id="test-user",
        account_id="acct-test",
        trading_mode=TradingMode.PAPER,
        approval_level=ApprovalLevel.L2_PAPER,
    )


def _redis_with_price(price: float | None, bid: float | None = None, ask: float | None = None) -> AsyncMock:
    redis = AsyncMock()
    now_ms = int(time.time() * 1000)

    async def get(key: str):
        if ":price" in key and price is not None:
            return json.dumps({"price": price, "ts": now_ms})
        if ":book_ticker" in key and bid is not None and ask is not None:
            return json.dumps({
                "bid_price": bid, "bid_qty": 0.1,
                "ask_price": ask, "ask_qty": 0.1,
                "update_id": 1, "ts": now_ms,
            })
        return None

    redis.get = AsyncMock(side_effect=get)
    return redis


# ── Limit price resolution ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_limit_price_used_directly():
    intent = _intent(limit_price=Decimal("50000.00"))
    adapter = PaperExecutionAdapter(redis=None)
    resp = await adapter.submit(_request(intent), "coid-limit")
    assert resp.success is True
    assert resp.fill_price == Decimal("50000.00")


@pytest.mark.asyncio
async def test_limit_price_takes_priority_over_redis():
    redis = _redis_with_price(price=45000.0)
    intent = _intent(limit_price=Decimal("50000.00"))
    adapter = PaperExecutionAdapter(redis=redis)
    resp = await adapter.submit(_request(intent), "coid-limit-priority")
    assert resp.success is True
    assert resp.fill_price == Decimal("50000.00")


# ── Redis price resolution ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redis_last_price_used_when_no_limit():
    redis = _redis_with_price(price=62500.0)
    intent = _intent(size=Decimal("0.002"))
    adapter = PaperExecutionAdapter(redis=redis)
    resp = await adapter.submit(_request(intent), "coid-redis-price")
    assert resp.success is True
    assert resp.fill_price == Decimal("62500.00")


@pytest.mark.asyncio
async def test_redis_book_ticker_mid_fallback():
    redis = _redis_with_price(price=None, bid=60000.0, ask=60100.0)
    intent = _intent(size=Decimal("0.001"))
    adapter = PaperExecutionAdapter(redis=redis)
    resp = await adapter.submit(_request(intent), "coid-book-mid")
    assert resp.success is True
    expected_mid = (Decimal("60000.0") + Decimal("60100.0")) / 2
    assert resp.fill_price == expected_mid.quantize(Decimal("0.01"))


# ── size_usd derivation fallback ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_size_usd_derivation_when_no_redis():
    intent = _intent(size=Decimal("0.002"), size_usd=Decimal("100.00"))
    adapter = PaperExecutionAdapter(redis=None)
    resp = await adapter.submit(_request(intent), "coid-derived")
    assert resp.success is True
    expected = (Decimal("100.00") / Decimal("0.002")).quantize(Decimal("0.01"))
    assert resp.fill_price == expected


# ── Failure when no price available ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_failure_when_no_price_available():
    redis = _redis_with_price(price=None)
    intent = _intent(size=Decimal("0.001"))
    adapter = PaperExecutionAdapter(redis=redis)
    resp = await adapter.submit(_request(intent), "coid-noprice")
    assert resp.success is False
    assert "paper_price_unavailable" in (resp.error or "")


@pytest.mark.asyncio
async def test_failure_when_no_redis_and_no_size_usd():
    intent = _intent(size=Decimal("0.001"), size_usd=None)
    adapter = PaperExecutionAdapter(redis=None)
    resp = await adapter.submit(_request(intent), "coid-nodata")
    assert resp.success is False


# ── Commission sanity ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_commission_is_nonzero_on_success():
    intent = _intent(limit_price=Decimal("30000"))
    adapter = PaperExecutionAdapter(redis=None)
    resp = await adapter.submit(_request(intent), "coid-commission")
    assert resp.success is True
    assert resp.commission is not None
    assert resp.commission > Decimal("0")
    assert resp.commission_asset == "USDT"


# ── Exchange order ID format ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exchange_order_id_is_paper_prefixed():
    intent = _intent(limit_price=Decimal("30000"))
    adapter = PaperExecutionAdapter(redis=None)
    resp = await adapter.submit(_request(intent), "coid-id-check")
    assert resp.exchange_order_id is not None
    assert resp.exchange_order_id.startswith("PAPER-")


@pytest.mark.asyncio
async def test_exchange_order_id_is_deterministic():
    intent = _intent(limit_price=Decimal("30000"))
    adapter = PaperExecutionAdapter(redis=None)
    resp1 = await adapter.submit(_request(intent), "same-coid")
    resp2 = await adapter.submit(_request(intent), "same-coid")
    assert resp1.exchange_order_id == resp2.exchange_order_id


@pytest.mark.asyncio
async def test_exchange_order_ids_differ_per_client_order_id():
    intent = _intent(limit_price=Decimal("30000"))
    adapter = PaperExecutionAdapter(redis=None)
    resp1 = await adapter.submit(_request(intent), "coid-aaa")
    resp2 = await adapter.submit(_request(intent), "coid-bbb")
    assert resp1.exchange_order_id != resp2.exchange_order_id


# ── Market snapshot assembled fallback ───────────────────────────────────────


@pytest.mark.asyncio
async def test_get_symbol_snapshot_assembled_with_price():
    from services.mcp_server.facades.market import get_symbol_snapshot

    now_ms = int(time.time() * 1000)
    redis = AsyncMock()

    async def get(key: str):
        if "analytics" in key and "snapshot" in key:
            return None
        if ":price" in key:
            return json.dumps({"price": 65000.0, "ts": now_ms - 200})
        if ":book_ticker" in key:
            return json.dumps({
                "bid_price": 64990.0, "bid_qty": 0.5,
                "ask_price": 65010.0, "ask_qty": 0.5,
                "update_id": 99, "ts": now_ms - 100,
            })
        return None

    redis.get = AsyncMock(side_effect=get)

    snap = await get_symbol_snapshot(redis, "spot", "BTCUSDT")
    assert snap["last_price"] == "65000.0"
    assert snap["bid"] == "64990.0"
    assert snap["ask"] == "65010.0"
    assert "spread" in snap
    assert snap["price_age_ms"] >= 0
    assert snap["bid_ask_age_ms"] >= 0
    assert snap["source"] == "assembled"


@pytest.mark.asyncio
async def test_get_symbol_snapshot_no_data_returns_message():
    from services.mcp_server.facades.market import get_symbol_snapshot

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    snap = await get_symbol_snapshot(redis, "spot", "BTCUSDT")
    assert "message" in snap
    assert snap["source"] == "assembled"


@pytest.mark.asyncio
async def test_get_symbol_snapshot_analytics_path_injects_price():
    from services.mcp_server.facades.market import get_symbol_snapshot

    analytics_data = json.dumps({
        "market_state": {"price": 70000.0, "bid": 69990.0, "ask": 70010.0},
        "indicator_state": {},
    })
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=analytics_data)

    snap = await get_symbol_snapshot(redis, "futures", "BTCUSDT")
    assert snap["last_price"] == "70000.0"
    assert snap["bid"] == "69990.0"
    assert snap["ask"] == "70010.0"
    assert snap["source"] == "analytics_snapshot"
