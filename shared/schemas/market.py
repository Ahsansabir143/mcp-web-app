from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from shared.schemas.enums import MarketType, Venue


class SymbolInfo(BaseModel):
    venue: Venue
    market_type: MarketType
    symbol: str
    base_asset: str
    quote_asset: str
    status: str
    contract_type: str | None = None
    tick_size: Decimal | None = None
    step_size: Decimal | None = None
    min_qty: Decimal | None = None
    max_qty: Decimal | None = None
    min_notional: Decimal | None = None
    price_precision: int | None = None
    qty_precision: int | None = None
    max_leverage: int | None = None
    raw_info: dict[str, Any] = Field(default_factory=dict)


class CandleData(BaseModel):
    symbol: str
    market_type: MarketType
    interval: str
    open_time_ms: int
    close_time_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trades: int
    taker_buy_volume: Decimal
    taker_buy_quote_volume: Decimal
    is_closed: bool


class FundingRateData(BaseModel):
    symbol: str
    funding_rate: Decimal
    funding_time_ms: int
    mark_price: Decimal | None = None


class OpenInterestRecord(BaseModel):
    symbol: str
    open_interest: Decimal
    open_interest_value: Decimal | None = None
    timestamp_ms: int
