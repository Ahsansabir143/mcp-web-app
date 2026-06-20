from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Symbol(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "symbols"
    __table_args__ = (UniqueConstraint("venue", "market_type", "symbol"),)

    venue: Mapped[str] = mapped_column(String(32), nullable=False)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    base_asset: Mapped[str] = mapped_column(String(16), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="TRADING")
    contract_type: Mapped[str | None] = mapped_column(String(32))
    tick_size: Mapped[str | None] = mapped_column(Numeric(30, 12))
    step_size: Mapped[str | None] = mapped_column(Numeric(30, 12))
    min_qty: Mapped[str | None] = mapped_column(Numeric(30, 12))
    max_qty: Mapped[str | None] = mapped_column(Numeric(30, 12))
    min_notional: Mapped[str | None] = mapped_column(Numeric(30, 12))
    price_precision: Mapped[int | None] = mapped_column(Integer)
    qty_precision: Mapped[int | None] = mapped_column(Integer)
    max_leverage: Mapped[int | None] = mapped_column(Integer)
    raw_info: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("symbol", "market_type", "interval", "open_time_ms"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    open_time_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    close_time_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    open: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    high: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    low: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    close: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    volume: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    quote_volume: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    taker_buy_volume: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    taker_buy_quote_volume: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TradeHistory(Base):
    __tablename__ = "trade_history"
    __table_args__ = (UniqueConstraint("symbol", "market_type", "trade_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    trade_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    qty: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    quote_qty: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    is_buyer_maker: Mapped[bool] = mapped_column(Boolean, nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class FundingHistory(Base):
    __tablename__ = "funding_history"
    __table_args__ = (UniqueConstraint("symbol", "funding_time_ms"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False, default="futures")
    funding_rate: Mapped[str] = mapped_column(Numeric(20, 10), nullable=False)
    funding_time_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mark_price: Mapped[str | None] = mapped_column(Numeric(30, 12))


class OIHistory(Base):
    __tablename__ = "oi_history"
    __table_args__ = (UniqueConstraint("symbol", "timestamp_ms"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False, default="futures")
    open_interest: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    open_interest_value: Mapped[str | None] = mapped_column(Numeric(30, 12))
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class LiquidationEvent(Base):
    __tablename__ = "liquidation_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False, default="futures")
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(32), nullable=False)
    price: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    avg_price: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    orig_qty: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    last_fill_qty: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    last_fill_price: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class WallEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "wall_events"

    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    price: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    size: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    notional_usd: Mapped[str | None] = mapped_column(Numeric(30, 4))
    detected_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    removed_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    is_spoofing_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot_type: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
