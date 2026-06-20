from __future__ import annotations

import json

from shared.redis.keys import RedisKeys
from shared.schemas.enums import EventType, Venue
from shared.schemas.events import NormalizedEvent, RawEvent

from services.normalizer.handlers.base import HandlerResult, HotStateWrite, NormalizeError
from services.normalizer.symbol import normalize_symbol, symbol_from_stream

_BOOK_TTL_S = 30


def is_depth_delta_stream(source_stream: str) -> bool:
    return "@depth" in source_stream and "snapshot" not in source_stream


def _parse_levels(levels: list) -> list[tuple[str, str]]:
    return [(lvl[0], lvl[1]) for lvl in levels]


def handle_orderbook_snapshot(event: RawEvent) -> HandlerResult:
    d = event.payload
    symbol = symbol_from_stream(event.source_stream)
    if not symbol:
        raise NormalizeError(f"orderbook_snapshot: cannot determine symbol from {event.source_stream!r}")

    last_update_id: int = int(d.get("lastUpdateId", 0))
    bids = _parse_levels(d.get("bids", []))
    asks = _parse_levels(d.get("asks", []))

    normalized = NormalizedEvent(
        event_type=EventType.ORDERBOOK_SNAPSHOT,
        venue=Venue.BINANCE,
        market_type=event.market_type,
        symbol=symbol,
        timestamp_ms=event.received_ms,
        received_ms=event.received_ms,
        source_stream=event.source_stream,
        data={
            "last_update_id": last_update_id,
            "bids": bids,
            "asks": asks,
        },
    )

    mtype = event.market_type.value
    book_value = json.dumps({
        "last_update_id": last_update_id,
        "bids": bids,
        "asks": asks,
        "ts": event.received_ms,
    })
    hot = [
        HotStateWrite(
            key=RedisKeys.market_book(mtype, symbol),
            value=book_value,
            ttl_s=_BOOK_TTL_S,
        )
    ]
    return HandlerResult(event=normalized, hot_writes=hot)


def handle_orderbook_delta(
    event: RawEvent,
    current_book: dict | None = None,
) -> HandlerResult:
    """Apply a depth update delta to the current in-memory book representation.

    If current_book is None (no snapshot yet in Redis), the hot-state write is
    skipped — the delta is still published to the normalized stream so downstream
    consumers can replay it when a snapshot arrives.
    """
    d = event.payload
    symbol = symbol_from_stream(event.source_stream)
    if not symbol:
        raise NormalizeError(f"orderbook_delta: cannot determine symbol from {event.source_stream!r}")

    first_update_id: int = int(d.get("U", 0))
    last_update_id: int = int(d.get("u", 0))
    bid_deltas = _parse_levels(d.get("b", []))
    ask_deltas = _parse_levels(d.get("a", []))

    normalized = NormalizedEvent(
        event_type=EventType.ORDERBOOK_DELTA,
        venue=Venue.BINANCE,
        market_type=event.market_type,
        symbol=symbol,
        timestamp_ms=int(d.get("E", event.received_ms)),
        received_ms=event.received_ms,
        source_stream=event.source_stream,
        data={
            "first_update_id": first_update_id,
            "last_update_id": last_update_id,
            "bids": bid_deltas,
            "asks": ask_deltas,
        },
    )

    hot: list[HotStateWrite] = []

    if current_book is not None:
        merged = _apply_delta(current_book, bid_deltas, ask_deltas, last_update_id, event.received_ms)
        mtype = event.market_type.value
        hot.append(
            HotStateWrite(
                key=RedisKeys.market_book(mtype, symbol),
                value=json.dumps(merged),
                ttl_s=_BOOK_TTL_S,
            )
        )

    return HandlerResult(event=normalized, hot_writes=hot)


def _apply_delta(
    book: dict,
    bid_deltas: list[tuple[str, str]],
    ask_deltas: list[tuple[str, str]],
    last_update_id: int,
    ts: int,
) -> dict:
    bids = {lvl[0]: lvl[1] for lvl in book.get("bids", [])}
    asks = {lvl[0]: lvl[1] for lvl in book.get("asks", [])}

    for price, qty in bid_deltas:
        if float(qty) == 0.0:
            bids.pop(price, None)
        else:
            bids[price] = qty

    for price, qty in ask_deltas:
        if float(qty) == 0.0:
            asks.pop(price, None)
        else:
            asks[price] = qty

    # Re-sort: bids descending, asks ascending
    sorted_bids = sorted(bids.items(), key=lambda x: -float(x[0]))
    sorted_asks = sorted(asks.items(), key=lambda x: float(x[0]))

    return {
        "last_update_id": last_update_id,
        "bids": [[p, q] for p, q in sorted_bids],
        "asks": [[p, q] for p, q in sorted_asks],
        "ts": ts,
    }
