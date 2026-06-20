from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from shared.schemas.enums import ApprovalLevel, RiskCheckName, TradingMode
from shared.schemas.strategy import TradeIntent


class ExecutionRequest(BaseModel):
    request_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    job_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    trade_intent: TradeIntent
    user_id: str
    account_id: str
    trading_mode: TradingMode
    approval_level: ApprovalLevel
    source: str = "strategy"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExecutionResult(BaseModel):
    job_id: uuid.UUID
    request_id: uuid.UUID
    success: bool
    trading_mode: TradingMode
    order_id: str | None = None
    client_order_id: str | None = None
    exchange_order_id: str | None = None
    fill_quantity: Decimal | None = None
    fill_price: Decimal | None = None
    commission: Decimal | None = None
    commission_asset: str | None = None
    error: str | None = None
    audit_ref: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


class RiskDecision(BaseModel):
    passed: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalPolicy(BaseModel):
    user_id: str
    account_id: str
    level: ApprovalLevel
    allowed_symbols: list[str] | None = None
    denied_symbols: list[str] = Field(default_factory=list)
    max_position_size_usd: Decimal = Decimal("1000")
    max_daily_loss_usd: Decimal = Decimal("500")
    max_leverage: float = 5.0
    max_concurrent_positions: int = 3
    paper_only: bool = True
    live_enabled: bool = False
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CancelOrderRequest(BaseModel):
    account_id: str
    symbol: str
    market_type: str
    order_id: str | None = None
    client_order_id: str | None = None
    reason: str = ""


class ClosePositionRequest(BaseModel):
    account_id: str
    symbol: str
    market_type: str
    position_side: str = "BOTH"
    reduce_only: bool = True
    order_type: str = "MARKET"
    reason: str = ""


class KillSwitchRequest(BaseModel):
    account_id: str
    cancel_all_orders: bool = True
    close_all_positions: bool = False
    reason: str
    triggered_by: str

