"""Unit tests for each normalizer handler.

All tests construct minimal RawEvent objects directly — no live Redis or stream
connections required.
"""
from __future__ import annotations

import json

import pytest

from shared.schemas.enums import EventType, MarketType, Venue
from shared.schemas.events import RawEvent

from services.normalizer.handlers.account_update import handle_account_update
from services.normalizer.handlers.agg_trade import handle_agg_trade
from services.normalizer.handlers.book_ticker import handle_book_ticker
from services.normalizer.handlers.kline import handle_kline
from services.normalizer.handlers.liquidation import handle_liquidation
from services.normalizer.handlers.mark_price import handle_mark_price
from services.normalizer.handlers.orderbook import handle_orderbook_delta, handle_orderbook_snapshot
from services.normalizer.handlers.trade import handle_trade
from services.normalizer.handlers.user_order import handle_user_order
from services.normalizer.router import route


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _raw(source_stream: str, payload: dict, market_type: MarketType = MarketType.SPOT) -> RawEvent:
    return RawEvent(
        venue=Venue.BINANCE,
        market_type=market_type,
        source_stream=source_stream,
        received_ms=1_718_880_000_000,
        payload=payload,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Trade
# ─────────────────────────────────────────────────────────────────────────────

class TestTradeHandler:
    def _event(self, market_type: MarketType = MarketType.SPOT) -> RawEvent:
        return _raw(
            "btcusdt@trade",
            {"e": "trade", "s": "BTCUSDT", "t": 123456, "p": "65432.10", "q": "0.00120",
             "T": 1718880000000, "m": False},
            market_type,
        )

    def test_event_type(self) -> None:
        r = handle_trade(self._event())
        assert r.event.event_type == EventType.TRADE

    def test_symbol_normalized(self) -> None:
        r = handle_trade(self._event())
        assert r.event.symbol == "BTCUSDT"

    def test_venue(self) -> None:
        assert handle_trade(self._event()).event.venue == Venue.BINANCE

    def test_data_fields(self) -> None:
        d = handle_trade(self._event()).event.data
        assert d["price"] == "65432.10"
        assert d["qty"] == "0.00120"
        assert d["is_buyer_maker"] is False
        assert d["trade_id"] == 123456

    def test_hot_state_price_key(self) -> None:
        result = handle_trade(self._event())
        assert len(result.hot_writes) == 1
        w = result.hot_writes[0]
        assert w.key == "market:spot:BTCUSDT:price"
        assert w.ttl_s == 60
        payload = json.loads(w.value)
        assert payload["price"] == "65432.10"

    def test_futures_price_key(self) -> None:
        result = handle_trade(self._event(MarketType.FUTURES))
        assert result.hot_writes[0].key == "market:futures:BTCUSDT:price"

    def test_symbol_from_stream_fallback(self) -> None:
        # payload has no "s" field — should fall back to stream name
        ev = _raw("ethusdt@trade", {"e": "trade", "t": 1, "p": "3000", "q": "1", "T": 100, "m": True})
        r = handle_trade(ev)
        assert r.event.symbol == "ETHUSDT"


# ─────────────────────────────────────────────────────────────────────────────
# AggTrade
# ─────────────────────────────────────────────────────────────────────────────

class TestAggTradeHandler:
    def _event(self) -> RawEvent:
        return _raw(
            "btcusdt@aggTrade",
            {"e": "aggTrade", "s": "BTCUSDT", "a": 990123, "p": "65440.00", "q": "0.25000",
             "f": 3801234568, "l": 3801234570, "T": 1718880001000, "m": False},
        )

    def test_event_type(self) -> None:
        assert handle_agg_trade(self._event()).event.event_type == EventType.AGG_TRADE

    def test_data_fields(self) -> None:
        d = handle_agg_trade(self._event()).event.data
        assert d["agg_trade_id"] == 990123
        assert d["first_trade_id"] == 3801234568
        assert d["last_trade_id"] == 3801234570
        assert d["is_buyer_maker"] is False

    def test_hot_state_price_key(self) -> None:
        r = handle_agg_trade(self._event())
        assert r.hot_writes[0].key == "market:spot:BTCUSDT:price"


# ─────────────────────────────────────────────────────────────────────────────
# BookTicker
# ─────────────────────────────────────────────────────────────────────────────

class TestBookTickerHandler:
    def _event(self) -> RawEvent:
        return _raw(
            "btcusdt@bookTicker",
            {"u": 400900217, "s": "BTCUSDT", "b": "65430.00", "B": "0.50000",
             "a": "65431.00", "A": "1.20000"},
        )

    def test_event_type(self) -> None:
        assert handle_book_ticker(self._event()).event.event_type == EventType.BOOK_TICKER

    def test_data_fields(self) -> None:
        d = handle_book_ticker(self._event()).event.data
        assert d["bid_price"] == "65430.00"
        assert d["ask_price"] == "65431.00"
        assert d["update_id"] == 400900217

    def test_hot_state_book_ticker_key(self) -> None:
        r = handle_book_ticker(self._event())
        assert len(r.hot_writes) == 1
        assert r.hot_writes[0].key == "market:spot:BTCUSDT:book_ticker"
        assert r.hot_writes[0].ttl_s == 10

    def test_hot_state_payload_structure(self) -> None:
        w = handle_book_ticker(self._event()).hot_writes[0]
        v = json.loads(w.value)
        assert v["bid_price"] == "65430.00"
        assert v["ask_price"] == "65431.00"


# ─────────────────────────────────────────────────────────────────────────────
# Orderbook Snapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestOrderbookSnapshotHandler:
    def _event(self) -> RawEvent:
        return _raw(
            "btcusdt@depth@snapshot",
            {
                "lastUpdateId": 160,
                "bids": [["65430.00", "0.50000"], ["65429.50", "1.20000"]],
                "asks": [["65431.00", "0.80000"], ["65431.50", "0.40000"]],
            },
        )

    def test_event_type(self) -> None:
        assert handle_orderbook_snapshot(self._event()).event.event_type == EventType.ORDERBOOK_SNAPSHOT

    def test_data_fields(self) -> None:
        d = handle_orderbook_snapshot(self._event()).event.data
        assert d["last_update_id"] == 160
        assert len(d["bids"]) == 2
        assert len(d["asks"]) == 2

    def test_hot_state_book_key(self) -> None:
        r = handle_orderbook_snapshot(self._event())
        assert r.hot_writes[0].key == "market:spot:BTCUSDT:book"
        assert r.hot_writes[0].ttl_s == 30

    def test_hot_state_book_value(self) -> None:
        w = handle_orderbook_snapshot(self._event()).hot_writes[0]
        v = json.loads(w.value)
        assert v["last_update_id"] == 160
        assert len(v["bids"]) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Orderbook Delta
# ─────────────────────────────────────────────────────────────────────────────

class TestOrderbookDeltaHandler:
    def _event(self) -> RawEvent:
        return _raw(
            "btcusdt@depth@100ms",
            {"e": "depthUpdate", "E": 1718880002000, "s": "BTCUSDT",
             "U": 157, "u": 160,
             "b": [["65430.00", "0.60000"], ["65428.00", "0.00000"]],
             "a": [["65431.00", "0.70000"]]},
        )

    def test_event_type(self) -> None:
        assert handle_orderbook_delta(self._event()).event.event_type == EventType.ORDERBOOK_DELTA

    def test_no_hot_state_without_snapshot(self) -> None:
        r = handle_orderbook_delta(self._event(), current_book=None)
        assert r.hot_writes == []

    def test_hot_state_with_snapshot(self) -> None:
        current = {
            "last_update_id": 156,
            "bids": [["65430.00", "0.50000"], ["65428.00", "1.20000"]],
            "asks": [["65431.00", "0.80000"]],
        }
        r = handle_orderbook_delta(self._event(), current_book=current)
        assert len(r.hot_writes) == 1
        assert r.hot_writes[0].key == "market:spot:BTCUSDT:book"

    def test_delta_removes_zero_qty_level(self) -> None:
        current = {
            "last_update_id": 156,
            "bids": [["65430.00", "0.50000"], ["65428.00", "1.20000"]],
            "asks": [["65431.00", "0.80000"]],
        }
        r = handle_orderbook_delta(self._event(), current_book=current)
        merged = json.loads(r.hot_writes[0].value)
        bid_prices = [b[0] for b in merged["bids"]]
        assert "65428.00" not in bid_prices  # removed (qty=0)
        assert "65430.00" in bid_prices      # updated

    def test_delta_updates_qty(self) -> None:
        current = {
            "last_update_id": 156,
            "bids": [["65430.00", "0.50000"]],
            "asks": [["65431.00", "0.80000"]],
        }
        r = handle_orderbook_delta(self._event(), current_book=current)
        merged = json.loads(r.hot_writes[0].value)
        bid_map = {b[0]: b[1] for b in merged["bids"]}
        assert bid_map["65430.00"] == "0.60000"  # updated from 0.50000

    def test_bids_sorted_descending(self) -> None:
        current = {
            "last_update_id": 100,
            "bids": [["65430.00", "0.5"], ["65425.00", "1.0"]],
            "asks": [],
        }
        ev = _raw("btcusdt@depth@100ms", {
            "e": "depthUpdate", "E": 100, "U": 101, "u": 102,
            "b": [["65427.00", "0.3"]], "a": [],
        })
        r = handle_orderbook_delta(ev, current_book=current)
        merged = json.loads(r.hot_writes[0].value)
        prices = [float(b[0]) for b in merged["bids"]]
        assert prices == sorted(prices, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Kline
# ─────────────────────────────────────────────────────────────────────────────

class TestKlineHandler:
    def _event(self, interval: str = "1m") -> RawEvent:
        return _raw(
            f"btcusdt@kline_{interval}",
            {"e": "kline", "s": "BTCUSDT", "k": {
                "t": 1718880000000, "T": 1718880059999, "s": "BTCUSDT",
                "i": interval, "o": "65420.00", "c": "65440.00",
                "h": "65445.00", "l": "65415.00", "v": "12.45000",
                "q": "815234.50", "n": 21, "V": "6.50000", "Q": "425123.00",
                "x": True,
            }},
        )

    def test_event_type(self) -> None:
        assert handle_kline(self._event()).event.event_type == EventType.KLINE

    def test_closed_flag(self) -> None:
        assert handle_kline(self._event()).event.data["is_closed"] is True

    def test_interval_in_data(self) -> None:
        assert handle_kline(self._event()).event.data["interval"] == "1m"

    def test_hot_state_key(self) -> None:
        r = handle_kline(self._event())
        assert r.hot_writes[0].key == "market:spot:BTCUSDT:klines:1m"

    def test_ttl_1m(self) -> None:
        assert handle_kline(self._event("1m")).hot_writes[0].ttl_s == 120

    def test_ttl_1h(self) -> None:
        assert handle_kline(self._event("1h")).hot_writes[0].ttl_s == 7_200

    def test_ttl_4h(self) -> None:
        assert handle_kline(self._event("4h")).hot_writes[0].ttl_s == 28_800

    def test_hot_state_value_contains_ohlcv(self) -> None:
        v = json.loads(handle_kline(self._event()).hot_writes[0].value)
        assert v["open"] == "65420.00"
        assert v["close"] == "65440.00"


# ─────────────────────────────────────────────────────────────────────────────
# MarkPrice
# ─────────────────────────────────────────────────────────────────────────────

class TestMarkPriceHandler:
    def _event(self) -> RawEvent:
        return _raw(
            "btcusdt@markPrice",
            {"e": "markPriceUpdate", "E": 1718880000000, "s": "BTCUSDT",
             "p": "65435.50", "P": "65440.00", "i": "65438.00",
             "r": "0.00010000", "T": 1718886000000},
            MarketType.FUTURES,
        )

    def test_event_type(self) -> None:
        assert handle_mark_price(self._event()).event.event_type == EventType.MARK_PRICE

    def test_data_fields(self) -> None:
        d = handle_mark_price(self._event()).event.data
        assert d["mark_price"] == "65435.50"
        assert d["funding_rate"] == "0.00010000"
        assert d["next_funding_time_ms"] == 1718886000000

    def test_two_hot_state_writes(self) -> None:
        r = handle_mark_price(self._event())
        assert len(r.hot_writes) == 2

    def test_mark_key(self) -> None:
        keys = {w.key for w in handle_mark_price(self._event()).hot_writes}
        assert "market:futures:BTCUSDT:mark" in keys

    def test_funding_key(self) -> None:
        keys = {w.key for w in handle_mark_price(self._event()).hot_writes}
        assert "market:futures:BTCUSDT:funding" in keys

    def test_mark_ttl(self) -> None:
        mark_write = next(
            w for w in handle_mark_price(self._event()).hot_writes
            if "mark" in w.key and "funding" not in w.key
        )
        assert mark_write.ttl_s == 60

    def test_funding_ttl(self) -> None:
        funding_write = next(
            w for w in handle_mark_price(self._event()).hot_writes
            if "funding" in w.key
        )
        assert funding_write.ttl_s == 3_600


# ─────────────────────────────────────────────────────────────────────────────
# Liquidation
# ─────────────────────────────────────────────────────────────────────────────

class TestLiquidationHandler:
    def _event(self) -> RawEvent:
        return _raw(
            "btcusdt@forceOrder",
            {"e": "forceOrder", "E": 1718880005000, "o": {
                "s": "BTCUSDT", "S": "SELL", "o": "LIMIT", "f": "IOC",
                "q": "0.10000", "p": "65000.00", "ap": "65010.00",
                "X": "FILLED", "l": "0.10000", "z": "0.10000", "T": 1718880005000,
            }},
            MarketType.FUTURES,
        )

    def test_event_type(self) -> None:
        assert handle_liquidation(self._event()).event.event_type == EventType.LIQUIDATION

    def test_no_hot_state_writes(self) -> None:
        assert handle_liquidation(self._event()).hot_writes == []

    def test_data_fields(self) -> None:
        d = handle_liquidation(self._event()).event.data
        assert d["side"] == "SELL"
        assert d["status"] == "FILLED"
        assert d["price"] == "65000.00"

    def test_symbol(self) -> None:
        assert handle_liquidation(self._event()).event.symbol == "BTCUSDT"


# ─────────────────────────────────────────────────────────────────────────────
# AccountUpdate
# ─────────────────────────────────────────────────────────────────────────────

class TestAccountUpdateHandler:
    def _event(self) -> RawEvent:
        return _raw(
            "user_data.ACCOUNT_UPDATE",
            {"e": "ACCOUNT_UPDATE", "E": 1718880010000, "a": {
                "m": "ORDER",
                "B": [{"a": "USDT", "wb": "10000.00", "cw": "9500.00", "bc": "0"}],
                "P": [{"s": "BTCUSDT", "pa": "0.10000", "ep": "65200.00",
                       "up": "23.50", "cr": "0", "mt": "cross", "iw": "0", "ps": "BOTH"}],
            }},
            MarketType.FUTURES,
        )

    def test_event_type(self) -> None:
        assert handle_account_update(self._event()).event.event_type == EventType.ACCOUNT_UPDATE

    def test_symbol_is_empty_string(self) -> None:
        # Account updates span multiple symbols — symbol field intentionally empty
        assert handle_account_update(self._event()).event.symbol == ""

    def test_no_hot_state_without_user_id(self) -> None:
        r = handle_account_update(self._event(), user_id="")
        assert r.hot_writes == []

    def test_two_hot_state_writes_with_user_id(self) -> None:
        r = handle_account_update(self._event(), user_id="abc-123")
        assert len(r.hot_writes) == 2

    def test_balance_hot_state_key(self) -> None:
        r = handle_account_update(self._event(), user_id="u1")
        keys = {w.key for w in r.hot_writes}
        assert "account:u1:balances" in keys

    def test_position_hot_state_key(self) -> None:
        r = handle_account_update(self._event(), user_id="u1")
        keys = {w.key for w in r.hot_writes}
        assert "account:u1:positions" in keys

    def test_balance_data_structure(self) -> None:
        r = handle_account_update(self._event(), user_id="u1")
        balance_write = next(w for w in r.hot_writes if "balances" in w.key)
        v = json.loads(balance_write.value)
        assert v["balances"][0]["asset"] == "USDT"
        assert v["balances"][0]["wallet_balance"] == "10000.00"

    def test_position_data_structure(self) -> None:
        r = handle_account_update(self._event(), user_id="u1")
        pos_write = next(w for w in r.hot_writes if "positions" in w.key)
        v = json.loads(pos_write.value)
        assert v["positions"][0]["symbol"] == "BTCUSDT"


# ─────────────────────────────────────────────────────────────────────────────
# UserOrder
# ─────────────────────────────────────────────────────────────────────────────

class TestUserOrderHandler:
    def _event(self, status: str = "NEW") -> RawEvent:
        return _raw(
            "user_data.ORDER_TRADE_UPDATE",
            {"e": "ORDER_TRADE_UPDATE", "E": 1718880015000, "o": {
                "s": "BTCUSDT", "c": "myOrder001", "S": "BUY", "o": "LIMIT",
                "f": "GTC", "q": "0.10000", "p": "65200.00", "ap": "65200.00",
                "X": status, "i": 8886774, "l": "0.10000", "z": "0.10000",
                "L": "65200.00", "N": "USDT", "n": "0.02608", "T": 1718880015000,
                "t": 521, "m": False, "R": False, "ps": "BOTH", "rp": "0",
                "sp": "0",
            }},
            MarketType.FUTURES,
        )

    def test_event_type(self) -> None:
        assert handle_user_order(self._event()).event.event_type == EventType.USER_ORDER

    def test_symbol(self) -> None:
        assert handle_user_order(self._event()).event.symbol == "BTCUSDT"

    def test_data_fields(self) -> None:
        d = handle_user_order(self._event()).event.data
        assert d["client_order_id"] == "myOrder001"
        assert d["order_status"] == "NEW"
        assert d["side"] == "BUY"

    def test_new_order_writes_open_orders(self) -> None:
        r = handle_user_order(self._event("NEW"), user_id="u1")
        keys = {w.key for w in r.hot_writes}
        assert "account:u1:open_orders" in keys

    def test_partially_filled_writes_open_orders(self) -> None:
        r = handle_user_order(self._event("PARTIALLY_FILLED"), user_id="u1")
        assert any("open_orders" in w.key for w in r.hot_writes)

    def test_filled_no_hot_state(self) -> None:
        r = handle_user_order(self._event("FILLED"), user_id="u1")
        assert r.hot_writes == []

    def test_canceled_no_hot_state(self) -> None:
        r = handle_user_order(self._event("CANCELED"), user_id="u1")
        assert r.hot_writes == []

    def test_no_hot_state_without_user_id(self) -> None:
        r = handle_user_order(self._event("NEW"), user_id="")
        assert r.hot_writes == []


# ─────────────────────────────────────────────────────────────────────────────
# Router
# ─────────────────────────────────────────────────────────────────────────────

class TestRouter:
    def _raw_ev(self, source_stream: str, payload: dict) -> RawEvent:
        return _raw(source_stream, payload)

    def test_routes_trade(self) -> None:
        ev = _raw("btcusdt@trade", {"e": "trade", "s": "BTCUSDT", "t": 1, "p": "1", "q": "1", "T": 1, "m": False})
        assert route(ev).event.event_type == EventType.TRADE

    def test_routes_agg_trade(self) -> None:
        ev = _raw("btcusdt@aggTrade", {"e": "aggTrade", "s": "BTCUSDT", "a": 1, "p": "1", "q": "1", "f": 1, "l": 1, "T": 1, "m": False})
        assert route(ev).event.event_type == EventType.AGG_TRADE

    def test_routes_book_ticker(self) -> None:
        ev = _raw("btcusdt@bookTicker", {"s": "BTCUSDT", "b": "1", "B": "1", "a": "2", "A": "1"})
        assert route(ev).event.event_type == EventType.BOOK_TICKER

    def test_routes_depth_snapshot(self) -> None:
        ev = _raw("btcusdt@depth@snapshot", {"lastUpdateId": 1, "bids": [], "asks": []})
        assert route(ev).event.event_type == EventType.ORDERBOOK_SNAPSHOT

    def test_routes_depth_delta(self) -> None:
        ev = _raw("btcusdt@depth@100ms", {"e": "depthUpdate", "U": 1, "u": 2, "b": [], "a": []})
        assert route(ev).event.event_type == EventType.ORDERBOOK_DELTA

    def test_routes_kline(self) -> None:
        ev = _raw("btcusdt@kline_1m", {"e": "kline", "k": {
            "t": 1, "T": 2, "s": "BTCUSDT", "i": "1m",
            "o": "1", "c": "2", "h": "3", "l": "0", "v": "1", "q": "1", "n": 1,
            "V": "0", "Q": "0", "x": False,
        }})
        assert route(ev).event.event_type == EventType.KLINE

    def test_routes_mark_price(self) -> None:
        ev = _raw("btcusdt@markPrice", {"e": "markPriceUpdate", "E": 1, "s": "BTCUSDT",
                                         "p": "1", "i": "1", "r": "0", "T": 1}, MarketType.FUTURES)
        assert route(ev).event.event_type == EventType.MARK_PRICE

    def test_routes_liquidation(self) -> None:
        ev = _raw("btcusdt@forceOrder", {"e": "forceOrder", "E": 1, "o": {
            "s": "BTCUSDT", "S": "SELL", "o": "LIMIT", "f": "IOC",
            "q": "1", "p": "1", "ap": "1", "X": "FILLED", "l": "1", "z": "1", "T": 1,
        }}, MarketType.FUTURES)
        assert route(ev).event.event_type == EventType.LIQUIDATION

    def test_routes_account_update(self) -> None:
        ev = _raw("user_data.ACCOUNT_UPDATE", {"e": "ACCOUNT_UPDATE", "E": 1, "a": {"m": "ORDER", "B": [], "P": []}}, MarketType.FUTURES)
        assert route(ev).event.event_type == EventType.ACCOUNT_UPDATE

    def test_routes_user_order(self) -> None:
        ev = _raw("user_data.ORDER_TRADE_UPDATE", {"e": "ORDER_TRADE_UPDATE", "E": 1, "o": {
            "s": "BTCUSDT", "c": "x", "S": "BUY", "o": "LIMIT", "f": "GTC",
            "q": "1", "p": "1", "ap": "1", "X": "NEW", "i": 1, "l": "0", "z": "0",
            "L": "0", "N": "", "n": "0", "T": 1, "t": 0, "m": False, "R": False,
            "ps": "BOTH", "rp": "0", "sp": "0",
        }}, MarketType.FUTURES)
        assert route(ev).event.event_type == EventType.USER_ORDER

    def test_unknown_stream_returns_none(self) -> None:
        ev = _raw("btcusdt@unknownEvent", {"data": "x"})
        assert route(ev) is None
