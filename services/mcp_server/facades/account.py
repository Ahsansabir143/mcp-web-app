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
from shared.utils.logging import get_logger

log = get_logger("mcp_server.facades.account")


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

        # Pull stream cache from Redis for freshness
        cache_raw = await redis.get(RedisKeys.account_stream_status(str(acct.id)))
        redis_status = None
        if cache_raw:
            try:
                redis_status = json.loads(cache_raw)
            except Exception:
                pass

        rows.append({
            "account_id": str(acct.id),
            "account_label": acct.account_label,
            "venue": acct.venue,
            "trading_mode": acct.trading_mode,
            "approval_level": acct.approval_level,
            "connection_status": acct.connection_status,
            "last_connectivity_check_ms": acct.last_connectivity_check_ms,
            "stream_status": acct.stream_status,
            "stream_last_event_ms": acct.stream_last_event_ms,
            "stream_age_ms": stream_age_ms,
            "stream_error": acct.stream_error,
            "redis_stream_cache": redis_status,
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

    # Try Redis cache first
    cache_raw = await redis.get(RedisKeys.account_live_balances(account_id))
    if cache_raw:
        try:
            balances = json.loads(cache_raw)
            if min_total > 0:
                balances = [b for b in balances if float(b.get("total", 0)) >= min_total]
            return {
                "account_id": account_id,
                "source": "redis_cache",
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
    return {
        "account_id": account_id,
        "source": "database",
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

    cache_raw = await redis.get(RedisKeys.account_live_positions(account_id))
    if cache_raw:
        try:
            positions = json.loads(cache_raw)
            return {
                "account_id": account_id,
                "source": "redis_cache",
                "positions": positions,
                "count": len(positions),
            }
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
    return {
        "account_id": account_id,
        "source": "database",
        "positions": positions,
        "count": len(positions),
    }


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
            select(Fill)
            .where(Fill.account_id == aid)
            .order_by(desc(Fill.timestamp_ms))
            .limit(limit)
        )
        if symbol:
            stmt = stmt.where(Fill.symbol == symbol)
        rows = (await session.execute(stmt)).scalars().all()

    fills = [
        {
            "exchange_trade_id": r.exchange_trade_id,
            "symbol": r.symbol,
            "side": r.side,
            "price": str(r.price),
            "qty": str(r.qty),
            "quote_qty": str(r.quote_qty),
            "commission": str(r.commission),
            "commission_asset": r.commission_asset,
            "realized_pnl": str(r.realized_pnl) if r.realized_pnl else None,
            "is_maker": r.is_maker,
            "timestamp_ms": r.timestamp_ms,
        }
        for r in rows
    ]
    return {"account_id": account_id, "fills": fills, "count": len(fills)}
