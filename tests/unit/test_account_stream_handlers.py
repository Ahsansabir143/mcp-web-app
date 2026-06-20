"""Track B tests — AccountStateWriter event handling and persistence."""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.execution.account_stream.state import AccountStateWriter


def _make_writer(session_factory=None, redis=None):
    if session_factory is None:
        session_factory = MagicMock()
    if redis is None:
        redis = AsyncMock()
        redis.set = AsyncMock()
    account_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    return AccountStateWriter(session_factory, redis, account_id), account_id


# ── Balance upsert ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_balances_inserts_new_balance():
    from shared.db.models.account import Balance

    stored = []

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    mock_session.add = MagicMock(side_effect=lambda b: stored.append(b))
    mock_session.commit = AsyncMock()

    redis = AsyncMock()
    redis.set = AsyncMock()

    # for _cache_balances DB read
    mock_session.execute = AsyncMock(side_effect=[
        # first call: select existing balance → None
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        # second call: select all balances for cache → empty
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
    ])

    writer, _ = _make_writer(MagicMock(return_value=mock_session), redis)

    await writer.upsert_balances([{"a": "USDT", "f": "100.5", "l": "0.0"}], 1000)

    assert len(stored) == 1
    b = stored[0]
    assert b.asset == "USDT"
    assert b.free == "100.5"
    assert b.total == "100.5"


@pytest.mark.asyncio
async def test_upsert_balances_updates_existing_balance():
    from shared.db.models.account import Balance

    existing = MagicMock()
    existing.asset = "BTC"
    existing.free = "1.0"
    existing.locked = "0.0"
    existing.total = "1.0"
    existing.updated_at_ms = 0

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(side_effect=[
        MagicMock(scalar_one_or_none=MagicMock(return_value=existing)),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
    ])
    mock_session.commit = AsyncMock()

    writer, _ = _make_writer(MagicMock(return_value=mock_session), AsyncMock(set=AsyncMock()))
    await writer.upsert_balances([{"a": "BTC", "f": "2.5", "l": "0.1"}], 2000)

    assert existing.free == "2.5"
    assert existing.locked == "0.1"
    assert existing.total == "2.6"
    assert existing.updated_at_ms == 2000


@pytest.mark.asyncio
async def test_upsert_balances_skips_empty_asset():
    stored = []
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
    )
    mock_session.add = MagicMock(side_effect=lambda b: stored.append(b))
    mock_session.commit = AsyncMock()

    writer, _ = _make_writer(MagicMock(return_value=mock_session), AsyncMock(set=AsyncMock()))
    # Empty asset should be skipped
    await writer.upsert_balances([{"a": "", "f": "100", "l": "0"}], 1000)
    assert len(stored) == 0


# ── Order upsert from execution report ───────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_order_creates_new_order_on_execution_report():
    stored = []

    mock_order = MagicMock()
    mock_order.id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    # First execute: select existing order → None; second: select fill → None
    mock_session.execute = AsyncMock(side_effect=[
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
    ])

    def capture_add(obj):
        stored.append(obj)
        if hasattr(obj, "client_order_id"):
            obj.id = uuid.uuid4()

    mock_session.add = MagicMock(side_effect=capture_add)
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()

    writer, _ = _make_writer(MagicMock(return_value=mock_session))

    evt = {
        "e": "executionReport",
        "c": "coid-test-123",
        "i": 456789,
        "s": "BTCUSDT",
        "S": "BUY",
        "o": "MARKET",
        "X": "NEW",
        "q": "0.001",
        "z": "0.000",
        "p": "0",
        "L": "0",
        "x": "NEW",
        "t": -1,
        "O": 1000000,
        "T": 1000001,
        "f": "GTC",
    }
    await writer.upsert_order_from_execution_report(evt)

    orders = [o for o in stored if hasattr(o, "client_order_id")]
    assert len(orders) == 1
    assert orders[0].client_order_id == "coid-test-123"
    assert orders[0].symbol == "BTCUSDT"
    assert orders[0].status == "NEW"


@pytest.mark.asyncio
async def test_upsert_order_creates_fill_on_trade_execution():
    from shared.db.models.account import Order, Fill

    existing_order = MagicMock()
    existing_order.id = uuid.uuid4()
    existing_order.status = "NEW"
    existing_order.filled_qty = "0"
    existing_order.avg_fill_price = None

    stored_fills = []

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(side_effect=[
        # select existing order → found
        MagicMock(scalar_one_or_none=MagicMock(return_value=existing_order)),
        # select fill → not found (so we create one)
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
    ])
    mock_session.add = MagicMock(side_effect=lambda f: stored_fills.append(f))
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()

    writer, _ = _make_writer(MagicMock(return_value=mock_session))

    evt = {
        "e": "executionReport",
        "c": "coid-fill-999",
        "i": 123,
        "s": "BTCUSDT",
        "S": "BUY",
        "o": "MARKET",
        "X": "FILLED",
        "q": "0.001",
        "z": "0.001",
        "p": "0",
        "L": "65000.00",   # fill price
        "l": "0.001",      # fill qty
        "x": "TRADE",
        "t": 777,
        "n": "0.04",
        "N": "USDT",
        "m": False,
        "T": 1000002,
        "O": 1000000,
        "f": "GTC",
    }
    await writer.upsert_order_from_execution_report(evt)

    fills = [f for f in stored_fills if hasattr(f, "exchange_trade_id")]
    assert len(fills) == 1
    f = fills[0]
    assert f.exchange_trade_id == "777"
    assert f.price == "65000.00"
    assert f.qty == "0.001"
    assert f.commission == "0.04"
    assert f.commission_asset == "USDT"


# ── Stream status caching ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_balances_cached_to_redis_after_upsert():
    from shared.db.models.account import Balance as BalanceModel
    from decimal import Decimal

    mock_balance = MagicMock()
    mock_balance.asset = "USDT"
    mock_balance.free = Decimal("100")
    mock_balance.locked = Decimal("0")
    mock_balance.total = Decimal("100")
    mock_balance.updated_at_ms = 1000

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(side_effect=[
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # no existing
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_balance])))),
    ])
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    redis = AsyncMock()
    redis.set = AsyncMock()

    writer, _ = _make_writer(MagicMock(return_value=mock_session), redis)
    await writer.upsert_balances([{"a": "USDT", "f": "100", "l": "0"}], 1000)

    redis.set.assert_awaited_once()
    call_args = redis.set.call_args
    key = call_args[0][0]
    assert "live:balances" in key
    data = json.loads(call_args[0][1])
    assert len(data) == 1
    assert data[0]["asset"] == "USDT"
