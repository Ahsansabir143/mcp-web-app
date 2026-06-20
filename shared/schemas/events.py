from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from shared.schemas.enums import EventType, MarketType, Venue


class NormalizedEvent(BaseModel):
    """Canonical envelope for all normalized exchange events."""

    event_type: EventType
    venue: Venue
    market_type: MarketType
    symbol: str
    timestamp_ms: int
    received_ms: int
    sequence: int | None = None
    source_stream: str | None = None
    data: dict[str, Any]
    raw: dict[str, Any] | None = None

    model_config = {"frozen": True}


class RawEvent(BaseModel):
    """Envelope for raw Binance websocket messages before normalization."""

    venue: Venue = Venue.BINANCE
    market_type: MarketType
    source_stream: str
    received_ms: int
    payload: dict[str, Any]

    model_config = {"frozen": True}


class TradeData(BaseModel):
    trade_id: int
    price: str
    qty: str
    quote_qty: str
    is_buyer_maker: bool
    trade_time_ms: int


class AggTradeData(BaseModel):
    agg_trade_id: int
    price: str
    qty: str
    first_trade_id: int
    last_trade_id: int
    trade_time_ms: int
    is_buyer_maker: bool


class BookTickerData(BaseModel):
    bid_price: str
    bid_qty: str
    ask_price: str
    ask_qty: str
    update_id: int | None = None


class OrderbookSnapshotData(BaseModel):
    last_update_id: int
    bids: list[tuple[str, str]]
    asks: list[tuple[str, str]]


class OrderbookDeltaData(BaseModel):
    first_update_id: int
    last_update_id: int
    bids: list[tuple[str, str]]
    asks: list[tuple[str, str]]


class KlineData(BaseModel):
    interval: str
    open_time_ms: int
    close_time_ms: int
    open: str
    high: str
    low: str
    close: str
    volume: str
    quote_volume: str
    trades: int
    taker_buy_volume: str
    taker_buy_quote_volume: str
    is_closed: bool


class MarkPriceData(BaseModel):
    mark_price: str
    index_price: str
    estimated_settle_price: str | None = None
    funding_rate: str
    next_funding_time_ms: int


class LiquidationData(BaseModel):
    side: str
    order_type: str
    time_in_force: str
    orig_qty: str
    price: str
    avg_price: str
    last_fill_qty: str
    last_fill_price: str
    status: str
    order_time_ms: int


class OpenInterestData(BaseModel):
    open_interest: str
    open_interest_value: str | None = None


class UserOrderData(BaseModel):
    client_order_id: str
    exchange_order_id: str
    side: str
    order_type: str
    order_status: str
    price: str
    orig_qty: str
    filled_qty: str
    avg_price: str
    reduce_only: bool
    position_side: str
    stop_price: str
    time_in_force: str
    trade_time_ms: int
    commission: str
    commission_asset: str
    realized_pnl: str
    is_maker: bool


class UserBalanceData(BaseModel):
    asset: str
    wallet_balance: str
    unrealized_pnl: str
    margin_balance: str
    available_balance: str
    cross_wallet_balance: str
    cross_unrealized_pnl: str


class UserPositionData(BaseModel):
    symbol: str
    position_side: str
    position_amt: str
    entry_price: str
    accumulated_realized: str
    unrealized_pnl: str
    margin_type: str
    isolated_wallet: str
    update_time_ms: int
