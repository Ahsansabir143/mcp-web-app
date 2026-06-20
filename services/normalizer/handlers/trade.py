from __future__ import annotations

import json

from shared.redis.keys import RedisKeys
from shared.schemas.enums import EventType, Venue
from shared.schemas.events import NormalizedEvent, RawEvent

from services.normalizer.handlers.base import HandlerResult, HotStateWrite, NormalizeError
from services.normalizer.symbol import normalize_symbol, symbol_from_stream

_PRICE_TTL_S = 60


def handle_trade(event: RawEvent) -> HandlerResult:
    d = event.payload
    symbol = normalize_symbol(d.get("s", "")) or symbol_from_stream(event.source_stream)
    if not symbol:
        raise NormalizeError(f"trade: cannot determine symbol from {event.source_stream!r}")

    price: str = d.get("p", "0")
    qty: str = d.get("q", "0")
    trade_time_ms: int = int(d.get("T", event.received_ms))
    is_buyer_maker: bool = bool(d.get("m", False))
    trade_id = d.get("t")

    normalized = NormalizedEvent(
        event_type=EventType.TRADE,
        venue=Venue.BINANCE,
        market_type=event.market_type,
        symbol=symbol,
        timestamp_ms=trade_time_ms,
        received_ms=event.received_ms,
        source_stream=event.source_stream,
        data={
            "trade_id": trade_id,
            "price": price,
            "qty": qty,
            "is_buyer_maker": is_buyer_maker,
            "trade_time_ms": trade_time_ms,
        },
    )

    mtype = event.market_type.value
    hot = [
        HotStateWrite(
            key=RedisKeys.market_price(mtype, symbol),
            value=json.dumps({"price": price, "ts": trade_time_ms}),
            ttl_s=_PRICE_TTL_S,
        )
    ]
    return HandlerResult(event=normalized, hot_writes=hot)
