from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Strategy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "strategies"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol_filters: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    versions: Mapped[list[StrategyVersion]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan"
    )
    runs: Mapped[list[StrategyRun]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan"
    )


class StrategyVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_strategy_versions_id_ver"),)

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategies.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    rules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    approval_required: Mapped[str] = mapped_column(String(32), nullable=False, default="l1_simulation")
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    strategy: Mapped[Strategy] = relationship(back_populates="versions")


class StrategyRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "strategy_runs"

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategies.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    run_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    start_ms: Mapped[int | None] = mapped_column(BigInteger)
    end_ms: Mapped[int | None] = mapped_column(BigInteger)
    stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    strategy: Mapped[Strategy] = relationship(back_populates="runs")
    evaluations: Mapped[list[StrategyEvaluation]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class StrategyEvaluation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "strategy_evaluations"

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategy_runs.id", ondelete="SET NULL"), index=True
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot_timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    signal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    direction: Mapped[str | None] = mapped_column(String(8))
    confidence: Mapped[str | None] = mapped_column(String(8))
    explanation: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    intent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    run: Mapped[StrategyRun | None] = relationship(back_populates="evaluations")


class StrategyAction(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "strategy_actions"

    strategy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class StrategyRollback(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "strategy_rollbacks"

    strategy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    from_version: Mapped[int] = mapped_column(Integer, nullable=False)
    to_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rolled_back_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
