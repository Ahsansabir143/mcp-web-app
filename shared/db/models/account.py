from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="free")

    exchange_accounts: Mapped[list[ExchangeAccount]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ExchangeAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "exchange_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    venue: Mapped[str] = mapped_column(String(32), nullable=False, default="binance")
    account_label: Mapped[str] = mapped_column(String(128), nullable=False, default="default")
    trading_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="paper")
    approval_level: Mapped[str] = mapped_column(String(32), nullable=False, default="l0_readonly")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped[User] = relationship(back_populates="exchange_accounts")
    credential_ref: Mapped[ApiCredentialRef | None] = relationship(
        back_populates="account", cascade="all, delete-orphan", uselist=False
    )
    balances: Mapped[list[Balance]] = relationship(back_populates="account", cascade="all, delete-orphan")
    positions: Mapped[list[Position]] = relationship(back_populates="account", cascade="all, delete-orphan")
    orders: Mapped[list[Order]] = relationship(back_populates="account", cascade="all, delete-orphan")


class ApiCredentialRef(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Stores encrypted API credentials.  Plaintext secrets NEVER persisted here."""

    __tablename__ = "api_credentials_ref"

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exchange_accounts.id", ondelete="CASCADE"),
        nullable=False, unique=True
    )
    credential_type: Mapped[str] = mapped_column(String(32), nullable=False, default="hmac")
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_secret: Mapped[str] = mapped_column(Text, nullable=False)
    iv: Mapped[str] = mapped_column(String(64), nullable=False)

    account: Mapped[ExchangeAccount] = relationship(back_populates="credential_ref")


class Balance(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "balances"
    __table_args__ = (UniqueConstraint("account_id", "asset"),)

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exchange_accounts.id", ondelete="CASCADE"), nullable=False
    )
    asset: Mapped[str] = mapped_column(String(16), nullable=False)
    free: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False, default="0")
    locked: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False, default="0")
    total: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False, default="0")
    wallet_balance: Mapped[str | None] = mapped_column(Numeric(30, 12))
    unrealized_pnl: Mapped[str | None] = mapped_column(Numeric(30, 12))
    updated_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    account: Mapped[ExchangeAccount] = relationship(back_populates="balances")


class Position(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("account_id", "symbol", "market_type", "side"),)

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exchange_accounts.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False, default="BOTH")
    quantity: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False, default="0")
    entry_price: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False, default="0")
    mark_price: Mapped[str | None] = mapped_column(Numeric(30, 12))
    unrealized_pnl: Mapped[str | None] = mapped_column(Numeric(30, 12))
    leverage: Mapped[str | None] = mapped_column(Numeric(10, 2))
    margin: Mapped[str | None] = mapped_column(Numeric(30, 12))
    isolated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    account: Mapped[ExchangeAccount] = relationship(back_populates="positions")


class Order(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (sa.Index("ix_orders_account_symbol_status", "account_id", "symbol", "status"),)

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exchange_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution_jobs.id"), index=True
    )
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="NEW", index=True)
    quantity: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    filled_qty: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False, default="0")
    price: Mapped[str | None] = mapped_column(Numeric(30, 12))
    stop_price: Mapped[str | None] = mapped_column(Numeric(30, 12))
    avg_fill_price: Mapped[str | None] = mapped_column(Numeric(30, 12))
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    time_in_force: Mapped[str] = mapped_column(String(8), nullable=False, default="GTC")
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    account: Mapped[ExchangeAccount] = relationship(back_populates="orders")
    fills: Mapped[list[Fill]] = relationship(back_populates="order", cascade="all, delete-orphan")


class Fill(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "fills"
    __table_args__ = (UniqueConstraint("account_id", "exchange_trade_id", name="uq_fills_account_trade"),)

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exchange_accounts.id"), nullable=False, index=True
    )
    exchange_trade_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    price: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    qty: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    quote_qty: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False)
    commission: Mapped[str] = mapped_column(Numeric(30, 12), nullable=False, default="0")
    commission_asset: Mapped[str] = mapped_column(String(16), nullable=False, default="USDT")
    realized_pnl: Mapped[str | None] = mapped_column(Numeric(30, 12))
    is_maker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    order: Mapped[Order] = relationship(back_populates="fills")
