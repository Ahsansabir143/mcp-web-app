"""Tests for bid/ask/spread population in SnapshotBuilder._build_market_state.

Covers:
  - Populated from last_book_ticker when present (original path)
  - Populated from top-of-book orderbook when last_book_ticker is absent (new path)
  - Null when neither source is available
  - spread_bps sanity for both sources
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from services.analytics.snapshot.builder import SnapshotBuilder
from services.analytics.state import SymbolState


def _make_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    return redis


async def _build(state: SymbolState):
    builder = SnapshotBuilder()
    return await builder.build(state, _make_redis(), "", now_ms=2_000_000)


# ── bid/ask from book_ticker (original path) ──────────────────────────────────


def test_bid_ask_populated_from_book_ticker():
    """When last_book_ticker is set, bid/ask/spread come from it."""
    async def run():
        s = SymbolState("BTCUSDT", "futures")
        s.last_price = 63000.0
        s.last_book_ticker = {
            "bid_price": "62990",
            "bid_qty": "1.0",
            "ask_price": "63010",
            "ask_qty": "1.0",
        }
        return await _build(s)

    snap = asyncio.run(run())
    ms = snap.market_state
    assert ms.bid == Decimal("62990")
    assert ms.ask == Decimal("63010")
    assert ms.spread is not None
    assert ms.spread > Decimal("0")
    assert ms.spread_bps is not None
    assert ms.spread_bps > Decimal("0")


def test_bid_ask_null_when_book_ticker_has_zero_prices():
    """Zero prices in book_ticker must not propagate as valid bid/ask."""
    async def run():
        s = SymbolState("BTCUSDT", "futures")
        s.last_book_ticker = {
            "bid_price": "0",
            "bid_qty": "0",
            "ask_price": "0",
            "ask_qty": "0",
        }
        return await _build(s)

    snap = asyncio.run(run())
    ms = snap.market_state
    # _safe_float returns None for 0; bid/ask should be None or spread absent
    assert ms.spread is None


# ── bid/ask from orderbook top (new fallback path) ────────────────────────────


def test_bid_ask_from_orderbook_when_no_book_ticker():
    """When last_book_ticker is None but last_book has data, bid/ask derive from top-of-book."""
    async def run():
        s = SymbolState("BTCUSDT", "spot")
        s.last_price = 63000.0
        s.last_book_ticker = None
        s.last_book = {
            "bids": [["62990", "0.5"], ["62980", "1.0"]],
            "asks": [["63010", "0.3"], ["63020", "0.8"]],
        }
        return await _build(s)

    snap = asyncio.run(run())
    ms = snap.market_state
    assert ms.bid == Decimal("62990")
    assert ms.ask == Decimal("63010")
    assert ms.bid_size == Decimal("0.5")
    assert ms.ask_size == Decimal("0.3")
    assert ms.spread is not None
    assert ms.spread > Decimal("0")
    assert ms.spread_bps is not None
    assert ms.spread_bps > Decimal("0")


def test_spread_bps_reasonable_for_orderbook_fallback():
    """spread_bps from orderbook top should be ~3 bps for a 20-pip spread on 63000."""
    async def run():
        s = SymbolState("BTCUSDT", "spot")
        s.last_book_ticker = None
        s.last_book = {
            "bids": [["62990", "1"]],
            "asks": [["63010", "1"]],
        }
        return await _build(s)

    snap = asyncio.run(run())
    ms = snap.market_state
    # spread = 20, mid ≈ 63000, bps ≈ 20/63000 * 10000 ≈ 3.17
    assert ms.spread_bps is not None
    assert Decimal("2") < ms.spread_bps < Decimal("5")


def test_bid_ask_null_when_neither_book_ticker_nor_orderbook():
    """With no book data at all, bid/ask/spread must stay None."""
    async def run():
        s = SymbolState("BTCUSDT", "spot")
        s.last_price = 63000.0
        s.last_book_ticker = None
        s.last_book = None
        return await _build(s)

    snap = asyncio.run(run())
    ms = snap.market_state
    assert ms.bid is None
    assert ms.ask is None
    assert ms.spread is None
    assert ms.spread_bps is None


def test_book_ticker_takes_precedence_over_orderbook():
    """When both book_ticker and last_book are set, book_ticker wins."""
    async def run():
        s = SymbolState("BTCUSDT", "futures")
        s.last_price = 63000.0
        s.last_book_ticker = {
            "bid_price": "62950",
            "bid_qty": "2",
            "ask_price": "63050",
            "ask_qty": "2",
        }
        s.last_book = {
            "bids": [["62990", "0.5"]],
            "asks": [["63010", "0.3"]],
        }
        return await _build(s)

    snap = asyncio.run(run())
    ms = snap.market_state
    # book_ticker prices (62950/63050) should win over orderbook (62990/63010)
    assert ms.bid == Decimal("62950")
    assert ms.ask == Decimal("63050")


def test_orderbook_fallback_handles_flat_price_format():
    """Orderbook rows can be [price_str, qty_str] — the fallback handles this."""
    async def run():
        s = SymbolState("BTCUSDT", "spot")
        s.last_book_ticker = None
        # Some providers give dicts, but the canonical Binance format is [str, str]
        s.last_book = {
            "bids": [["55000.50", "0.25"]],
            "asks": [["55001.00", "0.10"]],
        }
        return await _build(s)

    snap = asyncio.run(run())
    ms = snap.market_state
    assert ms.bid == Decimal("55000.50")
    assert ms.ask == Decimal("55001.00")


def test_orderbook_fallback_with_empty_bids_or_asks():
    """If bids or asks list is empty, bid/ask must remain None (no crash)."""
    async def run():
        s = SymbolState("BTCUSDT", "spot")
        s.last_book_ticker = None
        s.last_book = {
            "bids": [],  # empty
            "asks": [["63010", "0.3"]],
        }
        return await _build(s)

    snap = asyncio.run(run())
    ms = snap.market_state
    assert ms.bid is None
    assert ms.ask is None
