from __future__ import annotations

import json

from shared.redis.keys import RedisKeys
from shared.schemas.enums import EventType, Venue
from shared.schemas.events import NormalizedEvent, RawEvent

from services.normalizer.handlers.base import HandlerResult, HotStateWrite, NormalizeError
from services.normalizer.symbol import normalize_symbol, symbol_from_stream

_MARK_TTL_S = 60
_FUNDING_TTL_S = 3_600  # funding rate changes every 8 h; keep 1 h


def handle_mark_price(event: RawEvent) -> HandlerResult:
    d = event.payload
    symbol = normalize_symbol(d.get("s", "")) or symbol_from_stream(event.source_stream)
    if not symbol:
        raise NormalizeError(f"mark_price: cannot determine symbol from {event.source_stream!r}")

    mark_price: str = d.get("p", "0")
    index_price: str = d.get("i", "0")
    estimated_settle: str = d.get("P", "0")
    funding_rate: str = d.get("r", "0")
    next_funding_ms: int = int(d.get("T", 0))
    event_ms: int = int(d.get("E", event.received_ms))

    normalized = NormalizedEvent(
        event_type=EventType.MARK_PRICE,
        venue=Venue.BINANCE,
        market_type=event.market_type,
        symbol=symbol,
        timestamp_ms=event_ms,
        received_ms=event.received_ms,
        source_stream=event.source_stream,
        data={
            "mark_price": mark_price,
            "index_price": index_price,
            "estimated_settle_price": estimated_settle,
            "funding_rate": funding_rate,
            "next_funding_time_ms": next_funding_ms,
        },
    )

    mtype = event.market_type.value
    hot = [
        HotStateWrite(
            key=RedisKeys.market_mark(mtype, symbol),
            value=json.dumps({
                "mark_price": mark_price,
                "index_price": index_price,
                "estimated_settle_price": estimated_settle,
                "ts": event_ms,
            }),
            ttl_s=_MARK_TTL_S,
        ),
        HotStateWrite(
            key=RedisKeys.market_funding(mtype, symbol),
            value=json.dumps({
                "funding_rate": funding_rate,
                "next_funding_time_ms": next_funding_ms,
                "ts": event_ms,
            }),
            ttl_s=_FUNDING_TTL_S,
        ),
    ]
    return HandlerResult(event=normalized, hot_writes=hot)
