from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from shared.redis.keys import RedisKeys
from shared.schemas.execution import ExecutionRequest
from shared.schemas.strategy import TradeIntent
from services.execution.adapter.base import AdapterResponse, ExecutionAdapterBase

_PAPER_COMMISSION_RATE = Decimal("0.0004")  # 4 bps simulated maker fee

# Canonical market-type pairs for cross-market fallback (paper mode only).
# When spot data is unavailable, try futures price as a proxy (prices are
# nearly identical for BTC/ETH), and vice versa.
_MARKET_TYPE_FALLBACK = {"spot": "futures", "futures": "spot"}


class PaperExecutionAdapter(ExecutionAdapterBase):
    """Deterministic paper execution adapter.

    Fill-price resolution order (per intent):
      1. intent.limit_price          — LIMIT orders fill exactly here
      2. Redis analytics snapshot    — analytics:{market_type}:{symbol}:snapshot
                                       (market_state.price, same source as
                                        get_symbol_snapshot; refreshed every ~1 s)
      3. Redis market price key      — market:{market_type}:{symbol}:price  (60 s TTL)
      4. Redis book_ticker mid       — market:{market_type}:{symbol}:book_ticker (10 s TTL)
      5. Cross-market fallback       — repeat steps 2-4 with the complementary
                                       market_type (spot↔futures).  Paper mode only;
                                       BTC/ETH spot/futures prices track within ~$1.
      6. size_usd / size derivation  — last resort when Redis is unavailable
      7. Failure (success=False)     — never fill at Decimal("0")

    Priority order (2→3→4) matches the canonical key order of get_symbol_snapshot
    so any price visible in the snapshot is always reachable by the adapter.

    No network calls to any exchange; purely deterministic for replay.
    Accepts an optional redis client so callers can inject it; when None the
    adapter works only for LIMIT orders and size-derived fills.
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

        # 2–4. Redis lookups (analytics snapshot → market price → book ticker)
        if self._redis is not None:
            price = await self._redis_price(mtype, symbol)
            if price is not None:
                return price.quantize(Decimal("0.01"))

            # 5. Cross-market fallback (paper mode only; spot ↔ futures)
            fallback_mtype = _MARKET_TYPE_FALLBACK.get(mtype)
            if fallback_mtype:
                price = await self._redis_price(fallback_mtype, symbol)
                if price is not None:
                    return price.quantize(Decimal("0.01"))

        # 6. Derivation from notional (size_usd / size) — works when both set
        if intent.size_usd is not None and intent.size > 0:
            return (intent.size_usd / intent.size).quantize(Decimal("0.01"))

        return None

    async def _redis_price(self, market_type: str, symbol: str) -> Decimal | None:
        """Try canonical price sources in priority order; return None if all absent.

        Order matches get_symbol_snapshot (analytics snapshot first) so any price
        visible in the MCP snapshot is always reachable by this adapter:
          1. analytics:{market_type}:{symbol}:snapshot  (market_state.price, ~1 s refresh)
          2. market:{market_type}:{symbol}:price         (60 s TTL, per-trade write)
          3. market:{market_type}:{symbol}:book_ticker   (10 s TTL, mid-price)
        """
        # 1. Analytics snapshot (canonical — same source as get_symbol_snapshot)
        analytics_raw = await self._redis.get(
            RedisKeys.analytics_snapshot(market_type, symbol)
        )
        if analytics_raw:
            try:
                snapshot = json.loads(analytics_raw)
                ms = snapshot.get("market_state") or {}
                p = ms.get("price")
                if p is not None and str(p) not in ("", "0", "None"):
                    candidate = Decimal(str(p))
                    if candidate > 0:
                        return candidate
            except Exception:
                pass

        # 2. Market price key (written by normalizer trade handler)
        raw = await self._redis.get(RedisKeys.market_price(market_type, symbol))
        if raw:
            try:
                data = json.loads(raw)
                price_str = data.get("price")
                if price_str and str(price_str) not in ("", "0"):
                    candidate = Decimal(str(price_str))
                    if candidate > 0:
                        return candidate
            except Exception:
                pass

        # 3. Book ticker mid-price
        book_raw = await self._redis.get(RedisKeys.market_book_ticker(market_type, symbol))
        if book_raw:
            try:
                book = json.loads(book_raw)
                bid = book.get("bid_price")
                ask = book.get("ask_price")
                if bid and ask and str(bid) not in ("", "0") and str(ask) not in ("", "0"):
                    mid = (Decimal(str(bid)) + Decimal(str(ask))) / 2
                    if mid > 0:
                        return mid
            except Exception:
                pass

        return None
