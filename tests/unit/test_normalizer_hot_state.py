"""Tests for HotStateWriter and orderbook delta merge logic.

Uses AsyncMock to avoid live Redis connections.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.normalizer.handlers.base import HotStateWrite
from services.normalizer.handlers.orderbook import _apply_delta, handle_orderbook_delta
from services.normalizer.hot_state import HotStateWriter
from shared.schemas.enums import MarketType, Venue
from shared.schemas.events import RawEvent


# ─────────────────────────────────────────────────────────────────────────────
# HotStateWriter
# ─────────────────────────────────────────────────────────────────────────────

def _make_writer() -> tuple[HotStateWriter, MagicMock]:
    """Return a HotStateWriter with a mocked Redis pipeline."""
    redis_mock = AsyncMock()

    pipe_mock = AsyncMock()
    pipe_mock.__aenter__ = AsyncMock(return_value=pipe_mock)
    pipe_mock.__aexit__ = AsyncMock(return_value=False)
    pipe_mock.set = MagicMock()
    pipe_mock.execute = AsyncMock(return_value=[True])
    redis_mock.pipeline = MagicMock(return_value=pipe_mock)

    writer = HotStateWriter(redis=redis_mock)
    return writer, pipe_mock


@pytest.mark.asyncio
async def test_write_single_key() -> None:
    writer, pipe = _make_writer()
    await writer.write_all([HotStateWrite(key="k", value="v", ttl_s=60)])
    pipe.set.assert_called_once_with("k", "v", ex=60)


@pytest.mark.asyncio
async def test_write_multiple_keys_uses_pipeline() -> None:
    writer, pipe = _make_writer()
    writes = [
        HotStateWrite(key="k1", value="v1", ttl_s=10),
        HotStateWrite(key="k2", value="v2", ttl_s=30),
    ]
    await writer.write_all(writes)
    assert pipe.set.call_count == 2


@pytest.mark.asyncio
async def test_write_with_no_ttl() -> None:
    writer, pipe = _make_writer()
    await writer.write_all([HotStateWrite(key="k", value="v", ttl_s=None)])
    pipe.set.assert_called_once_with("k", "v")


@pytest.mark.asyncio
async def test_write_empty_list_is_noop() -> None:
    writer, pipe = _make_writer()
    await writer.write_all([])
    pipe.set.assert_not_called()
    pipe.execute.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Orderbook delta merge (_apply_delta)
# ─────────────────────────────────────────────────────────────────────────────

def _book(bids: list, asks: list, update_id: int = 100) -> dict:
    return {"last_update_id": update_id, "bids": bids, "asks": asks}


def test_apply_delta_updates_bid_qty() -> None:
    book = _book([["100.0", "1.0"]], [])
    result = _apply_delta(book, [("100.0", "2.0")], [], 101, 0)
    bid_map = {b[0]: b[1] for b in result["bids"]}
    assert bid_map["100.0"] == "2.0"


def test_apply_delta_removes_zero_qty_bid() -> None:
    book = _book([["100.0", "1.0"], ["99.0", "2.0"]], [])
    result = _apply_delta(book, [("99.0", "0.0")], [], 101, 0)
    prices = [b[0] for b in result["bids"]]
    assert "99.0" not in prices
    assert "100.0" in prices


def test_apply_delta_removes_zero_qty_ask() -> None:
    book = _book([], [["101.0", "1.0"], ["102.0", "0.5"]])
    result = _apply_delta(book, [], [("101.0", "0.0")], 101, 0)
    prices = [a[0] for a in result["asks"]]
    assert "101.0" not in prices
    assert "102.0" in prices


def test_apply_delta_adds_new_bid_level() -> None:
    book = _book([["100.0", "1.0"]], [])
    result = _apply_delta(book, [("98.0", "3.0")], [], 101, 0)
    bid_map = {b[0]: b[1] for b in result["bids"]}
    assert "98.0" in bid_map
    assert bid_map["98.0"] == "3.0"


def test_apply_delta_bids_sorted_descending() -> None:
    book = _book([["100.0", "1.0"], ["98.0", "1.0"]], [])
    result = _apply_delta(book, [("99.0", "2.0")], [], 101, 0)
    prices = [float(b[0]) for b in result["bids"]]
    assert prices == sorted(prices, reverse=True)


def test_apply_delta_asks_sorted_ascending() -> None:
    book = _book([], [["102.0", "1.0"], ["104.0", "1.0"]])
    result = _apply_delta(book, [], [("103.0", "0.5")], 101, 0)
    prices = [float(a[0]) for a in result["asks"]]
    assert prices == sorted(prices)


def test_apply_delta_updates_last_update_id() -> None:
    book = _book([["100.0", "1.0"]], [], update_id=100)
    result = _apply_delta(book, [], [], 105, 999)
    assert result["last_update_id"] == 105


def test_apply_delta_updates_ts() -> None:
    book = _book([], [])
    result = _apply_delta(book, [], [], 101, ts=1234567890)
    assert result["ts"] == 1234567890


def test_apply_delta_empty_deltas_preserves_book() -> None:
    bids = [["100.0", "1.0"], ["99.0", "2.0"]]
    asks = [["101.0", "0.5"]]
    book = _book(bids, asks)
    result = _apply_delta(book, [], [], 101, 0)
    assert len(result["bids"]) == 2
    assert len(result["asks"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Orderbook delta integration (handler + no-snapshot guard)
# ─────────────────────────────────────────────────────────────────────────────

def _delta_event() -> RawEvent:
    return RawEvent(
        venue=Venue.BINANCE,
        market_type=MarketType.SPOT,
        source_stream="btcusdt@depth@100ms",
        received_ms=1_718_880_000_000,
        payload={
            "e": "depthUpdate", "E": 1_718_880_000_000, "s": "BTCUSDT",
            "U": 101, "u": 105,
            "b": [["100.0", "2.0"]], "a": [],
        },
    )


def test_delta_handler_skips_hot_state_without_snapshot() -> None:
    r = handle_orderbook_delta(_delta_event(), current_book=None)
    assert r.hot_writes == []


def test_delta_handler_writes_hot_state_with_snapshot() -> None:
    snapshot = {"last_update_id": 100, "bids": [["100.0", "1.0"]], "asks": []}
    r = handle_orderbook_delta(_delta_event(), current_book=snapshot)
    assert len(r.hot_writes) == 1
    merged = json.loads(r.hot_writes[0].value)
    bid_map = {b[0]: b[1] for b in merged["bids"]}
    assert bid_map["100.0"] == "2.0"
