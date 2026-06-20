from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from shared.schemas.execution import ExecutionRequest


@dataclass
class AdapterResponse:
    """Normalised result returned by any execution adapter."""

    success: bool
    client_order_id: str
    exchange_order_id: str | None = None
    fill_price: Decimal | None = None
    fill_quantity: Decimal | None = None
    commission: Decimal | None = None
    commission_asset: str = "USDT"
    raw_response: dict[str, Any] | None = None
    error: str | None = None


class ExecutionAdapterBase(ABC):
    """Abstract adapter boundary.

    All exchange-specific code must live behind this interface.
    Phase 6 ships the PaperExecutionAdapter only.  Real Binance execution is
    a future adapter that plugs in here without touching any other layer.
    """

    @abstractmethod
    async def submit(
        self,
        request: ExecutionRequest,
        client_order_id: str,
    ) -> AdapterResponse: ...

    @abstractmethod
    def adapter_name(self) -> str: ...
