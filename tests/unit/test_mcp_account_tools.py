"""Track C tests — MCP account observability tools."""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── get_account_connection_status ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_account_connection_status_returns_account_info():
    from services.mcp_server.tools.account import get_account_connection_status

    account_id = str(uuid.uuid4())

    mock_acct = MagicMock()
    mock_acct.id = uuid.UUID(account_id)
    mock_acct.account_label = "main"
    mock_acct.venue = "binance"
    mock_acct.trading_mode = "paper"
    mock_acct.approval_level = "l2_paper"
    mock_acct.connection_status = "connected"
    mock_acct.last_connectivity_check_ms = 1000000
    mock_acct.stream_status = "connected"
    mock_acct.stream_last_event_ms = 999000
    mock_acct.stream_error = None

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_acct]))))
    )
    session_factory = MagicMock(return_value=mock_session)

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    result = await get_account_connection_status(
        {"account_id": account_id},
        redis=redis,
        session_factory=session_factory,
    )

    assert result["connection_status"] == "connected"
    assert result["stream_status"] == "connected"
    assert result["account_id"] == account_id


@pytest.mark.asyncio
async def test_get_account_connection_status_invalid_uuid():
    from services.mcp_server.facades.account import get_account_connection_status

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    result = await get_account_connection_status(MagicMock(), redis, "not-a-uuid")
    assert result.get("error") == "invalid_account_id"


# ── get_account_balances ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_account_balances_reads_redis_cache_first():
    from services.mcp_server.tools.account import get_account_balances

    account_id = str(uuid.uuid4())
    cached = [{"asset": "USDT", "free": "500", "locked": "0", "total": "500", "updated_at_ms": 1000}]

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps(cached))

    result = await get_account_balances(
        {"account_id": account_id},
        redis=redis,
        session_factory=MagicMock(),
    )

    assert result["source"] == "redis_cache"
    assert result["count"] == 1
    assert result["balances"][0]["asset"] == "USDT"


@pytest.mark.asyncio
async def test_get_account_balances_falls_back_to_db():
    from services.mcp_server.tools.account import get_account_balances

    account_id = str(uuid.uuid4())

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    mock_bal = MagicMock()
    mock_bal.asset = "BTC"
    mock_bal.free = Decimal("0.1")
    mock_bal.locked = Decimal("0")
    mock_bal.total = Decimal("0.1")
    mock_bal.updated_at_ms = 2000

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_bal]))))
    )
    session_factory = MagicMock(return_value=mock_session)

    result = await get_account_balances(
        {"account_id": account_id},
        redis=redis,
        session_factory=session_factory,
    )

    assert result["source"] == "database"
    assert result["count"] == 1
    assert result["balances"][0]["asset"] == "BTC"


@pytest.mark.asyncio
async def test_get_account_balances_min_total_filter():
    from services.mcp_server.tools.account import get_account_balances

    account_id = str(uuid.uuid4())
    cached = [
        {"asset": "USDT", "total": "500", "free": "500", "locked": "0", "updated_at_ms": 1000},
        {"asset": "XRP", "total": "0.001", "free": "0.001", "locked": "0", "updated_at_ms": 1000},
    ]
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps(cached))

    result = await get_account_balances(
        {"account_id": account_id, "min_total": 1.0},
        redis=redis,
        session_factory=MagicMock(),
    )

    assert result["count"] == 1
    assert result["balances"][0]["asset"] == "USDT"


# ── get_open_orders ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_open_orders_returns_new_and_partially_filled():
    from services.mcp_server.tools.account import get_open_orders

    account_id = str(uuid.uuid4())

    mock_order = MagicMock()
    mock_order.client_order_id = "coid-open"
    mock_order.exchange_order_id = "EX-001"
    mock_order.symbol = "BTCUSDT"
    mock_order.market_type = "spot"
    mock_order.side = "BUY"
    mock_order.order_type = "LIMIT"
    mock_order.status = "NEW"
    mock_order.quantity = Decimal("0.001")
    mock_order.filled_qty = Decimal("0")
    mock_order.price = Decimal("60000")
    mock_order.avg_fill_price = None
    mock_order.time_in_force = "GTC"
    mock_order.created_at_ms = 1000
    mock_order.updated_at_ms = 1001

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_order]))))
    )
    session_factory = MagicMock(return_value=mock_session)

    result = await get_open_orders(
        {"account_id": account_id},
        redis=AsyncMock(),
        session_factory=session_factory,
    )

    assert result["count"] == 1
    assert result["orders"][0]["status"] == "NEW"
    assert result["orders"][0]["symbol"] == "BTCUSDT"


# ── check_live_trade_policy ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_live_trade_policy_blocked_when_disabled():
    from services.mcp_server.tools.account import check_live_trade_policy

    result = await check_live_trade_policy(
        {"symbol": "BTCUSDT", "account_id": "acct-1", "notional_usd": 50.0},
        redis=AsyncMock(),
        session_factory=MagicMock(),
    )

    assert result["policy_would_allow"] is False
    assert result["live_trading_enabled"] is False
    assert len(result["blocked_reasons"]) > 0
    assert "note" in result


@pytest.mark.asyncio
async def test_check_live_trade_policy_missing_symbol():
    from services.mcp_server.tools.account import check_live_trade_policy

    result = await check_live_trade_policy(
        {},
        redis=AsyncMock(),
        session_factory=MagicMock(),
    )

    assert result.get("error") == "missing_argument"


@pytest.mark.asyncio
async def test_get_recent_fills_returns_fill_list():
    from services.mcp_server.tools.account import get_recent_fills

    account_id = str(uuid.uuid4())

    mock_fill = MagicMock()
    mock_fill.exchange_trade_id = "TRD-001"
    mock_fill.symbol = "BTCUSDT"
    mock_fill.side = "BUY"
    mock_fill.price = Decimal("65000")
    mock_fill.qty = Decimal("0.001")
    mock_fill.quote_qty = Decimal("65")
    mock_fill.commission = Decimal("0.04")
    mock_fill.commission_asset = "USDT"
    mock_fill.realized_pnl = None
    mock_fill.is_maker = False
    mock_fill.timestamp_ms = 2000000

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_fill]))))
    )
    session_factory = MagicMock(return_value=mock_session)

    result = await get_recent_fills(
        {"account_id": account_id},
        redis=AsyncMock(),
        session_factory=session_factory,
    )

    assert result["count"] == 1
    f = result["fills"][0]
    assert f["symbol"] == "BTCUSDT"
    assert f["price"] == "65000"
    assert f["commission_asset"] == "USDT"
