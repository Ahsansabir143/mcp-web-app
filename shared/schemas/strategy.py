from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from shared.schemas.enums import (
    ApprovalLevel,
    MarketType,
    OrderSide,
    OrderType,
    StrategyState,
    TimeInForce,
)


class StrategyDefinition(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    user_id: str
    name: str
    description: str = ""
    market_type: MarketType
    symbol_filters: list[str] = Field(default_factory=list)
    state: StrategyState = StrategyState.DRAFT
    current_version: int = 1
    approval_required: ApprovalLevel = ApprovalLevel.L1_SIMULATION
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StrategyVersion(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    strategy_id: uuid.UUID
    version: int
    rules: list[dict[str, Any]]
    parameters: dict[str, Any] = Field(default_factory=dict)
    approval_required: ApprovalLevel = ApprovalLevel.L1_SIMULATION
    change_note: str = ""
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TradeIntent(BaseModel):
    """Output of strategy evaluation â€” an intent to trade, not an order."""

    intent_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    strategy_id: uuid.UUID | None = None
    strategy_version: int | None = None
    symbol: str
    market_type: MarketType
    side: OrderSide
    intent_type: str = "OPEN"
    order_type: OrderType = OrderType.MARKET
    size: Decimal
    size_usd: Decimal | None = None
    limit_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    reduce_only: bool = False
    time_in_force: TimeInForce = TimeInForce.GTC
    position_side: str | None = None
    explanation: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StrategyEvaluation(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    run_id: uuid.UUID | None = None
    strategy_id: uuid.UUID
    version: int
    symbol: str
    market_type: MarketType
    snapshot_timestamp_ms: int
    signal: bool
    direction: str | None = None
    confidence: float | None = None
    explanation: dict[str, Any] = Field(default_factory=dict)
    trade_intent: TradeIntent | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StrategyRunConfig(BaseModel):
    strategy_id: uuid.UUID
    version: int
    run_type: str
    symbols: list[str]
    start_ms: int | None = None
    end_ms: int | None = None
    parameters_override: dict[str, Any] = Field(default_factory=dict)

