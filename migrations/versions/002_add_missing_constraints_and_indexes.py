"""Add missing constraints and indexes

Corrects Phase 1 gaps:
- UniqueConstraint(strategy_id, version) on strategy_versions
- UniqueConstraint(account_id, exchange_trade_id) on fills
- Indexes: fills.symbol, fills.exchange_trade_id, execution_jobs.trade_intent_id,
  execution_jobs.status, orders.status, orders(account_id,symbol,status),
  execution_events.event_type, execution_events.timestamp_ms,
  mcp_sessions.session_token_hash, strategy_evaluations.snapshot_timestamp_ms,
  strategy_evaluations.signal, audit_log.action_type, audit_log.resource_type,
  incident_log.resolved, account_update_reasons.reason_code

Revision ID: 002
Revises: 001
Create Date: 2026-06-20
"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── strategy_versions: version must be unique per strategy ─────
    op.create_unique_constraint(
        "uq_strategy_versions_id_ver", "strategy_versions", ["strategy_id", "version"]
    )

    # ── fills: prevent duplicate fill ingestion from stream replay ──
    op.create_unique_constraint(
        "uq_fills_account_trade", "fills", ["account_id", "exchange_trade_id"]
    )
    op.create_index("ix_fills_exchange_trade_id", "fills", ["exchange_trade_id"])
    op.create_index("ix_fills_symbol", "fills", ["symbol"])

    # ── execution_jobs: missing lookup indexes ─────────────────────
    op.create_index("ix_execution_jobs_trade_intent_id", "execution_jobs", ["trade_intent_id"])
    op.create_index("ix_execution_jobs_status", "execution_jobs", ["status"])

    # ── orders: open-order queries ─────────────────────────────────
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index(
        "ix_orders_account_symbol_status", "orders",
        ["account_id", "symbol", "status"]
    )

    # ── execution_events: time-range and type queries ─────────────
    op.create_index("ix_execution_events_event_type", "execution_events", ["event_type"])
    op.create_index("ix_execution_events_timestamp_ms", "execution_events", ["timestamp_ms"])

    # ── mcp_sessions: token lookup on every MCP request ───────────
    op.create_index(
        "ix_mcp_sessions_token_hash", "mcp_sessions", ["session_token_hash"]
    )

    # ── strategy_evaluations: signal lookup and time range ────────
    op.create_index(
        "ix_strategy_evaluations_snapshot_ms",
        "strategy_evaluations", ["snapshot_timestamp_ms"]
    )
    op.create_index("ix_strategy_evaluations_signal", "strategy_evaluations", ["signal"])

    # ── audit_log: filter by action and resource type ─────────────
    op.create_index("ix_audit_log_action_type", "audit_log", ["action_type"])
    op.create_index("ix_audit_log_resource_type", "audit_log", ["resource_type"])

    # ── incident_log: open incident dashboard ─────────────────────
    op.create_index("ix_incident_log_resolved", "incident_log", ["resolved"])

    # ── account_update_reasons: filter by code ────────────────────
    op.create_index(
        "ix_account_update_reasons_reason_code",
        "account_update_reasons", ["reason_code"]
    )


def downgrade() -> None:
    op.drop_index("ix_account_update_reasons_reason_code", "account_update_reasons")
    op.drop_index("ix_incident_log_resolved", "incident_log")
    op.drop_index("ix_audit_log_resource_type", "audit_log")
    op.drop_index("ix_audit_log_action_type", "audit_log")
    op.drop_index("ix_strategy_evaluations_signal", "strategy_evaluations")
    op.drop_index("ix_strategy_evaluations_snapshot_ms", "strategy_evaluations")
    op.drop_index("ix_mcp_sessions_token_hash", "mcp_sessions")
    op.drop_index("ix_execution_events_timestamp_ms", "execution_events")
    op.drop_index("ix_execution_events_event_type", "execution_events")
    op.drop_index("ix_orders_account_symbol_status", "orders")
    op.drop_index("ix_orders_status", "orders")
    op.drop_index("ix_execution_jobs_status", "execution_jobs")
    op.drop_index("ix_execution_jobs_trade_intent_id", "execution_jobs")
    op.drop_index("ix_fills_symbol", "fills")
    op.drop_index("ix_fills_exchange_trade_id", "fills")
    op.drop_unique_constraint("uq_fills_account_trade", "fills")
    op.drop_unique_constraint("uq_strategy_versions_id_ver", "strategy_versions")
