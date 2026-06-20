from __future__ import annotations

import json

from shared.redis.keys import RedisKeys
from shared.schemas.enums import EventType, Venue
from shared.schemas.events import NormalizedEvent, RawEvent

from services.normalizer.handlers.base import HandlerResult, HotStateWrite, NormalizeError
from services.normalizer.symbol import normalize_symbol

_ACCOUNT_TTL_S = 300

# These statuses mean the order is still open and should be in the hot-state key.
_OPEN_STATUSES = {"NEW", "PARTIALLY_FILLED"}


def handle_user_order(event: RawEvent, user_id: str = "") -> HandlerResult:
    """Normalize ORDER_TRADE_UPDATE (Binance futures user data stream).

    Writes open-order hot-state only for NEW and PARTIALLY_FILLED statuses.
    Hot-state writes require user_id (same limitation as account_update handler).
    """
    d = event.payload
    o = d.get("o", d)  # ORDER_TRADE_UPDATE wraps order in "o"

    symbol_raw: str = o.get("s", "")
    symbol = normalize_symbol(symbol_raw) if symbol_raw else ""
    if not symbol:
        raise NormalizeError(f"user_order: missing symbol in payload")

    trade_time_ms: int = int(o.get("T", event.received_ms))
    status: str = o.get("X", "")

    normalized = NormalizedEvent(
        event_type=EventType.USER_ORDER,
        venue=Venue.BINANCE,
        market_type=event.market_type,
        symbol=symbol,
        timestamp_ms=trade_time_ms,
        received_ms=event.received_ms,
        source_stream=event.source_stream,
        data={
            "client_order_id": o.get("c", ""),
            "exchange_order_id": str(o.get("i", "")),
            "side": o.get("S", ""),
            "order_type": o.get("o", ""),
            "order_status": status,
            "price": o.get("p", "0"),
            "orig_qty": o.get("q", "0"),
            "filled_qty": o.get("z", "0"),
            "avg_price": o.get("ap", "0"),
            "reduce_only": bool(o.get("R", False)),
            "position_side": o.get("ps", "BOTH"),
            "stop_price": o.get("sp", "0"),
            "time_in_force": o.get("f", "GTC"),
            "trade_time_ms": trade_time_ms,
            "commission": o.get("n", "0"),
            "commission_asset": o.get("N", ""),
            "realized_pnl": o.get("rp", "0"),
            "is_maker": bool(o.get("m", False)),
        },
    )

    hot: list[HotStateWrite] = []
    if user_id and status in _OPEN_STATUSES:
        # Keyed by client_order_id so repeated partial-fill updates overwrite correctly.
        open_order_payload = json.dumps({
            "symbol": symbol,
            "client_order_id": o.get("c", ""),
            "exchange_order_id": str(o.get("i", "")),
            "side": o.get("S", ""),
            "status": status,
            "price": o.get("p", "0"),
            "orig_qty": o.get("q", "0"),
            "filled_qty": o.get("z", "0"),
            "ts": trade_time_ms,
        })
        hot.append(
            HotStateWrite(
                key=RedisKeys.account_open_orders(user_id),
                value=open_order_payload,
                ttl_s=_ACCOUNT_TTL_S,
            )
        )

    return HandlerResult(event=normalized, hot_writes=hot)
