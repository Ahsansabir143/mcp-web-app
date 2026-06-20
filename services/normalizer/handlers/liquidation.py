from __future__ import annotations

from shared.schemas.enums import EventType, Venue
from shared.schemas.events import NormalizedEvent, RawEvent

from services.normalizer.handlers.base import HandlerResult, NormalizeError
from services.normalizer.symbol import normalize_symbol, symbol_from_stream


def handle_liquidation(event: RawEvent) -> HandlerResult:
    d = event.payload
    o = d.get("o", d)  # futures forceOrder wraps order in "o"; handle flat fallback

    symbol = normalize_symbol(o.get("s", "")) or symbol_from_stream(event.source_stream)
    if not symbol:
        raise NormalizeError(f"liquidation: cannot determine symbol from {event.source_stream!r}")

    order_time_ms: int = int(o.get("T", event.received_ms))

    normalized = NormalizedEvent(
        event_type=EventType.LIQUIDATION,
        venue=Venue.BINANCE,
        market_type=event.market_type,
        symbol=symbol,
        timestamp_ms=order_time_ms,
        received_ms=event.received_ms,
        source_stream=event.source_stream,
        data={
            "side": o.get("S", ""),
            "order_type": o.get("o", ""),
            "time_in_force": o.get("f", ""),
            "orig_qty": o.get("q", "0"),
            "price": o.get("p", "0"),
            "avg_price": o.get("ap", "0"),
            "last_fill_qty": o.get("l", "0"),
            "status": o.get("X", ""),
            "order_time_ms": order_time_ms,
        },
    )

    # No hot-state writes: liquidations are events, not persistent state.
    return HandlerResult(event=normalized, hot_writes=[])
