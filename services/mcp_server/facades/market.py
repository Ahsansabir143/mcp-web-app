"""Market / analytics hot-state facade — read-only Redis reads."""
from __future__ import annotations

import json
import time
from decimal import Decimal, InvalidOperation

from shared.redis.client import RedisClient
from shared.redis.keys import RedisKeys


async def get_symbol_snapshot(
    redis: RedisClient,
    market_type: str,
    symbol: str,
) -> dict:
    """Return the latest analytics snapshot for a symbol.

    Reads the full ``analytics:{market_type}:{symbol}:snapshot`` key written by
    the analytics service. Falls back to assembling from individual hot-state
    keys if the snapshot key is absent.

    The assembled fallback always surfaces top-level ``last_price``, ``bid``,
    ``ask``, ``spread``, ``price_age_ms``, and ``bid_ask_age_ms`` fields so
    callers get a consistent shape regardless of which path was taken.
    """
    raw = await redis.get(RedisKeys.analytics_snapshot(market_type, symbol))
    if raw:
        data = json.loads(raw)
        data["symbol"] = symbol
        data["market_type"] = market_type
        data["source"] = "analytics_snapshot"
        _inject_price_fields(data)
        return data

    # ── Assembled fallback ────────────────────────────────────────────────────
    now_ms = int(time.time() * 1000)
    parts: dict = {
        "symbol": symbol,
        "market_type": market_type,
        "source": "assembled",
        "available": {},
    }

    price_raw = await redis.get(RedisKeys.market_price(market_type, symbol))
    if price_raw:
        price_data = json.loads(price_raw)
        parts["available"]["price"] = price_data
        price_val = price_data.get("price")
        if price_val:
            parts["last_price"] = str(price_val)
        price_ts = price_data.get("ts")
        if price_ts:
            parts["price_age_ms"] = max(0, now_ms - int(price_ts))

    book_raw = await redis.get(RedisKeys.market_book_ticker(market_type, symbol))
    if book_raw:
        book = json.loads(book_raw)
        parts["available"]["book_ticker"] = book
        bid = book.get("bid_price")
        ask = book.get("ask_price")
        if bid:
            parts["bid"] = str(bid)
        if ask:
            parts["ask"] = str(ask)
        if bid and ask:
            try:
                parts["spread"] = str(
                    (Decimal(str(ask)) - Decimal(str(bid))).quantize(Decimal("0.01"))
                )
            except (InvalidOperation, TypeError):
                pass
        book_ts = book.get("ts")
        if book_ts:
            parts["bid_ask_age_ms"] = max(0, now_ms - int(book_ts))

    mark_raw = await redis.get(RedisKeys.market_mark(market_type, symbol))
    if mark_raw:
        parts["available"]["mark"] = json.loads(mark_raw)

    funding_raw = await redis.get(RedisKeys.market_funding(market_type, symbol))
    if funding_raw:
        parts["available"]["funding"] = json.loads(funding_raw)

    cvd_raw = await redis.get(RedisKeys.analytics_cvd(market_type, symbol))
    if cvd_raw:
        parts["available"]["cvd"] = json.loads(cvd_raw)

    delta_raw = await redis.get(RedisKeys.analytics_delta(market_type, symbol))
    if delta_raw:
        parts["available"]["delta"] = json.loads(delta_raw)

    rvol_raw = await redis.get(RedisKeys.analytics_rvol(market_type, symbol))
    if rvol_raw:
        parts["available"]["rvol"] = json.loads(rvol_raw)

    liq_raw = await redis.get(RedisKeys.analytics_liquidation_clusters(market_type, symbol))
    if liq_raw:
        parts["available"]["liquidation_clusters"] = json.loads(liq_raw)

    fp_raw = await redis.get(RedisKeys.analytics_funding_pressure(market_type, symbol))
    if fp_raw:
        parts["available"]["funding_pressure"] = json.loads(fp_raw)

    if not parts["available"]:
        parts["message"] = (
            f"No data available for {symbol}/{market_type}. "
            "Ensure the ingest, normalizer, and analytics services are running."
        )

    return parts


def _inject_price_fields(snapshot: dict) -> None:
    """Promote last_price/bid/ask to top level when reading full analytics snapshot."""
    ms = snapshot.get("market_state") or {}
    if ms.get("price") is not None and "last_price" not in snapshot:
        snapshot["last_price"] = str(ms["price"])
    if ms.get("bid") is not None and "bid" not in snapshot:
        snapshot["bid"] = str(ms["bid"])
    if ms.get("ask") is not None and "ask" not in snapshot:
        snapshot["ask"] = str(ms["ask"])


async def get_current_price(
    redis: RedisClient,
    market_type: str,
    symbol: str,
) -> str | None:
    """Return current mid-price string or None if unavailable."""
    raw = await redis.get(RedisKeys.market_price(market_type, symbol))
    if not raw:
        # Try book_ticker as fallback
        book_raw = await redis.get(RedisKeys.market_book_ticker(market_type, symbol))
        if book_raw:
            book = json.loads(book_raw)
            bid = book.get("bid_price")
            ask = book.get("ask_price")
            if bid and ask:
                from decimal import Decimal
                mid = (Decimal(str(bid)) + Decimal(str(ask))) / 2
                return str(mid)
        return None
    data = json.loads(raw)
    return str(data.get("price") or data.get("mark_price") or "")
