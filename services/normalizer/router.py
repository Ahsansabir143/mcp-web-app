from __future__ import annotations

from shared.schemas.events import RawEvent

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

    # ── Private user data streams ────────────────────────────────
    if s == "user_data.ACCOUNT_UPDATE":
        return handle_account_update(raw_event, user_id)

    if s == "user_data.ORDER_TRADE_UPDATE":
        return handle_user_order(raw_event, user_id)

    # ── Orderbook (must check before generic @depth match) ───────
    if "@depth" in s:
        if "snapshot" in s:
            return handle_orderbook_snapshot(raw_event)
        return handle_orderbook_delta(raw_event, current_book)

    # ── Kline (must check before suffix matching) ────────────────
    if "@kline_" in s:
        return handle_kline(raw_event)

    # ── Suffix-routed streams ────────────────────────────────────
    if s.endswith("@trade"):
        return handle_trade(raw_event)

    if s.endswith("@aggTrade"):
        return handle_agg_trade(raw_event)

    if s.endswith("@bookTicker"):
        return handle_book_ticker(raw_event)

    if s.endswith("@markPrice"):
        return handle_mark_price(raw_event)

    if s.endswith("@forceOrder"):
        return handle_liquidation(raw_event)

    return None  # unknown stream — caller will ACK and skip
