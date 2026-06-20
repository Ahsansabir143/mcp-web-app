from decimal import Decimal

from pydantic import BaseModel


class RiskLimits(BaseModel):
    max_position_size_usd: Decimal = Decimal("1000")
    max_leverage: Decimal = Decimal("5")
    max_daily_loss_usd: Decimal = Decimal("500")
    max_concurrent_positions: int = 3
    symbol_cooldown_seconds: int = 300
    funding_window_filter: bool = True
    funding_threshold_pct: Decimal = Decimal("0.1")
    circuit_breaker_loss_pct: Decimal = Decimal("5.0")
    circuit_breaker_window_seconds: int = 3600
    large_trade_usd_threshold: Decimal = Decimal("10000")
