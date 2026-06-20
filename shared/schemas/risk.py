from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from shared.schemas.enums import IncidentSeverity


class IncidentRecord(BaseModel):
    incident_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    incident_type: str
    severity: IncidentSeverity = IncidentSeverity.WARNING
    user_id: str | None = None
    account_id: str | None = None
    strategy_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    description: str
    context: dict[str, Any] = Field(default_factory=dict)
    resolved: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None
    resolved_by: str | None = None


class RiskLimits(BaseModel):
    max_position_size_usd: float = 1000.0
    max_leverage: float = 5.0
    max_daily_loss_usd: float = 500.0
    max_concurrent_positions: int = 3
    symbol_cooldown_seconds: int = 300
    funding_window_filter: bool = True
    funding_threshold_pct: float = 0.1
    circuit_breaker_loss_pct: float = 5.0
    circuit_breaker_window_seconds: int = 3600
    large_trade_usd_threshold: float = 10000.0

