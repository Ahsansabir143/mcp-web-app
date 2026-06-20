"""Unit tests for Redis key builder and stream names."""
import pytest

from shared.redis.keys import RedisKeys
from shared.redis.streams import StreamNames


class TestStreamNames:
    def test_all_streams_defined(self):
        streams = StreamNames.all()
        assert "stream:binance:raw" in streams
        assert "stream:binance:normalized" in streams
        assert "stream:analytics:derived" in streams
        assert "stream:strategy:intents" in streams
        assert "stream:execution:events" in streams
        assert "stream:mcp:audit" in streams
        assert len(streams) == 6


class TestRedisKeys:
    # Market keys
    def test_market_price(self):
        assert RedisKeys.market_price("futures", "BTCUSDT") == "market:futures:BTCUSDT:price"

    def test_market_book(self):
        assert RedisKeys.market_book("spot", "ETHUSDT") == "market:spot:ETHUSDT:book"

    def test_market_klines(self):
        assert RedisKeys.market_klines("futures", "BTCUSDT", "1m") == "market:futures:BTCUSDT:klines:1m"

    def test_market_continuous_klines(self):
        key = RedisKeys.market_continuous_klines("futures", "BTCUSDT", "4h")
        assert key == "market:futures:BTCUSDT:continuous_klines:4h"

    def test_market_funding(self):
        assert RedisKeys.market_funding("futures", "BTCUSDT") == "market:futures:BTCUSDT:funding"

    def test_market_oi(self):
        assert RedisKeys.market_oi("futures", "BTCUSDT") == "market:futures:BTCUSDT:oi"

    def test_market_mark(self):
        assert RedisKeys.market_mark("futures", "BTCUSDT") == "market:futures:BTCUSDT:mark"

    def test_market_book_ticker(self):
        assert RedisKeys.market_book_ticker("spot", "BTCUSDT") == "market:spot:BTCUSDT:book_ticker"

    # Analytics keys
    def test_analytics_score(self):
        assert RedisKeys.analytics_score("futures", "BTCUSDT") == "analytics:futures:BTCUSDT:score"

    def test_analytics_walls(self):
        assert RedisKeys.analytics_walls("futures", "BTCUSDT") == "analytics:futures:BTCUSDT:walls"

    def test_analytics_cvd(self):
        assert RedisKeys.analytics_cvd("futures", "BTCUSDT") == "analytics:futures:BTCUSDT:cvd"

    def test_analytics_indicators(self):
        key = RedisKeys.analytics_indicators("futures", "BTCUSDT", "15m")
        assert key == "analytics:futures:BTCUSDT:indicators:15m"

    def test_analytics_snapshot(self):
        key = RedisKeys.analytics_snapshot("futures", "BTCUSDT")
        assert key == "analytics:futures:BTCUSDT:snapshot"

    def test_analytics_liquidation_clusters(self):
        key = RedisKeys.analytics_liquidation_clusters("futures", "BTCUSDT")
        assert key == "analytics:futures:BTCUSDT:liquidation_clusters"

    # Account keys
    def test_account_snapshot(self):
        assert RedisKeys.account_snapshot("user-123") == "account:user-123:snapshot"

    def test_account_balances(self):
        assert RedisKeys.account_balances("user-123") == "account:user-123:balances"

    def test_account_positions(self):
        assert RedisKeys.account_positions("user-123") == "account:user-123:positions"

    def test_account_open_orders(self):
        assert RedisKeys.account_open_orders("user-123") == "account:user-123:open_orders"

    # Risk keys
    def test_risk_state(self):
        assert RedisKeys.risk_state("user-123") == "risk:user-123:state"

    def test_risk_limits(self):
        assert RedisKeys.risk_limits("user-123") == "risk:user-123:limits"

    # Strategy keys
    def test_strategy_active(self):
        assert RedisKeys.strategy_active("strat-1") == "strategy:strat-1:active"

    def test_strategy_version(self):
        assert RedisKeys.strategy_version("strat-1", 3) == "strategy:strat-1:version:3"

    # Approval
    def test_approval_level(self):
        assert RedisKeys.approval_level("user-1") == "approval:user-1:level"

    # Kill switch / controls
    def test_kill_switch(self):
        assert RedisKeys.kill_switch("acct-1") == "kill_switch:acct-1"

    def test_user_pause(self):
        assert RedisKeys.user_pause("acct-1") == "pause:user:acct-1"

    def test_symbol_pause(self):
        assert RedisKeys.symbol_pause("acct-1", "BTCUSDT") == "pause:symbol:acct-1:BTCUSDT"

    def test_symbol_cooldown(self):
        assert RedisKeys.symbol_cooldown("acct-1", "BTCUSDT") == "cooldown:acct-1:BTCUSDT"

    def test_circuit_breaker(self):
        assert RedisKeys.circuit_breaker("acct-1") == "circuit_breaker:acct-1"

    def test_job_lock(self):
        assert RedisKeys.job_lock("job-abc") == "job:lock:job-abc"

    def test_job_status(self):
        assert RedisKeys.job_status("job-abc") == "job:status:job-abc"


class TestKeyUniqueness:
    def test_different_symbols_different_keys(self):
        k1 = RedisKeys.market_price("futures", "BTCUSDT")
        k2 = RedisKeys.market_price("futures", "ETHUSDT")
        assert k1 != k2

    def test_different_market_types_different_keys(self):
        k1 = RedisKeys.market_price("futures", "BTCUSDT")
        k2 = RedisKeys.market_price("spot", "BTCUSDT")
        assert k1 != k2

    def test_different_intervals_different_keys(self):
        k1 = RedisKeys.market_klines("futures", "BTCUSDT", "1m")
        k2 = RedisKeys.market_klines("futures", "BTCUSDT", "1h")
        assert k1 != k2
