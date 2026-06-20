from __future__ import annotations

import json

from shared.redis.keys import RedisKeys
from shared.schemas.enums import EventType, Venue
from shared.schemas.events import NormalizedEvent, RawEvent

from services.normalizer.handlers.base import HandlerResult, HotStateWrite, NormalizeError

_ACCOUNT_TTL_S = 300


def handle_account_update(event: RawEvent, user_id: str = "") -> HandlerResult:
    """Normalize ACCOUNT_UPDATE (Binance futures user data stream).

    Hot-state writes require user_id from service config (account→user mapping is
    not embedded in the payload).  When user_id is empty, the event is still
    published to stream:binance:normalized but hot-state is skipped.
    """
    d = event.payload
    a = d.get("a", {})

    event_ms: int = int(d.get("E", event.received_ms))
    reason: str = a.get("m", "")

    raw_balances: list[dict] = a.get("B", [])
    raw_positions: list[dict] = a.get("P", [])

    balances = [
        {
            "asset": b.get("a", ""),
            "wallet_balance": b.get("wb", "0"),
            "cross_wallet_balance": b.get("cw", "0"),
            "balance_change": b.get("bc", "0"),
        }
        for b in raw_balances
    ]

    positions = [
        {
            "symbol": p.get("s", ""),
            "position_side": p.get("ps", "BOTH"),
            "position_amt": p.get("pa", "0"),
            "entry_price": p.get("ep", "0"),
            "unrealized_pnl": p.get("up", "0"),
            "accumulated_realized": p.get("cr", "0"),
            "margin_type": p.get("mt", ""),
            "isolated_wallet": p.get("iw", "0"),
        }
        for p in raw_positions
    ]

    normalized = NormalizedEvent(
        event_type=EventType.ACCOUNT_UPDATE,
        venue=Venue.BINANCE,
        market_type=event.market_type,
        symbol="",   # account update spans multiple symbols
        timestamp_ms=event_ms,
        received_ms=event.received_ms,
        source_stream=event.source_stream,
        data={
            "reason": reason,
            "balances": balances,
            "positions": positions,
        },
    )

    hot: list[HotStateWrite] = []
    if user_id:
        hot = [
            HotStateWrite(
                key=RedisKeys.account_balances(user_id),
                value=json.dumps({"balances": balances, "ts": event_ms}),
                ttl_s=_ACCOUNT_TTL_S,
            ),
            HotStateWrite(
                key=RedisKeys.account_positions(user_id),
                value=json.dumps({"positions": positions, "ts": event_ms}),
                ttl_s=_ACCOUNT_TTL_S,
            ),
        ]

    return HandlerResult(event=normalized, hot_writes=hot)
