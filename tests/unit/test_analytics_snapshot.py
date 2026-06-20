"""Tests for SnapshotBuilder and the AnalyticsDispatcher + StateStore integration."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.analytics.dispatcher import AnalyticsDispatcher
from services.analytics.engines.book_integrity import BookIntegrityState
from services.analytics.snapshot.builder import SnapshotBuilder, _d
from services.analytics.state import StateStore, SymbolState
from shared.schemas.analytics import UnifiedDecisionSnapshot
from shared.schemas.enums import EventType, MarketType, Venue
from shared.schemas.events import NormalizedEvent


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_event(event_type: EventType, symbol: str = "BTCUSDT",
                market_type: MarketType = MarketType.FUTURES,
                data: dict | None = None) -> NormalizedEvent:
    return NormalizedEvent(
        event_type=event_type,
        venue=Venue.BINANCE,
        market_type=market_type,
        symbol=symbol,
        timestamp_ms=1_000_000,
        received_ms=1_000_001,
        source_stream=f"btcusdt@trade",
        data=data or {},
    )


def _make_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    return redis


async def _build(state: SymbolState, redis=None) -> UnifiedDecisionSnapshot:
    builder = SnapshotBuilder()
    return await builder.build(state, redis or _make_redis(), "", now_ms=2_000_000)


# ── _d helper ─────────────────────────────────────────────────────────────────

def test_d_none():
    assert _d(None) is None


def test_d_converts_float():
    result = _d(100.5)
    assert isinstance(result, Decimal)
    assert result == Decimal("100.5")


# ── SymbolState + StateStore ──────────────────────────────────────────────────

def test_state_store_creates_on_first_access():
    store = StateStore()
    s = store.get("BTCUSDT", "futures")
    assert s.symbol == "BTCUSDT"
    assert s.market_type == "futures"


def test_state_store_returns_same_instance():
    store = StateStore()
    s1 = store.get("BTCUSDT", "futures")
    s2 = store.get("BTCUSDT", "futures")
    assert s1 is s2


def test_state_store_separate_per_market_type():
    store = StateStore()
    s_spot = store.get("BTCUSDT", "spot")
    s_fut = store.get("BTCUSDT", "futures")
    assert s_spot is not s_fut


def test_symbol_state_get_indicator_creates():
    s = SymbolState("BTCUSDT", "futures")
    e1 = s.get_indicator("1m")
    e2 = s.get_indicator("1m")
    assert e1 is e2
    assert "1m" in s.indicators


# ── Dispatcher routing ────────────────────────────────────────────────────────

def test_dispatcher_trade_updates_price():
    store = StateStore()
    d = AnalyticsDispatcher(store)
    event = _make_event(EventType.TRADE, data={"price": "50000", "qty": "0.1", "is_buyer_maker": False})
    d.update(event)
    state = store.get("BTCUSDT", "futures")
    assert state.last_price == 50000.0


def test_dispatcher_mark_price_updates_price():
    store = StateStore()
    d = AnalyticsDispatcher(store)
    event = _make_event(EventType.MARK_PRICE, data={
        "mark_price": "50100", "index_price": "50050", "funding_rate": "0.0001",
        "next_funding_time_ms": 9999999,
    })
    d.update(event)
    state = store.get("BTCUSDT", "futures")
    assert state.last_price == 50100.0
    assert state.last_mark is not None


def test_dispatcher_kline_only_closed():
    store = StateStore()
    d = AnalyticsDispatcher(store)
    # Open kline → should NOT update indicator
    event = _make_event(EventType.KLINE, data={
        "interval": "1m", "open": "50000", "high": "51000",
        "low": "49000", "close": "50500", "volume": "100", "is_closed": False,
    })
    d.update(event)
    state = store.get("BTCUSDT", "futures")
    assert "1m" not in state.indicators or state.indicators["1m"]._prev_close is None


def test_dispatcher_kline_closed_seeds_indicator():
    store = StateStore()
    d = AnalyticsDispatcher(store)
    for i in range(10):
        event = _make_event(EventType.KLINE, data={
            "interval": "1m", "open": str(100 + i), "high": str(102 + i),
            "low": str(99 + i), "close": str(101 + i), "volume": "10", "is_closed": True,
        })
        d.update(event)
    state = store.get("BTCUSDT", "futures")
    assert "1m" in state.indicators


def test_dispatcher_liquidation_recorded():
    store = StateStore()
    d = AnalyticsDispatcher(store)
    event = _make_event(EventType.LIQUIDATION, data={
        "side": "SELL", "orig_qty": "1.0", "price": "50000",
    })
    d.update(event)
    state = store.get("BTCUSDT", "futures")
    long_usd, _ = state.liquidations.compute_cluster_totals(50000.0, now_ms=1_010_000)
    assert long_usd > 0.0


def test_dispatcher_orderbook_integrity():
    store = StateStore()
    d = AnalyticsDispatcher(store)
    # Snapshot
    snap_event = _make_event(EventType.ORDERBOOK_SNAPSHOT, data={
        "last_update_id": 100, "bids": [["50000", "1"]], "asks": [["50001", "1"]]
    })
    book = {"bids": [["50000", "1"]], "asks": [["50001", "1"]], "last_update_id": 100}
    d.update(snap_event, current_book=book)
    state = store.get("BTCUSDT", "futures")
    assert state.integrity.is_valid

    # Valid delta
    delta_event = _make_event(EventType.ORDERBOOK_DELTA, data={
        "first_update_id": 101, "last_update_id": 110,
        "bids": [["49999", "2"]], "asks": [],
    })
    new_book = {"bids": [["50000", "1"], ["49999", "2"]], "asks": [["50001", "1"]], "last_update_id": 110}
    d.update(delta_event, current_book=new_book)
    assert state.integrity.is_valid
    assert state.last_book is new_book


def test_dispatcher_gap_invalidates_book():
    store = StateStore()
    d = AnalyticsDispatcher(store)
    snap_event = _make_event(EventType.ORDERBOOK_SNAPSHOT, data={"last_update_id": 100})
    d.update(snap_event)
    state = store.get("BTCUSDT", "futures")

    gap_event = _make_event(EventType.ORDERBOOK_DELTA, data={
        "first_update_id": 200, "last_update_id": 210,  # gap: expected 101, got 200
    })
    d.update(gap_event)
    assert not state.integrity.is_valid


# ── SnapshotBuilder ───────────────────────────────────────────────────────────

def test_snapshot_builder_empty_state():
    async def run():
        s = SymbolState("BTCUSDT", "futures")
        return await _build(s)

    snap = asyncio.get_event_loop().run_until_complete(run())
    assert isinstance(snap, UnifiedDecisionSnapshot)
    assert snap.meta.symbol == "BTCUSDT"
    assert snap.market_state.price is None


def test_snapshot_builder_with_price():
    async def run():
        s = SymbolState("BTCUSDT", "futures")
        s.last_price = 50000.0
        return await _build(s)

    snap = asyncio.get_event_loop().run_until_complete(run())
    assert snap.market_state.price == Decimal("50000.0")


def test_snapshot_builder_book_skipped_when_invalid():
    async def run():
        s = SymbolState("BTCUSDT", "futures")
        s.last_price = 50000.0
        s.last_book = {"bids": [["49999", "10"]], "asks": [["50001", "10"]]}
        # integrity.is_valid == False (no snapshot called)
        return await _build(s)

    snap = asyncio.get_event_loop().run_until_complete(run())
    # Book state should be empty (guardrail)
    assert snap.book_state.imbalance_ratio is None


def test_snapshot_builder_book_included_when_valid():
    async def run():
        s = SymbolState("BTCUSDT", "futures")
        s.last_price = 50000.0
        s.integrity.on_snapshot(100, 1000)
        s.last_book = {"bids": [["49999", "10"]], "asks": [["50001", "10"]]}
        return await _build(s)

    snap = asyncio.get_event_loop().run_until_complete(run())
    assert snap.book_state.imbalance_ratio is not None


def test_snapshot_builder_futures_state_empty_without_mark():
    async def run():
        s = SymbolState("BTCUSDT", "futures")
        return await _build(s)

    snap = asyncio.get_event_loop().run_until_complete(run())
    assert snap.futures_state.mark_price is None
    assert snap.futures_state.funding_pressure_score is None


def test_snapshot_builder_futures_state_with_mark():
    async def run():
        s = SymbolState("BTCUSDT", "futures")
        s.last_price = 50000.0
        s.last_mark = {
            "mark_price": "50100", "index_price": "50050",
            "funding_rate": "0.0001", "next_funding_time_ms": 9_999_999,
        }
        return await _build(s)

    snap = asyncio.get_event_loop().run_until_complete(run())
    assert snap.futures_state.mark_price == Decimal("50100.0")
    assert snap.futures_state.funding_rate == Decimal("0.0001")
    assert snap.futures_state.funding_pressure_score is not None


def test_snapshot_meta_staleness():
    async def run():
        s = SymbolState("BTCUSDT", "futures")
        s.event_timestamps["trade"] = 1_990_000   # 10s before now
        return await _build(s)

    snap = asyncio.get_event_loop().run_until_complete(run())
    assert snap.meta.staleness_ms.get("trade") == 10_000


def test_snapshot_account_empty_when_no_id():
    async def run():
        s = SymbolState("BTCUSDT", "futures")
        return await _build(s)

    snap = asyncio.get_event_loop().run_until_complete(run())
    assert snap.account_state.total_equity_usd is None
    assert snap.account_state.balances == []
