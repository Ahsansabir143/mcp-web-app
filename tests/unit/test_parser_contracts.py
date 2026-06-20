"""Parser contract test fixtures.

These tests assert that raw Binance WebSocket payloads match the expected
structure consumed by the normalizer.  No parsing logic runs here — the
tests validate that each fixture is a well-formed dict with the required
keys so the normalizer can be written and tested against known-good inputs.
"""
from __future__ import annotations

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — raw payloads as they arrive from Binance combined stream endpoint
# wss://stream.binance.com/stream  (spot)
# wss://fstream.binance.com/stream  (futures)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def raw_trade_spot() -> dict:
    """Spot trade event.  stream: btcusdt@trade"""
    return {
        "stream": "btcusdt@trade",
        "data": {
            "e": "trade",
            "E": 1718880000000,
            "s": "BTCUSDT",
            "t": 3801234567,
            "p": "65432.10",
            "q": "0.00120",
            "b": 1234567890,
            "a": 1234567891,
            "T": 1718880000000,
            "m": False,
            "M": True,
        },
    }


@pytest.fixture()
def raw_trade_futures() -> dict:
    """Futures trade event.  stream: btcusdt@aggTrade (futures use aggTrade for public trades)
    Futures also expose @trade but with the same structure minus buyer/seller order IDs."""
    return {
        "stream": "btcusdt@trade",
        "data": {
            "e": "trade",
            "E": 1718880000001,
            "s": "BTCUSDT",
            "t": 9801234567,
            "p": "65430.00",
            "q": "0.05000",
            "b": 2234567890,
            "a": 2234567891,
            "T": 1718880000001,
            "m": True,
        },
    }


@pytest.fixture()
def raw_agg_trade() -> dict:
    """Aggregate trade event.  stream: btcusdt@aggTrade"""
    return {
        "stream": "btcusdt@aggTrade",
        "data": {
            "e": "aggTrade",
            "E": 1718880001000,
            "s": "BTCUSDT",
            "a": 990123456,
            "p": "65440.00",
            "q": "0.25000",
            "f": 3801234568,
            "l": 3801234570,
            "T": 1718880001000,
            "m": False,
            "M": True,
        },
    }


@pytest.fixture()
def raw_book_ticker() -> dict:
    """Best bid/ask event.  stream: btcusdt@bookTicker"""
    return {
        "stream": "btcusdt@bookTicker",
        "data": {
            "u": 400900217,
            "s": "BTCUSDT",
            "b": "65430.00",
            "B": "0.50000",
            "a": "65431.00",
            "A": "1.20000",
        },
    }


@pytest.fixture()
def raw_orderbook_snapshot() -> dict:
    """Full depth snapshot — obtained via REST /api/v3/depth?symbol=BTCUSDT&limit=20.
    Published into raw stream after initial subscription to @depth."""
    return {
        "stream": "btcusdt@depth@snapshot",
        "data": {
            "lastUpdateId": 160,
            "bids": [
                ["65430.00", "0.50000"],
                ["65429.50", "1.20000"],
                ["65429.00", "3.00000"],
            ],
            "asks": [
                ["65431.00", "0.80000"],
                ["65431.50", "0.40000"],
                ["65432.00", "2.10000"],
            ],
        },
    }


@pytest.fixture()
def raw_orderbook_delta() -> dict:
    """Incremental depth update.  stream: btcusdt@depth@100ms"""
    return {
        "stream": "btcusdt@depth@100ms",
        "data": {
            "e": "depthUpdate",
            "E": 1718880002000,
            "s": "BTCUSDT",
            "U": 157,
            "u": 160,
            "b": [
                ["65430.00", "0.60000"],
                ["65428.00", "0.00000"],
            ],
            "a": [
                ["65431.00", "0.70000"],
                ["65435.00", "0.00000"],
            ],
        },
    }


@pytest.fixture()
def raw_kline_spot() -> dict:
    """Kline/candlestick event.  stream: btcusdt@kline_1m"""
    return {
        "stream": "btcusdt@kline_1m",
        "data": {
            "e": "kline",
            "E": 1718880060000,
            "s": "BTCUSDT",
            "k": {
                "t": 1718880000000,
                "T": 1718880059999,
                "s": "BTCUSDT",
                "i": "1m",
                "f": 3801234560,
                "L": 3801234580,
                "o": "65420.00",
                "c": "65440.00",
                "h": "65445.00",
                "l": "65415.00",
                "v": "12.45000",
                "n": 21,
                "x": True,
                "q": "815234.50",
                "V": "6.50000",
                "Q": "425123.00",
                "B": "0",
            },
        },
    }


@pytest.fixture()
def raw_mark_price() -> dict:
    """Mark price + funding rate.  stream: btcusdt@markPrice (futures only)"""
    return {
        "stream": "btcusdt@markPrice",
        "data": {
            "e": "markPriceUpdate",
            "E": 1718880000000,
            "s": "BTCUSDT",
            "p": "65435.50",
            "P": "65440.00",
            "i": "65438.00",
            "r": "0.00010000",
            "T": 1718886000000,
        },
    }


@pytest.fixture()
def raw_liquidation() -> dict:
    """Liquidation order event.  stream: btcusdt@forceOrder (futures only)"""
    return {
        "stream": "btcusdt@forceOrder",
        "data": {
            "e": "forceOrder",
            "E": 1718880005000,
            "o": {
                "s": "BTCUSDT",
                "S": "SELL",
                "o": "LIMIT",
                "f": "IOC",
                "q": "0.10000",
                "p": "65000.00",
                "ap": "65010.00",
                "X": "FILLED",
                "l": "0.10000",
                "z": "0.10000",
                "T": 1718880005000,
            },
        },
    }


@pytest.fixture()
def raw_account_update_futures() -> dict:
    """Private ACCOUNT_UPDATE event (futures user data stream)."""
    return {
        "e": "ACCOUNT_UPDATE",
        "E": 1718880010000,
        "T": 1718880010000,
        "a": {
            "m": "ORDER",
            "B": [
                {
                    "a": "USDT",
                    "wb": "10000.00000000",
                    "cw": "9500.00000000",
                    "bc": "0",
                },
            ],
            "P": [
                {
                    "s": "BTCUSDT",
                    "pa": "0.10000",
                    "ep": "65200.00",
                    "cr": "0",
                    "up": "23.50",
                    "mt": "cross",
                    "iw": "0",
                    "ps": "BOTH",
                },
            ],
        },
    }


@pytest.fixture()
def raw_order_trade_update_futures() -> dict:
    """Private ORDER_TRADE_UPDATE event (futures user data stream)."""
    return {
        "e": "ORDER_TRADE_UPDATE",
        "E": 1718880015000,
        "T": 1718880015000,
        "o": {
            "s": "BTCUSDT",
            "c": "myOrder001",
            "S": "BUY",
            "o": "LIMIT",
            "f": "GTC",
            "q": "0.10000",
            "p": "65200.00",
            "ap": "65200.00",
            "sp": "0",
            "x": "TRADE",
            "X": "FILLED",
            "i": 8886774,
            "l": "0.10000",
            "z": "0.10000",
            "L": "65200.00",
            "N": "USDT",
            "n": "0.02608",
            "T": 1718880015000,
            "t": 521,
            "b": "9934.97",
            "a": "9000",
            "m": False,
            "R": False,
            "wt": "CONTRACT_PRICE",
            "ot": "LIMIT",
            "ps": "BOTH",
            "cp": False,
            "rp": "0",
            "pP": False,
            "si": 0,
            "ss": 0,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Contract assertions
# ─────────────────────────────────────────────────────────────────────────────


class TestTradeContract:
    def test_spot_has_required_fields(self, raw_trade_spot: dict) -> None:
        d = raw_trade_spot["data"]
        assert d["e"] == "trade"
        assert "s" in d  # symbol
        assert "t" in d  # trade id
        assert "p" in d  # price
        assert "q" in d  # quantity
        assert "T" in d  # trade time
        assert isinstance(d["m"], bool)  # buyer is maker

    def test_spot_combined_stream_envelope(self, raw_trade_spot: dict) -> None:
        assert "stream" in raw_trade_spot
        assert "data" in raw_trade_spot
        assert raw_trade_spot["stream"].endswith("@trade")

    def test_futures_trade_missing_buyer_seller_ok(self, raw_trade_futures: dict) -> None:
        d = raw_trade_futures["data"]
        # futures @trade does not guarantee b/a (buyer/seller order ids)
        assert "p" in d and "q" in d and "T" in d


class TestAggTradeContract:
    def test_required_fields(self, raw_agg_trade: dict) -> None:
        d = raw_agg_trade["data"]
        assert d["e"] == "aggTrade"
        assert "a" in d  # aggregate trade id
        assert "p" in d
        assert "q" in d
        assert "f" in d  # first trade id
        assert "l" in d  # last trade id
        assert "T" in d
        assert isinstance(d["m"], bool)

    def test_combined_stream_envelope(self, raw_agg_trade: dict) -> None:
        assert raw_agg_trade["stream"].endswith("@aggTrade")


class TestBookTickerContract:
    def test_required_fields(self, raw_book_ticker: dict) -> None:
        d = raw_book_ticker["data"]
        assert "s" in d  # symbol
        assert "b" in d  # best bid price
        assert "B" in d  # best bid qty
        assert "a" in d  # best ask price
        assert "A" in d  # best ask qty

    def test_combined_stream_envelope(self, raw_book_ticker: dict) -> None:
        assert raw_book_ticker["stream"].endswith("@bookTicker")


class TestOrderbookContract:
    def test_snapshot_required_fields(self, raw_orderbook_snapshot: dict) -> None:
        d = raw_orderbook_snapshot["data"]
        assert "lastUpdateId" in d
        assert isinstance(d["bids"], list)
        assert isinstance(d["asks"], list)
        # each level is [price_str, qty_str]
        assert len(d["bids"][0]) == 2
        assert len(d["asks"][0]) == 2

    def test_snapshot_levels_are_strings(self, raw_orderbook_snapshot: dict) -> None:
        d = raw_orderbook_snapshot["data"]
        for price, qty in d["bids"] + d["asks"]:
            assert isinstance(price, str)
            assert isinstance(qty, str)

    def test_delta_required_fields(self, raw_orderbook_delta: dict) -> None:
        d = raw_orderbook_delta["data"]
        assert d["e"] == "depthUpdate"
        assert "U" in d  # first update id in event
        assert "u" in d  # last update id in event
        assert isinstance(d["b"], list)
        assert isinstance(d["a"], list)

    def test_delta_zero_qty_means_remove(self, raw_orderbook_delta: dict) -> None:
        d = raw_orderbook_delta["data"]
        removals_bid = [level for level in d["b"] if level[1] == "0.00000"]
        assert len(removals_bid) == 1
        assert removals_bid[0][0] == "65428.00"

    def test_combined_stream_envelope(
        self, raw_orderbook_delta: dict, raw_orderbook_snapshot: dict
    ) -> None:
        assert "depth" in raw_orderbook_delta["stream"]
        assert "depth" in raw_orderbook_snapshot["stream"]


class TestKlineContract:
    def test_required_fields(self, raw_kline_spot: dict) -> None:
        d = raw_kline_spot["data"]
        assert d["e"] == "kline"
        assert "k" in d
        k = d["k"]
        for field in ("t", "T", "s", "i", "o", "c", "h", "l", "v", "n", "x"):
            assert field in k, f"missing kline inner field: {field}"

    def test_kline_closed_flag(self, raw_kline_spot: dict) -> None:
        assert raw_kline_spot["data"]["k"]["x"] is True

    def test_combined_stream_envelope(self, raw_kline_spot: dict) -> None:
        assert "@kline_" in raw_kline_spot["stream"]


class TestMarkPriceContract:
    def test_required_fields(self, raw_mark_price: dict) -> None:
        d = raw_mark_price["data"]
        assert d["e"] == "markPriceUpdate"
        assert "s" in d  # symbol
        assert "p" in d  # mark price
        assert "r" in d  # funding rate
        assert "T" in d  # next funding time

    def test_index_price_present(self, raw_mark_price: dict) -> None:
        assert "i" in raw_mark_price["data"]

    def test_combined_stream_envelope(self, raw_mark_price: dict) -> None:
        assert raw_mark_price["stream"].endswith("@markPrice")


class TestLiquidationContract:
    def test_required_fields(self, raw_liquidation: dict) -> None:
        d = raw_liquidation["data"]
        assert d["e"] == "forceOrder"
        o = d["o"]
        for field in ("s", "S", "o", "q", "p", "ap", "X", "l", "z", "T"):
            assert field in o, f"missing forceOrder field: {field}"

    def test_status_is_filled(self, raw_liquidation: dict) -> None:
        assert raw_liquidation["data"]["o"]["X"] == "FILLED"

    def test_combined_stream_envelope(self, raw_liquidation: dict) -> None:
        assert raw_liquidation["stream"].endswith("@forceOrder")


class TestPrivateStreamContracts:
    def test_account_update_structure(self, raw_account_update_futures: dict) -> None:
        d = raw_account_update_futures
        assert d["e"] == "ACCOUNT_UPDATE"
        assert "a" in d
        assert "B" in d["a"]  # balances
        assert "P" in d["a"]  # positions
        b = d["a"]["B"][0]
        assert "a" in b   # asset
        assert "wb" in b  # wallet balance
        assert "cw" in b  # cross wallet balance

    def test_account_update_position_fields(self, raw_account_update_futures: dict) -> None:
        pos = raw_account_update_futures["a"]["P"][0]
        assert "s" in pos   # symbol
        assert "pa" in pos  # position amount
        assert "ep" in pos  # entry price
        assert "up" in pos  # unrealized pnl
        assert "ps" in pos  # position side

    def test_order_trade_update_structure(self, raw_order_trade_update_futures: dict) -> None:
        d = raw_order_trade_update_futures
        assert d["e"] == "ORDER_TRADE_UPDATE"
        o = d["o"]
        for field in ("s", "c", "S", "o", "f", "q", "p", "X", "i", "T"):
            assert field in o, f"missing ORDER_TRADE_UPDATE field: {field}"

    def test_order_trade_update_fill_fields(self, raw_order_trade_update_futures: dict) -> None:
        o = raw_order_trade_update_futures["o"]
        assert "l" in o   # last filled qty
        assert "z" in o   # cumulative filled qty
        assert "L" in o   # last filled price
        assert "n" in o   # commission amount
        assert "N" in o   # commission asset
        assert "t" in o   # trade id
        assert "rp" in o  # realized pnl

    def test_no_stream_envelope_for_private(
        self,
        raw_account_update_futures: dict,
        raw_order_trade_update_futures: dict,
    ) -> None:
        # Private user data stream events arrive WITHOUT the combined-stream envelope
        assert "stream" not in raw_account_update_futures
        assert "stream" not in raw_order_trade_update_futures
