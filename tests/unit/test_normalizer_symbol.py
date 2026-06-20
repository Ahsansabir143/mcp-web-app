"""Tests for canonical symbol normalization."""
from __future__ import annotations

import pytest

from services.normalizer.symbol import normalize_symbol, symbol_from_stream


class TestNormalizeSymbol:
    def test_already_uppercase(self) -> None:
        assert normalize_symbol("BTCUSDT") == "BTCUSDT"

    def test_lowercase_converted(self) -> None:
        assert normalize_symbol("btcusdt") == "BTCUSDT"

    def test_mixed_case(self) -> None:
        assert normalize_symbol("btcUSDT") == "BTCUSDT"

    def test_strips_dot_p_suffix(self) -> None:
        assert normalize_symbol("BTCUSDT.P") == "BTCUSDT"

    def test_strips_lowercase_dot_p(self) -> None:
        assert normalize_symbol("btcusdt.p") == "BTCUSDT"

    def test_no_false_strip_of_non_suffix(self) -> None:
        # Symbol ending in P but not .P should not be stripped
        assert normalize_symbol("XRPUSDT") == "XRPUSDT"

    def test_strips_whitespace(self) -> None:
        assert normalize_symbol("  BTCUSDT  ") == "BTCUSDT"

    def test_eth_symbol(self) -> None:
        assert normalize_symbol("ethusdt") == "ETHUSDT"

    def test_sol_symbol(self) -> None:
        assert normalize_symbol("SOLUSDT.P") == "SOLUSDT"


class TestSymbolFromStream:
    def test_trade_stream(self) -> None:
        assert symbol_from_stream("btcusdt@trade") == "BTCUSDT"

    def test_agg_trade_stream(self) -> None:
        assert symbol_from_stream("ethusdt@aggTrade") == "ETHUSDT"

    def test_book_ticker_stream(self) -> None:
        assert symbol_from_stream("solusdt@bookTicker") == "SOLUSDT"

    def test_depth_stream(self) -> None:
        assert symbol_from_stream("btcusdt@depth@100ms") == "BTCUSDT"

    def test_depth_snapshot_stream(self) -> None:
        assert symbol_from_stream("btcusdt@depth@snapshot") == "BTCUSDT"

    def test_kline_stream(self) -> None:
        assert symbol_from_stream("btcusdt@kline_1m") == "BTCUSDT"

    def test_mark_price_stream(self) -> None:
        assert symbol_from_stream("btcusdt@markPrice") == "BTCUSDT"

    def test_force_order_stream(self) -> None:
        assert symbol_from_stream("btcusdt@forceOrder") == "BTCUSDT"

    def test_private_stream_returns_none(self) -> None:
        assert symbol_from_stream("user_data.ACCOUNT_UPDATE") is None

    def test_private_order_stream_returns_none(self) -> None:
        assert symbol_from_stream("user_data.ORDER_TRADE_UPDATE") is None

    def test_empty_string_returns_none(self) -> None:
        assert symbol_from_stream("") is None

    def test_no_at_sign_returns_none(self) -> None:
        assert symbol_from_stream("btcusdt") is None

    def test_dot_p_symbol_in_stream(self) -> None:
        assert symbol_from_stream("btcusdt.p@markPrice") == "BTCUSDT"
