from __future__ import annotations

import json

from shared.redis.keys import RedisKeys
from shared.schemas.enums import EventType, Venue
from shared.schemas.events import NormalizedEvent, RawEvent

from services.normalizer.handlers.base import HandlerResult, HotStateWrite, NormalizeError
from services.normalizer.symbol import normalize_symbol, symbol_from_stream

# TTL = 2× the interval duration so the latest candle outlives its period.
_INTERVAL_TTL: dict[str, int] = {
    "1s": 4,
    "1m": 120,
    "3m": 360,
    "5m": 600,
    "15m": 1_800,
    "30m": 3_600,
    "1h": 7_200,
    "2h": 14_400,
    "4h": 28_800,
    "6h": 43_200,
    "8h": 57_600,
    "12h": 86_400,
    "1d": 172_800,
    "3d": 518_400,
    "1w": 1_209_600,
    "1M": 5_184_000,
}
_DEFAULT_TTL_S = 300


def _interval_from_stream(source_stream: str) -> str:
    """Extract interval string from stream name like 'btcusdt@kline_1m'."""
    if "@kline_" in source_stream:
        return source_stream.split("@kline_", 1)[1]
    return ""


def handle_kline(event: RawEvent) -> HandlerResult:
    d = event.payload
    k = d.get("k", {})
    symbol = normalize_symbol(k.get("s", "") or d.get("s", "")) or symbol_from_stream(event.source_stream)
    if not symbol:
        raise NormalizeError(f"kline: cannot determine symbol from {event.source_stream!r}")

    interval: str = k.get("i", "") or _interval_from_stream(event.source_stream)
    is_closed: bool = bool(k.get("x", False))
    open_time_ms: int = int(k.get("t", event.received_ms))
    close_time_ms: int = int(k.get("T", open_time_ms))

    normalized = NormalizedEvent(
        event_type=EventType.KLINE,
        venue=Venue.BINANCE,
        market_type=event.market_type,
        symbol=symbol,
        timestamp_ms=open_time_ms,
        received_ms=event.received_ms,
        source_stream=event.source_stream,
        data={
            "interval": interval,
            "open_time_ms": open_time_ms,
            "close_time_ms": close_time_ms,
            "open": k.get("o", "0"),
            "high": k.get("h", "0"),
            "low": k.get("l", "0"),
            "close": k.get("c", "0"),
            "volume": k.get("v", "0"),
            "quote_volume": k.get("q", "0"),
            "trades": int(k.get("n", 0)),
            "taker_buy_volume": k.get("V", "0"),
            "taker_buy_quote_volume": k.get("Q", "0"),
            "is_closed": is_closed,
        },
    )

    mtype = event.market_type.value
    ttl = _INTERVAL_TTL.get(interval, _DEFAULT_TTL_S)
    hot = [
        HotStateWrite(
            key=RedisKeys.market_klines(mtype, symbol, interval),
            value=json.dumps({
                "interval": interval,
                "open_time_ms": open_time_ms,
                "close_time_ms": close_time_ms,
                "open": k.get("o", "0"),
                "high": k.get("h", "0"),
                "low": k.get("l", "0"),
                "close": k.get("c", "0"),
                "volume": k.get("v", "0"),
                "is_closed": is_closed,
                "ts": event.received_ms,
            }),
            ttl_s=ttl,
        )
    ]
    return HandlerResult(event=normalized, hot_writes=hot)
