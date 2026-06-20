from __future__ import annotations

import json

from shared.redis.keys import RedisKeys
from shared.schemas.enums import EventType, Venue
from shared.schemas.events import NormalizedEvent, RawEvent

from services.normalizer.handlers.base import HandlerResult, HotStateWrite, NormalizeError
from services.normalizer.symbol import normalize_symbol, symbol_from_stream

_BOOK_TICKER_TTL_S = 10


def handle_book_ticker(event: RawEvent) -> HandlerResult:
    d = event.payload
    symbol = normalize_symbol(d.get("s", "")) or symbol_from_stream(event.source_stream)
    if not symbol:
        raise NormalizeError(f"book_ticker: cannot determine symbol from {event.source_stream!r}")

    bid_price: str = d.get("b", "0")
    bid_qty: str = d.get("B", "0")
    ask_price: str = d.get("a", "0")
    ask_qty: str = d.get("A", "0")
    update_id = d.get("u")

    normalized = NormalizedEvent(
        event_type=EventType.BOOK_TICKER,
        venue=Venue.BINANCE,
        market_type=event.market_type,
        symbol=symbol,
        timestamp_ms=event.received_ms,
        received_ms=event.received_ms,
        source_stream=event.source_stream,
        data={
            "bid_price": bid_price,
            "bid_qty": bid_qty,
            "ask_price": ask_price,
            "ask_qty": ask_qty,
            "update_id": update_id,
        },
    )

    mtype = event.market_type.value
    hot = [
        HotStateWrite(
            key=RedisKeys.market_book_ticker(mtype, symbol),
            value=json.dumps({
                "bid": bid_price,
                "bid_qty": bid_qty,
                "ask": ask_price,
                "ask_qty": ask_qty,
                "update_id": update_id,
                "ts": event.received_ms,
            }),
            ttl_s=_BOOK_TICKER_TTL_S,
        )
    ]
    return HandlerResult(event=normalized, hot_writes=hot)
