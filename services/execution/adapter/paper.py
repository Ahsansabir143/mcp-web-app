from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from shared.redis.keys import RedisKeys
from shared.schemas.execution import ExecutionRequest
from shared.schemas.strategy import TradeIntent
from services.execution.adapter.base import AdapterResponse, ExecutionAdapterBase

_PAPER_COMMISSION_RATE = Decimal("0.0004")  # 4 bps simulated maker fee


class PaperExecutionAdapter(ExecutionAdapterBase):
    """Deterministic paper execution adapter.

    Fill-price resolution order:
      1. intent.limit_price  — LIMIT orders fill exactly here
      2. Redis market:{market_type}:{symbol}:price  — current last-trade price (TTL 60s)
      3. Redis market:{market_type}:{symbol}:book_ticker mid  — (bid+ask)/2 (TTL 10s)
      4. Redis analytics:{market_type}:{symbol}:snapshot → market_state.price
             (same source as get_symbol_snapshot last_price; longer TTL from analytics svc)
      5. size_usd / size derivation  — last resort when Redis is unavailable
      6. Failure (success=False)  — never fill at Decimal("0")

    Steps 2-4 use the same canonical key order as the MCP get_symbol_snapshot facade so
    that a price visible in the snapshot is always reachable by the adapter.

    No network calls to any exchange; purely deterministic for replay.
    Accepts an optional redis client so callers can inject it; when None the
    adapter still works for LIMIT orders and size-derived fills (e.g. tests).
    """

    def __init__(self, redis=None) -> None:
        self._redis = redis

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

        fill_price = await self._resolve_fill_price(intent)
        if fill_price is None or fill_price <= 0:
            return AdapterResponse(
                success=False,
                client_order_id=client_order_id,
                exchange_order_id=None,
                error=(
                    "paper_price_unavailable: no limit_price, Redis has no current "
                    f"price for {intent.symbol}/{intent.market_type.value}, "
                    "and size_usd/size derivation is not possible"
                ),
            )

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

    # ── Price resolution ──────────────────────────────────────────────────────

    async def _resolve_fill_price(self, intent: TradeIntent) -> Decimal | None:
        """Return fill price in decreasing priority order."""
        # 1. Limit price — always authoritative for LIMIT orders
        if intent.limit_price is not None:
            return intent.limit_price

        mtype = intent.market_type.value
        symbol = intent.symbol

        # 2. Redis last-trade price
        if self._redis is not None:
            price = await self._redis_price(mtype, symbol)
            if price is not None:
                return price.quantize(Decimal("0.01"))

        # 3. Derivation from notional (size_usd / size) — works when both set
        if intent.size_usd is not None and intent.size > 0:
            return (intent.size_usd / intent.size).quantize(Decimal("0.01"))

        return None

    async def _redis_price(self, market_type: str, symbol: str) -> Decimal | None:
        """Try canonical price sources in priority order; return None if all absent.

        Order matches get_symbol_snapshot so any price visible in the snapshot
        is reachable by the adapter:
          1. market:{market_type}:{symbol}:price          (60 s TTL)
          2. market:{market_type}:{symbol}:book_ticker    (10 s TTL, mid)
          3. analytics:{market_type}:{symbol}:snapshot    (longer TTL, market_state.price)
        """
        raw = await self._redis.get(RedisKeys.market_price(market_type, symbol))
        if raw:
            try:
                data = json.loads(raw)
                price_str = data.get("price")
                if price_str and str(price_str) not in ("", "0"):
                    return Decimal(str(price_str))
            except Exception:
                pass

        book_raw = await self._redis.get(RedisKeys.market_book_ticker(market_type, symbol))
        if book_raw:
            try:
                book = json.loads(book_raw)
                bid = book.get("bid_price")
                ask = book.get("ask_price")
                if bid and ask and str(bid) not in ("", "0") and str(ask) not in ("", "0"):
                    return (Decimal(str(bid)) + Decimal(str(ask))) / 2
            except Exception:
                pass

        analytics_raw = await self._redis.get(
            RedisKeys.analytics_snapshot(market_type, symbol)
        )
        if analytics_raw:
            try:
                snapshot = json.loads(analytics_raw)
                ms = snapshot.get("market_state") or {}
                p = ms.get("price")
                if p and str(p) not in ("", "0"):
                    return Decimal(str(p))
            except Exception:
                pass

        return None
