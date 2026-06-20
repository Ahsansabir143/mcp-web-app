from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Boolean, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ExecutionJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "execution_jobs"

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exchange_accounts.id"), nullable=False, index=True
    )
    trade_intent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    trading_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    deterministic_client_order_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    intent_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    events: Mapped[list[ExecutionEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class ExecutionEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "execution_events"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution_jobs.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    job: Mapped[ExecutionJob] = relationship(back_populates="events")


class RiskPolicy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "risk_policies"

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exchange_accounts.id", ondelete="CASCADE"),
        nullable=False, unique=True
    )
    max_position_size_usd: Mapped[str] = mapped_column(
        Numeric(20, 4), nullable=False, default="1000"
    )
    max_leverage: Mapped[str] = mapped_column(Numeric(6, 2), nullable=False, default="5")
    max_daily_loss_usd: Mapped[str] = mapped_column(Numeric(20, 4), nullable=False, default="500")
    max_concurrent_positions: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=3
    )
    symbol_cooldown_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=300)
    funding_window_filter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    funding_threshold_pct: Mapped[str] = mapped_column(
        Numeric(8, 4), nullable=False, default="0.1"
    )
    circuit_breaker_threshold: Mapped[str] = mapped_column(
        Numeric(8, 4), nullable=False, default="5.0"
    )
    circuit_breaker_window_seconds: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=3600
    )


class ApprovalLevelRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "approval_levels"

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exchange_accounts.id", ondelete="CASCADE"),
        nullable=False, unique=True
    )
    level: Mapped[str] = mapped_column(String(32), nullable=False, default="l0_readonly")
    allowed_symbols: Mapped[list | None] = mapped_column(JSON)
    denied_symbols: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    paper_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    live_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
