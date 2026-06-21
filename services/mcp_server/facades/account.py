"""Account observability facade — live account state for MCP read tools.

All outputs are sanitized: no API keys, secrets, or encrypted values are ever
returned. Redis cache is the primary source for hot data; DB is the fallback.
"""
from __future__ import annotations

import json
import time
import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.db.models.account import Balance, ExchangeAccount, Fill, Order, Position
from shared.redis.keys import RedisKeys
from shared.redis.streams import StreamNames
from shared.utils.logging import get_logger

log = get_logger("mcp_server.facades.account")

_STREAM_STALE_MS = 120_000   # 2 min — matches Redis stream status TTL
_BALANCE_STALE_MS = 300_000  # 5 min — matches Redis balance/position cache TTL


async def get_account_connection_status(
    session_factory: async_sessionmaker[AsyncSession],
    redis,
    account_id: str | None = None,
) -> dict:
    """Return connection + stream health for the requested account.

    If account_id is None, returns status for ALL active accounts.
    """
    now_ms = int(time.time() * 1000)

    async with session_factory() as session:
        if account_id:
            try:
                aid = uuid.UUID(account_id)
            except ValueError:
                return {"error": "invalid_account_id"}
            stmt = select(ExchangeAccount).where(ExchangeAccount.id == aid)
        else:
            stmt = select(ExchangeAccount).where(ExchangeAccount.is_active == True)

        accounts = (await session.execute(stmt)).scalars().all()

    rows = []
    for acct in accounts:
        stream_age_ms = None
        if acct.stream_last_event_ms:
            stream_age_ms = now_ms - acct.stream_last_event_ms

        # Redis now written for ALL status transitions (not just on event receipt)
        cache_raw = await redis.get(RedisKeys.account_stream_status(str(acct.id)))
        redis_data = None
        source_of_truth = "db_fallback"
        if cache_raw:
            try:
                redis_data = json.loads(cache_raw)
                source_of_truth = "redis_live"
            except Exception:
                pass

        # Prefer Redis for current status/error; DB for stream_last_event_ms (written every event)
        stream_status = (redis_data or {}).get("status") or acct.stream_status
        stream_error = (redis_data or {}).get("error") or acct.stream_error

        if stream_age_ms is not None:
            stale = stream_age_ms > _STREAM_STALE_MS
        else:
            stale = True  # no events ever received

        rows.append({
            "account_id": str(acct.id),
            "account_label": acct.account_label,
            "venue": acct.venue,
            "trading_mode": acct.trading_mode,
            "approval_level": acct.approval_level,
            "connection_status": acct.connection_status,
            "last_connectivity_check_ms": acct.last_connectivity_check_ms,
            "stream_status": stream_status,
            "stream_last_event_ms": acct.stream_last_event_ms,
            "stream_age_ms": stream_age_ms,
            "stream_error": stream_error,
            "stale": stale,
            "stale_threshold_ms": _STREAM_STALE_MS,
            "source_of_truth": source_of_truth,
        })

    if account_id and rows:
        return rows[0]
    return {"accounts": rows, "count": len(rows)}


async def get_account_balances(
    session_factory: async_sessionmaker[AsyncSession],
    redis,
    account_id: str,
    min_total: float = 0.0,
) -> dict:
    """Return balances from Redis cache (fast) with DB fallback."""
    try:
        aid = uuid.UUID(account_id)
    except ValueError:
        return {"error": "invalid_account_id"}

    now_ms = int(time.time() * 1000)

    # Try Redis cache first
    cache_raw = await redis.get(RedisKeys.account_live_balances(account_id))
    if cache_raw:
        try:
            balances = json.loads(cache_raw)
            if min_total > 0:
                balances = [b for b in balances if float(b.get("total", 0)) >= min_total]

            ts_values = [b.get("updated_at_ms") for b in balances if b.get("updated_at_ms")]
            last_updated_ms = max(ts_values) if ts_values else None
            stale = last_updated_ms is None or (now_ms - last_updated_ms) > _BALANCE_STALE_MS

            return {
                "account_id": account_id,
                "source": "redis_cache",
                "last_updated_ms": last_updated_ms,
                "checked_at_ms": now_ms,
                "stale": stale,
                "balances": balances,
                "count": len(balances),
            }
        except Exception:
            pass

    # DB fallback
    async with session_factory() as session:
        stmt = select(Balance).where(Balance.account_id == aid)
        rows = (await session.execute(stmt)).scalars().all()

    balances = [
        {
            "asset": r.asset,
            "free": str(r.free),
            "locked": str(r.locked),
            "total": str(r.total),
            "updated_at_ms": r.updated_at_ms,
        }
        for r in rows
        if float(str(r.total or 0)) >= min_total
    ]
    ts_values = [b["updated_at_ms"] for b in balances if b.get("updated_at_ms")]
    last_updated_ms = max(ts_values) if ts_values else None
    return {
        "account_id": account_id,
        "source": "database",
        "last_updated_ms": last_updated_ms,
        "checked_at_ms": now_ms,
        "stale": True,  # DB = cache miss = potentially stale
        "balances": balances,
        "count": len(balances),
    }


async def get_account_positions(
    session_factory: async_sessionmaker[AsyncSession],
    redis,
    account_id: str,
) -> dict:
    """Return open positions from Redis cache with DB fallback."""
    try:
        aid = uuid.UUID(account_id)
    except ValueError:
        return {"error": "invalid_account_id"}

    now_ms = int(time.time() * 1000)
    _EMPTY_NOTE = (
        "No open positions. Paper trades do not create position records — "
        "use get_recent_fills to see paper execution history."
    )

    cache_raw = await redis.get(RedisKeys.account_live_positions(account_id))
    if cache_raw:
        try:
            positions = json.loads(cache_raw)
            ts_values = [p.get("updated_at_ms") for p in positions if p.get("updated_at_ms")]
            last_updated_ms = max(ts_values) if ts_values else None
            stale = last_updated_ms is None or (now_ms - last_updated_ms) > _BALANCE_STALE_MS

            result = {
                "account_id": account_id,
                "source": "redis_cache",
                "last_updated_ms": last_updated_ms,
                "checked_at_ms": now_ms,
                "stale": stale,
                "positions": positions,
                "count": len(positions),
            }
            if not positions:
                result["note"] = _EMPTY_NOTE
            return result
        except Exception:
            pass

    async with session_factory() as session:
        stmt = select(Position).where(Position.account_id == aid)
        rows = (await session.execute(stmt)).scalars().all()

    positions = [
        {
            "symbol": r.symbol,
            "market_type": r.market_type,
            "side": r.side,
            "quantity": str(r.quantity),
            "entry_price": str(r.entry_price),
            "mark_price": str(r.mark_price) if r.mark_price else None,
            "unrealized_pnl": str(r.unrealized_pnl) if r.unrealized_pnl else None,
            "leverage": str(r.leverage) if r.leverage else None,
            "updated_at_ms": r.updated_at_ms,
        }
        for r in rows
        if float(str(r.quantity or 0)) != 0
    ]
    ts_values = [p["updated_at_ms"] for p in positions if p.get("updated_at_ms")]
    last_updated_ms = max(ts_values) if ts_values else None

    result = {
        "account_id": account_id,
        "source": "database",
        "last_updated_ms": last_updated_ms,
        "checked_at_ms": now_ms,
        "stale": True,
        "positions": positions,
        "count": len(positions),
    }
    if not positions:
        result["note"] = _EMPTY_NOTE
    return result


async def get_open_orders(
    session_factory: async_sessionmaker[AsyncSession],
    account_id: str,
    symbol: str | None = None,
    limit: int = 50,
) -> dict:
    try:
        aid = uuid.UUID(account_id)
    except ValueError:
        return {"error": "invalid_account_id"}

    limit = min(max(1, limit), 200)
    async with session_factory() as session:
        stmt = (
            select(Order)
            .where(
                Order.account_id == aid,
                Order.status.in_(["NEW", "PARTIALLY_FILLED"]),
            )
            .order_by(desc(Order.updated_at_ms))
            .limit(limit)
        )
        if symbol:
            stmt = stmt.where(Order.symbol == symbol)
        rows = (await session.execute(stmt)).scalars().all()

    orders = [
        {
            "client_order_id": r.client_order_id,
            "exchange_order_id": r.exchange_order_id,
            "symbol": r.symbol,
            "market_type": r.market_type,
            "side": r.side,
            "order_type": r.order_type,
            "status": r.status,
            "quantity": str(r.quantity),
            "filled_qty": str(r.filled_qty),
            "price": str(r.price) if r.price else None,
            "avg_fill_price": str(r.avg_fill_price) if r.avg_fill_price else None,
            "time_in_force": r.time_in_force,
            "created_at_ms": r.created_at_ms,
            "updated_at_ms": r.updated_at_ms,
        }
        for r in rows
    ]
    return {"account_id": account_id, "orders": orders, "count": len(orders)}


async def get_recent_fills(
    session_factory: async_sessionmaker[AsyncSession],
    account_id: str,
    symbol: str | None = None,
    limit: int = 20,
) -> dict:
    try:
        aid = uuid.UUID(account_id)
    except ValueError:
        return {"error": "invalid_account_id"}

    limit = min(max(1, limit), 100)
    async with session_factory() as session:
        stmt = (
            select(Fill, Order.market_type.label("order_market_type"))
            .outerjoin(Order, Fill.order_id == Order.id)
            .where(Fill.account_id == aid)
            .order_by(desc(Fill.timestamp_ms))
            .limit(limit)
        )
        if symbol:
            stmt = stmt.where(Fill.symbol == symbol)
        rows = (await session.execute(stmt)).all()

    fills = []
    for fill, order_market_type in rows:
        fills.append({
            "exchange_trade_id": fill.exchange_trade_id,
            "symbol": fill.symbol,
            "side": fill.side,
            "price": str(fill.price),
            "qty": str(fill.qty),
            "quote_qty": str(fill.quote_qty),
            "commission": str(fill.commission),
            "commission_asset": fill.commission_asset,
            "realized_pnl": str(fill.realized_pnl) if fill.realized_pnl else None,
            "is_maker": fill.is_maker,
            "timestamp_ms": fill.timestamp_ms,
            "trading_mode": "paper" if fill.exchange_trade_id.startswith("PAPER-") else "live",
            "market_type": order_market_type or "unknown",
        })
    return {"account_id": account_id, "fills": fills, "count": len(fills)}


async def get_stream_health(
    session_factory: async_sessionmaker[AsyncSession],
    redis,
) -> dict:
    """Report health of account WebSocket streams and internal Redis streams."""
    now_ms = int(time.time() * 1000)

    # Account streams
    async with session_factory() as session:
        stmt = select(ExchangeAccount).where(ExchangeAccount.is_active == True)
        accounts = (await session.execute(stmt)).scalars().all()

    account_streams = []
    for acct in accounts:
        cache_raw = await redis.get(RedisKeys.account_stream_status(str(acct.id)))
        redis_data = None
        if cache_raw:
            try:
                redis_data = json.loads(cache_raw)
            except Exception:
                pass

        stream_age_ms = None
        if acct.stream_last_event_ms:
            stream_age_ms = now_ms - acct.stream_last_event_ms

        status = (redis_data or {}).get("status") or acct.stream_status or "unknown"
        healthy = (
            status == "connected"
            and stream_age_ms is not None
            and stream_age_ms < _STREAM_STALE_MS
        )

        account_streams.append({
            "account_id": str(acct.id),
            "account_label": acct.account_label,
            "venue": acct.venue,
            "status": status,
            "stream_last_event_ms": acct.stream_last_event_ms,
            "stream_age_ms": stream_age_ms,
            "healthy": healthy,
            "source": "redis_live" if redis_data else "db_fallback",
        })

    # Internal Redis streams — check length and consumer group lag
    _INTERNAL_STREAMS = [
        StreamNames.STRATEGY_INTENTS,
        StreamNames.EXECUTION_EVENTS,
        StreamNames.RAW,
        StreamNames.NORMALIZED,
        StreamNames.ANALYTICS_DERIVED,
        StreamNames.MCP_AUDIT,
    ]

    internal_streams = []
    for stream_name in _INTERNAL_STREAMS:
        info: dict = {"name": stream_name, "healthy": True}
        try:
            info["length"] = await redis.xlen(stream_name)
        except Exception:
            info["length"] = None
            info["healthy"] = False
        try:
            groups = await redis.xinfo_groups(stream_name)
            info["consumer_groups"] = [
                {
                    "name": str(g.get("name", "")),
                    "pending": g.get("pending", 0),
                    "lag": g.get("lag", 0),
                }
                for g in groups
            ]
        except Exception:
            info["consumer_groups"] = []
        internal_streams.append(info)

    overall_healthy = bool(account_streams) and all(s["healthy"] for s in account_streams)

    return {
        "account_streams": account_streams,
        "internal_streams": internal_streams,
        "overall_healthy": overall_healthy,
        "checked_at_ms": now_ms,
        "stale_threshold_ms": _STREAM_STALE_MS,
    }
