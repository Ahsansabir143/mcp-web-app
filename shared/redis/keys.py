"""
All Redis key builders.  Every service must import from here — never
construct Redis key strings inline in service code.
"""


class RedisKeys:
    # ── Market ────────────────────────────────────────────────

    @staticmethod
    def market_price(market_type: str, symbol: str) -> str:
        return f"market:{market_type}:{symbol}:price"

    @staticmethod
    def market_ticker24h(market_type: str, symbol: str) -> str:
        return f"market:{market_type}:{symbol}:ticker24h"

    @staticmethod
    def market_book(market_type: str, symbol: str) -> str:
        return f"market:{market_type}:{symbol}:book"

    @staticmethod
    def market_book_ticker(market_type: str, symbol: str) -> str:
        return f"market:{market_type}:{symbol}:book_ticker"

    @staticmethod
    def market_mark(market_type: str, symbol: str) -> str:
        return f"market:{market_type}:{symbol}:mark"

    @staticmethod
    def market_funding(market_type: str, symbol: str) -> str:
        return f"market:{market_type}:{symbol}:funding"

    @staticmethod
    def market_oi(market_type: str, symbol: str) -> str:
        return f"market:{market_type}:{symbol}:oi"

    @staticmethod
    def market_contract_info(market_type: str, symbol: str) -> str:
        return f"market:{market_type}:{symbol}:contract_info"

    @staticmethod
    def market_composite_index(market_type: str, symbol: str) -> str:
        return f"market:{market_type}:{symbol}:composite_index"

    @staticmethod
    def market_asset_index(market_type: str, symbol: str) -> str:
        return f"market:{market_type}:{symbol}:asset_index"

    @staticmethod
    def market_klines(market_type: str, symbol: str, interval: str) -> str:
        return f"market:{market_type}:{symbol}:klines:{interval}"

    @staticmethod
    def market_continuous_klines(market_type: str, symbol: str, interval: str) -> str:
        return f"market:{market_type}:{symbol}:continuous_klines:{interval}"

    # ── Analytics ────────────────────────────────────────────

    @staticmethod
    def analytics_score(market_type: str, symbol: str) -> str:
        return f"analytics:{market_type}:{symbol}:score"

    @staticmethod
    def analytics_walls(market_type: str, symbol: str) -> str:
        return f"analytics:{market_type}:{symbol}:walls"

    @staticmethod
    def analytics_cvd(market_type: str, symbol: str) -> str:
        return f"analytics:{market_type}:{symbol}:cvd"

    @staticmethod
    def analytics_delta(market_type: str, symbol: str) -> str:
        return f"analytics:{market_type}:{symbol}:delta"

    @staticmethod
    def analytics_rvol(market_type: str, symbol: str) -> str:
        return f"analytics:{market_type}:{symbol}:rvol"

    @staticmethod
    def analytics_liquidation_clusters(market_type: str, symbol: str) -> str:
        return f"analytics:{market_type}:{symbol}:liquidation_clusters"

    @staticmethod
    def analytics_funding_pressure(market_type: str, symbol: str) -> str:
        return f"analytics:{market_type}:{symbol}:funding_pressure"

    @staticmethod
    def analytics_indicators(market_type: str, symbol: str, interval: str) -> str:
        return f"analytics:{market_type}:{symbol}:indicators:{interval}"

    @staticmethod
    def analytics_snapshot(market_type: str, symbol: str) -> str:
        return f"analytics:{market_type}:{symbol}:snapshot"

    # ── Account ───────────────────────────────────────────────

    @staticmethod
    def account_snapshot(user_id: str) -> str:
        return f"account:{user_id}:snapshot"

    @staticmethod
    def account_balances(user_id: str) -> str:
        return f"account:{user_id}:balances"

    @staticmethod
    def account_positions(user_id: str) -> str:
        return f"account:{user_id}:positions"

    @staticmethod
    def account_open_orders(user_id: str) -> str:
        return f"account:{user_id}:open_orders"

    # ── Risk ─────────────────────────────────────────────────

    @staticmethod
    def risk_state(user_id: str) -> str:
        return f"risk:{user_id}:state"

    @staticmethod
    def risk_limits(user_id: str) -> str:
        return f"risk:{user_id}:limits"

    # ── Strategy ─────────────────────────────────────────────

    @staticmethod
    def strategy_active(strategy_id: str) -> str:
        return f"strategy:{strategy_id}:active"

    @staticmethod
    def strategy_version(strategy_id: str, version: int) -> str:
        return f"strategy:{strategy_id}:version:{version}"

    # ── Approval ─────────────────────────────────────────────

    @staticmethod
    def approval_level(user_id: str) -> str:
        return f"approval:{user_id}:level"

    # ── Kill switch / circuit breaker ────────────────────────

    @staticmethod
    def kill_switch(account_id: str) -> str:
        return f"kill_switch:{account_id}"

    @staticmethod
    def user_pause(account_id: str) -> str:
        return f"pause:user:{account_id}"

    @staticmethod
    def symbol_pause(account_id: str, symbol: str) -> str:
        return f"pause:symbol:{account_id}:{symbol}"

    @staticmethod
    def symbol_cooldown(account_id: str, symbol: str) -> str:
        return f"cooldown:{account_id}:{symbol}"

    @staticmethod
    def circuit_breaker(account_id: str) -> str:
        return f"circuit_breaker:{account_id}"

    # ── Execution idempotency ────────────────────────────────

    @staticmethod
    def job_lock(job_id: str) -> str:
        return f"job:lock:{job_id}"

    @staticmethod
    def job_status(job_id: str) -> str:
        return f"job:status:{job_id}"

    # ── MCP sessions ─────────────────────────────────────────

    @staticmethod
    def mcp_session(session_id: str) -> str:
        return f"mcp:session:{session_id}"

    # ── Global ops controls ──────────────────────────────────

    @staticmethod
    def global_trading_mode() -> str:
        return "global:trading_mode"

    @staticmethod
    def global_emergency_stop() -> str:
        return "global:emergency_stop"
