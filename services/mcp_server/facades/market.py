"""Market / analytics hot-state facade — read-only Redis reads."""
from __future__ import annotations

import json

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
    """
    raw = await redis.get(RedisKeys.analytics_snapshot(market_type, symbol))
    if raw:
        data = json.loads(raw)
        data["symbol"] = symbol
        data["market_type"] = market_type
        data["source"] = "analytics_snapshot"
        return data

    # Assemble from individual keys so the tool is useful even when the full
    # snapshot hasn't been written yet.
    parts: dict = {
        "symbol": symbol,
        "market_type": market_type,
        "source": "assembled",
        "available": {},
    }

    price_raw = await redis.get(RedisKeys.market_price(market_type, symbol))
    if price_raw:
        parts["available"]["price"] = json.loads(price_raw)

    book_raw = await redis.get(RedisKeys.market_book_ticker(market_type, symbol))
    if book_raw:
        parts["available"]["book_ticker"] = json.loads(book_raw)

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
