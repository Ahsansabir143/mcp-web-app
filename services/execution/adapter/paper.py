from __future__ import annotations

import hashlib
from decimal import Decimal

from shared.schemas.execution import ExecutionRequest
from services.execution.adapter.base import AdapterResponse, ExecutionAdapterBase

_PAPER_COMMISSION_RATE = Decimal("0.0004")  # 4 bps simulated maker fee


class PaperExecutionAdapter(ExecutionAdapterBase):
    """Deterministic paper execution adapter.

    Produces an immediate synthetic fill at the intent's limit_price (or
    size_usd/size estimate if limit_price is absent).  The exchange_order_id is
    derived deterministically from client_order_id so replay produces identical
    identifiers, enabling idempotent fill processing in tests and simulations.

    No network calls, no credentials, no Binance code.
    """

    def adapter_name(self) -> str:
        return "paper"

    async def submit(
        self,
        request: ExecutionRequest,
        client_order_id: str,
    ) -> AdapterResponse:
        intent = request.trade_intent

        # Deterministic fake exchange order ID: reproducible across replays
        raw = f"paper:{client_order_id}"
        fake_oid = "PAPER-" + hashlib.sha256(raw.encode()).hexdigest()[:16].upper()

        # Fill price: prefer limit_price, else derive from notional / size
        fill_price = intent.limit_price
        if fill_price is None and intent.size_usd and intent.size > 0:
            fill_price = (intent.size_usd / intent.size).quantize(Decimal("0.01"))
        if fill_price is None:
            fill_price = Decimal("0")

        fill_qty = intent.size
        commission = (fill_qty * fill_price * _PAPER_COMMISSION_RATE).quantize(
            Decimal("0.00000001")
        )

        return AdapterResponse(
            success=True,
            client_order_id=client_order_id,
            exchange_order_id=fake_oid,
            fill_price=fill_price,
            fill_quantity=fill_qty,
            commission=commission,
            commission_asset="USDT",
            raw_response={
                "orderId": fake_oid,
                "clientOrderId": client_order_id,
                "status": "FILLED",
                "executedQty": str(fill_qty),
                "avgPrice": str(fill_price),
                "mode": "paper",
            },
        )
