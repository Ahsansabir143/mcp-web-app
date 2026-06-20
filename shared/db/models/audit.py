from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class McpSession(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "mcp_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(String(128), nullable=False)
    session_token_hash: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    approval_level: Mapped[str] = mapped_column(String(32), nullable=False, default="l0_readonly")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    tool_calls: Mapped[list[McpToolCall]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class McpToolCall(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "mcp_tool_calls"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mcp_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    input: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[McpSession] = relationship(back_populates="tool_calls")


class AuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_log"

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IncidentLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "incident_log"

    incident_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warning")
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(128))


class AccountUpdateReason(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "account_update_reasons"

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exchange_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
