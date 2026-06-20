from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from shared.schemas.enums import MarketType


class PriceLevel(BaseModel):
    price: Decimal
    size: Decimal
    notional_usd: Decimal | None = None


class WallLevel(PriceLevel):
    is_spoofing_candidate: bool = False
    detected_at_ms: int | None = None


class MarketState(BaseModel):
    price: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    spread: Decimal | None = None
    spread_bps: Decimal | None = None
    high_24h: Decimal | None = None
    low_24h: Decimal | None = None
    volume_24h: Decimal | None = None
    quote_volume_24h: Decimal | None = None
    price_change_pct_24h: Decimal | None = None
    trades_24h: int | None = None


class BookState(BaseModel):
    imbalance_ratio: Decimal | None = None
    bid_depth_usd: Decimal | None = None
    ask_depth_usd: Decimal | None = None
    top_bid_walls: list[WallLevel] = Field(default_factory=list)
    top_ask_walls: list[WallLevel] = Field(default_factory=list)
    spoofing_alert: bool = False


class FlowState(BaseModel):
    delta: Decimal | None = None
    cvd: Decimal | None = None
    cvd_slope: Decimal | None = None
    tape_speed_trades_per_min: Decimal | None = None
    aggression_ratio: Decimal | None = None
    rvol: Decimal | None = None
    buy_volume: Decimal | None = None
    sell_volume: Decimal | None = None
    large_trade_threshold_usd: Decimal | None = None
    large_buy_count: int | None = None
    large_sell_count: int | None = None


class FuturesState(BaseModel):
    mark_price: Decimal | None = None
    index_price: Decimal | None = None
    funding_rate: Decimal | None = None
    next_funding_time_ms: int | None = None
    open_interest: Decimal | None = None
    open_interest_value_usd: Decimal | None = None
    oi_price_divergence: Decimal | None = None
    funding_pressure_score: Decimal | None = None
    liquidation_cluster_long_usd: Decimal | None = None
    liquidation_cluster_short_usd: Decimal | None = None


class IndicatorValues(BaseModel):
    ema_9: Decimal | None = None
    ema_21: Decimal | None = None
    ema_50: Decimal | None = None
    ema_200: Decimal | None = None
    rsi_14: Decimal | None = None
    vwap: Decimal | None = None
    bb_upper: Decimal | None = None
    bb_middle: Decimal | None = None
    bb_lower: Decimal | None = None
    bb_width: Decimal | None = None
    atr_14: Decimal | None = None


class IndicatorState(BaseModel):
    by_interval: dict[str, IndicatorValues] = Field(default_factory=dict)


class BalanceSnapshot(BaseModel):
    asset: str
    free: Decimal
    locked: Decimal
    total: Decimal
    wallet_balance: Decimal | None = None
    unrealized_pnl: Decimal | None = None


class PositionSnapshot(BaseModel):
    symbol: str
    market_type: MarketType
    side: str
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    leverage: Decimal | None = None
    margin: Decimal | None = None
    liquidation_price: Decimal | None = None


class AccountState(BaseModel):
    total_equity_usd: Decimal | None = None
    available_margin_usd: Decimal | None = None
    used_margin_usd: Decimal | None = None
    unrealized_pnl_usd: Decimal | None = None
    balances: list[BalanceSnapshot] = Field(default_factory=list)
    positions: list[PositionSnapshot] = Field(default_factory=list)
    open_orders_count: int = 0
    trading_mode: str | None = None


class RiskState(BaseModel):
    daily_pnl_usd: Decimal | None = None
    daily_loss_limit_pct_used: Decimal | None = None
    open_positions_count: int = 0
    max_concurrent_positions: int | None = None
    kill_switch_active: bool = False
    circuit_breaker_active: bool = False
    user_paused: bool = False
    symbol_paused: bool = False
    daily_trades: int = 0


class StrategySignal(BaseModel):
    strategy_id: str
    version: int
    signal: bool
    direction: str | None = None
    confidence: float | None = None


class StrategyStateSnapshot(BaseModel):
    active_strategy_ids: list[str] = Field(default_factory=list)
    last_signals: list[StrategySignal] = Field(default_factory=list)
    pending_intents: int = 0


class RecentFill(BaseModel):
    job_id: str
    symbol: str
    side: str
    qty: Decimal
    price: Decimal
    timestamp_ms: int


class ExecutionStateSnapshot(BaseModel):
    pending_jobs: int = 0
    recent_fills: list[RecentFill] = Field(default_factory=list)
    last_error: str | None = None


class SnapshotMeta(BaseModel):
    snapshot_timestamp_ms: int
    symbol: str
    market_type: MarketType
    account_id: str | None = None
    sources: list[str] = Field(default_factory=list)
    staleness_ms: dict[str, int] = Field(default_factory=dict)


class UnifiedDecisionSnapshot(BaseModel):
    """Single coherent view of market, account, and strategy state for strategy evaluation."""

    market_state: MarketState = Field(default_factory=MarketState)
    book_state: BookState = Field(default_factory=BookState)
    flow_state: FlowState = Field(default_factory=FlowState)
    futures_state: FuturesState = Field(default_factory=FuturesState)
    indicator_state: IndicatorState = Field(default_factory=IndicatorState)
    account_state: AccountState = Field(default_factory=AccountState)
    risk_state: RiskState = Field(default_factory=RiskState)
    strategy_state: StrategyStateSnapshot = Field(default_factory=StrategyStateSnapshot)
    execution_state: ExecutionStateSnapshot = Field(default_factory=ExecutionStateSnapshot)
    meta: SnapshotMeta

    model_config = {"frozen": False}
