from __future__ import annotations

import re

from shared.schemas.events import RawEvent

# Partial book depth streams (@depth5, @depth10, @depth20) are snapshot-style.
# Full diff depth streams are @depth or @depth@ (no level digit immediately after).
_PARTIAL_DEPTH_RE = re.compile(r"@depth\d")

from services.normalizer.handlers.account_update import handle_account_update
from services.normalizer.handlers.agg_trade import handle_agg_trade
from services.normalizer.handlers.base import HandlerResult
from services.normalizer.handlers.book_ticker import handle_book_ticker
from services.normalizer.handlers.kline import handle_kline
from services.normalizer.handlers.liquidation import handle_liquidation
from services.normalizer.handlers.mark_price import handle_mark_price
from services.normalizer.handlers.orderbook import (
    handle_orderbook_delta,
    handle_orderbook_snapshot,
    is_depth_delta_stream,
)
from services.normalizer.handlers.trade import handle_trade
from services.normalizer.handlers.user_order import handle_user_order


def route(
    raw_event: RawEvent,
    user_id: str = "",
    current_book: dict | None = None,
) -> HandlerResult | None:
    """Route a RawEvent to its normalizer handler.

    Returns None for unrecognized stream names (caller should ACK + skip).

    Args:
        raw_event:    The parsed RawEvent from stream:binance:raw.
        user_id:      Used for account hot-state keys (empty → skip hot writes).
        current_book: Pre-fetched orderbook for delta merging (None → skip merge).
    """
    s = raw_event.source_stream
    sl = s.lower()

    # ── Private user data streams ────────────────────────────────
    if sl == "user_data.account_update":
        return handle_account_update(raw_event, user_id)

    if sl == "user_data.order_trade_update":
        return handle_user_order(raw_event, user_id)

    # ── Orderbook (must check before generic @depth match) ───────
    if "@depth" in sl:
        # Partial book depth (@depth5, @depth10, @depth20) sends full snapshots each tick.
        # Full diff depth (@depth or @depth@<speed>) sends incremental deltas.
        if "snapshot" in sl or _PARTIAL_DEPTH_RE.search(sl):
            return handle_orderbook_snapshot(raw_event)
        return handle_orderbook_delta(raw_event, current_book)

    # ── Kline (must check before suffix matching) ────────────────
    if "@kline_" in sl:
        return handle_kline(raw_event)

    # ── Suffix-routed streams ────────────────────────────────────
    if sl.endswith("@trade"):
        return handle_trade(raw_event)

    if sl.endswith("@aggtrade"):
        return handle_agg_trade(raw_event)

    if sl.endswith("@bookticker"):
        return handle_book_ticker(raw_event)

    if sl.endswith("@markprice"):
        return handle_mark_price(raw_event)

    if sl.endswith("@forceorder"):
        return handle_liquidation(raw_event)

    return None  # unknown stream — caller will ACK and skip
