"""Initial schema — all tables

Revision ID: 001
Revises:
Create Date: 2026-06-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Market ──────────────────────────────────────────────────────
    op.create_table(
        "symbols",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("venue", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("base_asset", sa.String(16), nullable=False),
        sa.Column("quote_asset", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="TRADING"),
        sa.Column("contract_type", sa.String(32)),
        sa.Column("tick_size", sa.Numeric(30, 12)),
        sa.Column("step_size", sa.Numeric(30, 12)),
        sa.Column("min_qty", sa.Numeric(30, 12)),
        sa.Column("max_qty", sa.Numeric(30, 12)),
        sa.Column("min_notional", sa.Numeric(30, 12)),
        sa.Column("price_precision", sa.Integer()),
        sa.Column("qty_precision", sa.Integer()),
        sa.Column("max_leverage", sa.Integer()),
        sa.Column("raw_info", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("venue", "market_type", "symbol", name="uq_symbols_venue_market_symbol"),
    )

    op.create_table(
        "candles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("open_time_ms", sa.BigInteger(), nullable=False),
        sa.Column("close_time_ms", sa.BigInteger(), nullable=False),
        sa.Column("open", sa.Numeric(30, 12), nullable=False),
        sa.Column("high", sa.Numeric(30, 12), nullable=False),
        sa.Column("low", sa.Numeric(30, 12), nullable=False),
        sa.Column("close", sa.Numeric(30, 12), nullable=False),
        sa.Column("volume", sa.Numeric(30, 12), nullable=False),
        sa.Column("quote_volume", sa.Numeric(30, 12), nullable=False),
        sa.Column("trades", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("taker_buy_volume", sa.Numeric(30, 12), nullable=False),
        sa.Column("taker_buy_quote_volume", sa.Numeric(30, 12), nullable=False),
        sa.Column("is_closed", sa.Boolean(), nullable=False, server_default="true"),
        sa.UniqueConstraint("symbol", "market_type", "interval", "open_time_ms", name="uq_candles"),
    )
    op.create_index("ix_candles_symbol", "candles", ["symbol"])

    op.create_table(
        "trade_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("trade_id", sa.BigInteger(), nullable=False),
        sa.Column("price", sa.Numeric(30, 12), nullable=False),
        sa.Column("qty", sa.Numeric(30, 12), nullable=False),
        sa.Column("quote_qty", sa.Numeric(30, 12), nullable=False),
        sa.Column("is_buyer_maker", sa.Boolean(), nullable=False),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("symbol", "market_type", "trade_id", name="uq_trade_history"),
    )
    op.create_index("ix_trade_history_symbol", "trade_history", ["symbol"])
    op.create_index("ix_trade_history_timestamp_ms", "trade_history", ["timestamp_ms"])

    op.create_table(
        "funding_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False, server_default="futures"),
        sa.Column("funding_rate", sa.Numeric(20, 10), nullable=False),
        sa.Column("funding_time_ms", sa.BigInteger(), nullable=False),
        sa.Column("mark_price", sa.Numeric(30, 12)),
        sa.UniqueConstraint("symbol", "funding_time_ms", name="uq_funding_history"),
    )
    op.create_index("ix_funding_history_symbol", "funding_history", ["symbol"])

    op.create_table(
        "oi_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False, server_default="futures"),
        sa.Column("open_interest", sa.Numeric(30, 12), nullable=False),
        sa.Column("open_interest_value", sa.Numeric(30, 12)),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("symbol", "timestamp_ms", name="uq_oi_history"),
    )
    op.create_index("ix_oi_history_symbol", "oi_history", ["symbol"])
    op.create_index("ix_oi_history_timestamp_ms", "oi_history", ["timestamp_ms"])

    op.create_table(
        "liquidation_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False, server_default="futures"),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("order_type", sa.String(32), nullable=False),
        sa.Column("price", sa.Numeric(30, 12), nullable=False),
        sa.Column("avg_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("orig_qty", sa.Numeric(30, 12), nullable=False),
        sa.Column("last_fill_qty", sa.Numeric(30, 12), nullable=False),
        sa.Column("last_fill_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_liquidation_events_symbol", "liquidation_events", ["symbol"])
    op.create_index("ix_liquidation_events_timestamp_ms", "liquidation_events", ["timestamp_ms"])

    op.create_table(
        "wall_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("price", sa.Numeric(30, 12), nullable=False),
        sa.Column("size", sa.Numeric(30, 12), nullable=False),
        sa.Column("notional_usd", sa.Numeric(30, 4)),
        sa.Column("detected_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("removed_at_ms", sa.BigInteger()),
        sa.Column("is_spoofing_candidate", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_wall_events_symbol", "wall_events", ["symbol"])
    op.create_index("ix_wall_events_detected_at_ms", "wall_events", ["detected_at_ms"])

    op.create_table(
        "market_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("snapshot_type", sa.String(64), nullable=False),
        sa.Column("data", postgresql.JSON(), nullable=False),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_market_snapshots_symbol", "market_snapshots", ["symbol"])
    op.create_index("ix_market_snapshots_timestamp_ms", "market_snapshots", ["timestamp_ms"])

    # ── Users & accounts ────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(256), nullable=False, unique=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(256), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("plan", sa.String(32), nullable=False, server_default="free"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "exchange_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("venue", sa.String(32), nullable=False, server_default="binance"),
        sa.Column("account_label", sa.String(128), nullable=False, server_default="default"),
        sa.Column("trading_mode", sa.String(16), nullable=False, server_default="paper"),
        sa.Column("approval_level", sa.String(32), nullable=False, server_default="l0_readonly"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_exchange_accounts_user_id", "exchange_accounts", ["user_id"])

    op.create_table(
        "api_credentials_ref",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("exchange_accounts.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("credential_type", sa.String(32), nullable=False, server_default="hmac"),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("iv", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "balances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("exchange_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset", sa.String(16), nullable=False),
        sa.Column("free", sa.Numeric(30, 12), nullable=False, server_default="0"),
        sa.Column("locked", sa.Numeric(30, 12), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(30, 12), nullable=False, server_default="0"),
        sa.Column("wallet_balance", sa.Numeric(30, 12)),
        sa.Column("unrealized_pnl", sa.Numeric(30, 12)),
        sa.Column("updated_at_ms", sa.BigInteger(), nullable=False, server_default="0"),
        sa.UniqueConstraint("account_id", "asset", name="uq_balances_account_asset"),
    )

    op.create_table(
        "positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("exchange_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False, server_default="BOTH"),
        sa.Column("quantity", sa.Numeric(30, 12), nullable=False, server_default="0"),
        sa.Column("entry_price", sa.Numeric(30, 12), nullable=False, server_default="0"),
        sa.Column("mark_price", sa.Numeric(30, 12)),
        sa.Column("unrealized_pnl", sa.Numeric(30, 12)),
        sa.Column("leverage", sa.Numeric(10, 2)),
        sa.Column("margin", sa.Numeric(30, 12)),
        sa.Column("isolated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("updated_at_ms", sa.BigInteger(), nullable=False, server_default="0"),
        sa.UniqueConstraint("account_id", "symbol", "market_type", "side", name="uq_positions"),
    )

    # ── Execution ──────────────────────────────────────────────────
    op.create_table(
        "execution_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("exchange_accounts.id"), nullable=False),
        sa.Column("trade_intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True)),
        sa.Column("trading_mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("deterministic_client_order_id", sa.String(64), nullable=False, unique=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("intent_json", postgresql.JSON(), nullable=False),
        sa.Column("result_json", postgresql.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_execution_jobs_account_id", "execution_jobs", ["account_id"])
    op.create_index("ix_execution_jobs_strategy_id", "execution_jobs", ["strategy_id"])

    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("exchange_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("execution_jobs.id")),
        sa.Column("exchange_order_id", sa.String(64)),
        sa.Column("client_order_id", sa.String(64), nullable=False, unique=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("order_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="NEW"),
        sa.Column("quantity", sa.Numeric(30, 12), nullable=False),
        sa.Column("filled_qty", sa.Numeric(30, 12), nullable=False, server_default="0"),
        sa.Column("price", sa.Numeric(30, 12)),
        sa.Column("stop_price", sa.Numeric(30, 12)),
        sa.Column("avg_fill_price", sa.Numeric(30, 12)),
        sa.Column("reduce_only", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("time_in_force", sa.String(8), nullable=False, server_default="GTC"),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at_ms", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_orders_account_id", "orders", ["account_id"])
    op.create_index("ix_orders_job_id", "orders", ["job_id"])
    op.create_index("ix_orders_exchange_order_id", "orders", ["exchange_order_id"])
    op.create_index("ix_orders_symbol", "orders", ["symbol"])

    op.create_table(
        "fills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("exchange_accounts.id"), nullable=False),
        sa.Column("exchange_trade_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("price", sa.Numeric(30, 12), nullable=False),
        sa.Column("qty", sa.Numeric(30, 12), nullable=False),
        sa.Column("quote_qty", sa.Numeric(30, 12), nullable=False),
        sa.Column("commission", sa.Numeric(30, 12), nullable=False, server_default="0"),
        sa.Column("commission_asset", sa.String(16), nullable=False, server_default="USDT"),
        sa.Column("realized_pnl", sa.Numeric(30, 12)),
        sa.Column("is_maker", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_fills_order_id", "fills", ["order_id"])
    op.create_index("ix_fills_account_id", "fills", ["account_id"])
    op.create_index("ix_fills_timestamp_ms", "fills", ["timestamp_ms"])

    op.create_table(
        "execution_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("execution_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("data", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_execution_events_job_id", "execution_events", ["job_id"])

    op.create_table(
        "risk_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("exchange_accounts.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("max_position_size_usd", sa.Numeric(20, 4), nullable=False, server_default="1000"),
        sa.Column("max_leverage", sa.Numeric(6, 2), nullable=False, server_default="5"),
        sa.Column("max_daily_loss_usd", sa.Numeric(20, 4), nullable=False, server_default="500"),
        sa.Column("max_concurrent_positions", sa.BigInteger(), nullable=False, server_default="3"),
        sa.Column("symbol_cooldown_seconds", sa.BigInteger(), nullable=False, server_default="300"),
        sa.Column("funding_window_filter", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("funding_threshold_pct", sa.Numeric(8, 4), nullable=False, server_default="0.1"),
        sa.Column("circuit_breaker_threshold", sa.Numeric(8, 4), nullable=False, server_default="5.0"),
        sa.Column("circuit_breaker_window_seconds", sa.BigInteger(), nullable=False, server_default="3600"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "approval_levels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("exchange_accounts.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("level", sa.String(32), nullable=False, server_default="l0_readonly"),
        sa.Column("allowed_symbols", postgresql.JSON()),
        sa.Column("denied_symbols", postgresql.JSON(), nullable=False, server_default="[]"),
        sa.Column("paper_only", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("live_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # ── Strategy ─────────────────────────────────────────────────────
    op.create_table(
        "strategies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("symbol_filters", postgresql.JSON(), nullable=False, server_default="[]"),
        sa.Column("state", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_strategies_user_id", "strategies", ["user_id"])

    op.create_table(
        "strategy_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rules", postgresql.JSON(), nullable=False, server_default="[]"),
        sa.Column("parameters", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("approval_required", sa.String(32), nullable=False, server_default="l1_simulation"),
        sa.Column("change_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index("ix_strategy_versions_strategy_id", "strategy_versions", ["strategy_id"])

    op.create_table(
        "strategy_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("run_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("start_ms", sa.BigInteger()),
        sa.Column("end_ms", sa.BigInteger()),
        sa.Column("stats", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_strategy_runs_strategy_id", "strategy_runs", ["strategy_id"])

    op.create_table(
        "strategy_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("strategy_runs.id", ondelete="SET NULL")),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("snapshot_timestamp_ms", sa.BigInteger(), nullable=False),
        sa.Column("signal", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("direction", sa.String(8)),
        sa.Column("confidence", sa.String(8)),
        sa.Column("explanation", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index("ix_strategy_evaluations_strategy_id", "strategy_evaluations", ["strategy_id"])
    op.create_index("ix_strategy_evaluations_run_id", "strategy_evaluations", ["run_id"])

    op.create_table(
        "strategy_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("triggered_by", sa.String(128), nullable=False),
        sa.Column("details", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index("ix_strategy_actions_strategy_id", "strategy_actions", ["strategy_id"])

    op.create_table(
        "strategy_rollbacks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_version", sa.Integer(), nullable=False),
        sa.Column("to_version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("rolled_back_by", sa.String(128), nullable=False),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index("ix_strategy_rollbacks_strategy_id", "strategy_rollbacks", ["strategy_id"])

    # ── MCP & Audit ───────────────────────────────────────────────────
    op.create_table(
        "mcp_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(128), nullable=False),
        sa.Column("session_token_hash", sa.String(256), nullable=False),
        sa.Column("approval_level", sa.String(32), nullable=False, server_default="l0_readonly"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mcp_sessions_user_id", "mcp_sessions", ["user_id"])

    op.create_table(
        "mcp_tool_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("mcp_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("input", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("output", postgresql.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_mcp_tool_calls_session_id", "mcp_tool_calls", ["session_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(128)),
        sa.Column("details", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])

    op.create_table(
        "incident_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("incident_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True)),
        sa.Column("job_id", postgresql.UUID(as_uuid=True)),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("context", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by", sa.String(128)),
    )
    op.create_index("ix_incident_log_incident_type", "incident_log", ["incident_type"])
    op.create_index("ix_incident_log_user_id", "incident_log", ["user_id"])

    op.create_table(
        "account_update_reasons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("exchange_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("details", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_account_update_reasons_account_id", "account_update_reasons", ["account_id"])
    op.create_index("ix_account_update_reasons_timestamp_ms", "account_update_reasons", ["timestamp_ms"])


def downgrade() -> None:
    op.drop_table("account_update_reasons")
    op.drop_table("incident_log")
    op.drop_table("audit_log")
    op.drop_table("mcp_tool_calls")
    op.drop_table("mcp_sessions")
    op.drop_table("strategy_rollbacks")
    op.drop_table("strategy_actions")
    op.drop_table("strategy_evaluations")
    op.drop_table("strategy_runs")
    op.drop_table("strategy_versions")
    op.drop_table("strategies")
    op.drop_table("approval_levels")
    op.drop_table("risk_policies")
    op.drop_table("execution_events")
    op.drop_table("fills")
    op.drop_table("orders")
    op.drop_table("execution_jobs")
    op.drop_table("positions")
    op.drop_table("balances")
    op.drop_table("api_credentials_ref")
    op.drop_table("exchange_accounts")
    op.drop_table("users")
    op.drop_table("market_snapshots")
    op.drop_table("wall_events")
    op.drop_table("liquidation_events")
    op.drop_table("oi_history")
    op.drop_table("funding_history")
    op.drop_table("trade_history")
    op.drop_table("candles")
    op.drop_table("symbols")
