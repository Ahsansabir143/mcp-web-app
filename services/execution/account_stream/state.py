"""AccountStateWriter — persists user-data stream events to DB and Redis cache.

Handles three categories of events:
  balance_update   — outboundAccountPosition / ACCOUNT_UPDATE balances
  order_update     — executionReport / ORDER_TRADE_UPDATE
  position_update  — ACCOUNT_UPDATE positions (futures only)
"""
from __future__ import annotations

import json
import time
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.db.models.account import Balance, Fill, Order, Position
from shared.redis.keys import RedisKeys
from shared.utils.logging import get_logger

log = get_logger("execution.account_stream.state")

_BALANCE_CACHE_TTL_S = 300
_POSITION_CACHE_TTL_S = 300


class AccountStateWriter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        redis,
        account_id: uuid.UUID,
    ) -> None:
        self._factory = session_factory
        self._redis = redis
        self._account_id = account_id
        self._account_id_str = str(account_id)

    # ── Balances ──────────────────────────────────────────────────────────────

    async def upsert_balances(self, balances: list[dict], timestamp_ms: int | None = None) -> None:
        """Upsert a list of {"asset": str, "free": str, "locked": str} dicts."""
        now_ms = timestamp_ms or int(time.time() * 1000)
        async with self._factory() as session:
            for b in balances:
                asset = b.get("a") or b.get("asset", "")
                free = str(b.get("f") or b.get("free", "0"))
                locked = str(b.get("l") or b.get("locked", "0"))
                if not asset:
                    continue
                try:
                    total = str(Decimal(free) + Decimal(locked))
                except Exception:
                    total = "0"

                stmt = select(Balance).where(
                    Balance.account_id == self._account_id,
                    Balance.asset == asset,
                )
                existing = (await session.execute(stmt)).scalar_one_or_none()
                if existing:
                    existing.free = free
                    existing.locked = locked
                    existing.total = total
                    existing.updated_at_ms = now_ms
                else:
                    session.add(Balance(
                        account_id=self._account_id,
                        asset=asset,
                        free=free,
                        locked=locked,
                        total=total,
                        updated_at_ms=now_ms,
                    ))
            await session.commit()

        await self._cache_balances()

    async def _cache_balances(self) -> None:
        async with self._factory() as session:
            stmt = select(Balance).where(Balance.account_id == self._account_id)
            rows = (await session.execute(stmt)).scalars().all()
        data = [
            {"asset": r.asset, "free": str(r.free), "locked": str(r.locked),
             "total": str(r.total), "updated_at_ms": r.updated_at_ms}
            for r in rows
            if Decimal(str(r.total or 0)) > 0
        ]
        key = RedisKeys.account_live_balances(self._account_id_str)
        await self._redis.set(key, json.dumps(data), ex=_BALANCE_CACHE_TTL_S)

    # ── Positions ─────────────────────────────────────────────────────────────

    async def upsert_positions(self, positions: list[dict], timestamp_ms: int | None = None) -> None:
        """Upsert futures positions from ACCOUNT_UPDATE.P array."""
        now_ms = timestamp_ms or int(time.time() * 1000)
        async with self._factory() as session:
            for p in positions:
                symbol = p.get("s", "")
                market_type = p.get("mt", "futures")
                side = p.get("ps", "BOTH")
                qty = str(p.get("pa", "0"))
                entry_price = str(p.get("ep", "0"))
                unrealized_pnl = str(p.get("up", "0"))
                if not symbol:
                    continue
                stmt = select(Position).where(
                    Position.account_id == self._account_id,
                    Position.symbol == symbol,
                    Position.market_type == market_type,
                    Position.side == side,
                )
                existing = (await session.execute(stmt)).scalar_one_or_none()
                if existing:
                    existing.quantity = qty
                    existing.entry_price = entry_price
                    existing.unrealized_pnl = unrealized_pnl
                    existing.updated_at_ms = now_ms
                else:
                    session.add(Position(
                        account_id=self._account_id,
                        symbol=symbol,
                        market_type=market_type,
                        side=side,
                        quantity=qty,
                        entry_price=entry_price,
                        unrealized_pnl=unrealized_pnl,
                        updated_at_ms=now_ms,
                    ))
            await session.commit()

        await self._cache_positions()

    async def _cache_positions(self) -> None:
        async with self._factory() as session:
            stmt = select(Position).where(Position.account_id == self._account_id)
            rows = (await session.execute(stmt)).scalars().all()
        data = [
            {
                "symbol": r.symbol, "market_type": r.market_type, "side": r.side,
                "quantity": str(r.quantity), "entry_price": str(r.entry_price),
                "unrealized_pnl": str(r.unrealized_pnl or "0"),
                "updated_at_ms": r.updated_at_ms,
            }
            for r in rows
            if Decimal(str(r.quantity or 0)) != 0
        ]
        key = RedisKeys.account_live_positions(self._account_id_str)
        await self._redis.set(key, json.dumps(data), ex=_POSITION_CACHE_TTL_S)

    # ── Orders + Fills ────────────────────────────────────────────────────────

    async def upsert_order_from_execution_report(self, evt: dict) -> None:
        """Handle Binance spot executionReport event."""
        client_order_id = evt.get("c", "")
        exchange_order_id = str(evt.get("i", ""))
        symbol = evt.get("s", "")
        side = evt.get("S", "BUY")
        order_type = evt.get("o", "MARKET")
        status = evt.get("X", "NEW")
        qty = str(evt.get("q", "0"))
        filled_qty = str(evt.get("z", "0"))
        price = str(evt.get("p", "0")) or None
        avg_fill = str(evt.get("L", "0")) or None
        time_in_force = evt.get("f", "GTC")
        created_ms = int(evt.get("O", time.time() * 1000))
        updated_ms = int(evt.get("T", time.time() * 1000))

        if not client_order_id or not symbol:
            return

        async with self._factory() as session:
            stmt = select(Order).where(Order.client_order_id == client_order_id)
            order = (await session.execute(stmt)).scalar_one_or_none()
            if order:
                order.status = status
                order.filled_qty = filled_qty
                order.avg_fill_price = avg_fill if avg_fill and avg_fill != "0" else order.avg_fill_price
                order.exchange_order_id = exchange_order_id
                order.updated_at_ms = updated_ms
            else:
                order = Order(
                    account_id=self._account_id,
                    exchange_order_id=exchange_order_id,
                    client_order_id=client_order_id,
                    symbol=symbol,
                    market_type="spot",
                    side=side,
                    order_type=order_type,
                    status=status,
                    quantity=qty,
                    filled_qty=filled_qty,
                    price=price if price and price != "0" else None,
                    avg_fill_price=avg_fill if avg_fill and avg_fill != "0" else None,
                    time_in_force=time_in_force,
                    created_at_ms=created_ms,
                    updated_at_ms=updated_ms,
                )
                session.add(order)
            await session.flush()
            order_db_id = order.id

            # Persist fill if this is a trade execution
            exec_type = evt.get("x", "")
            trade_id = str(evt.get("t", -1))
            if exec_type == "TRADE" and trade_id != "-1":
                fill_qty = str(evt.get("l", "0"))
                fill_price = str(evt.get("L", "0"))
                commission = str(evt.get("n", "0"))
                commission_asset = str(evt.get("N", "USDT") or "USDT")
                quote_qty = str(Decimal(fill_price) * Decimal(fill_qty))

                fill_stmt = select(Fill).where(
                    Fill.account_id == self._account_id,
                    Fill.exchange_trade_id == trade_id,
                )
                if not (await session.execute(fill_stmt)).scalar_one_or_none():
                    session.add(Fill(
                        order_id=order_db_id,
                        account_id=self._account_id,
                        exchange_trade_id=trade_id,
                        symbol=symbol,
                        side=side,
                        price=fill_price,
                        qty=fill_qty,
                        quote_qty=quote_qty,
                        commission=commission,
                        commission_asset=commission_asset,
                        is_maker=bool(evt.get("m", False)),
                        timestamp_ms=updated_ms,
                    ))
            await session.commit()

    async def upsert_order_from_futures_report(self, evt_o: dict) -> None:
        """Handle Binance futures ORDER_TRADE_UPDATE.o sub-object."""
        client_order_id = evt_o.get("c", "")
        exchange_order_id = str(evt_o.get("i", ""))
        symbol = evt_o.get("s", "")
        side = evt_o.get("S", "BUY")
        order_type = evt_o.get("o", "MARKET")
        status = evt_o.get("X", "NEW")
        qty = str(evt_o.get("q", "0"))
        filled_qty = str(evt_o.get("z", "0"))
        price = str(evt_o.get("p", "0")) or None
        avg_fill = str(evt_o.get("ap", "0")) or None
        time_in_force = evt_o.get("f", "GTC")
        created_ms = int(evt_o.get("T", time.time() * 1000))
        updated_ms = created_ms

        if not client_order_id or not symbol:
            return

        async with self._factory() as session:
            stmt = select(Order).where(Order.client_order_id == client_order_id)
            order = (await session.execute(stmt)).scalar_one_or_none()
            if order:
                order.status = status
                order.filled_qty = filled_qty
                order.exchange_order_id = exchange_order_id
                order.avg_fill_price = avg_fill if avg_fill and avg_fill != "0" else order.avg_fill_price
                order.updated_at_ms = updated_ms
            else:
                order = Order(
                    account_id=self._account_id,
                    exchange_order_id=exchange_order_id,
                    client_order_id=client_order_id,
                    symbol=symbol,
                    market_type="futures",
                    side=side,
                    order_type=order_type,
                    status=status,
                    quantity=qty,
                    filled_qty=filled_qty,
                    price=price if price and price != "0" else None,
                    avg_fill_price=avg_fill if avg_fill and avg_fill != "0" else None,
                    time_in_force=time_in_force,
                    created_at_ms=created_ms,
                    updated_at_ms=updated_ms,
                )
                session.add(order)
            await session.flush()
            order_db_id = order.id

            exec_type = evt_o.get("x", "")
            trade_id = str(evt_o.get("t", -1))
            if exec_type == "TRADE" and trade_id != "-1":
                fill_qty = str(evt_o.get("l", "0"))
                fill_price = str(evt_o.get("L", "0"))
                commission = str(evt_o.get("n", "0"))
                commission_asset = str(evt_o.get("N", "USDT") or "USDT")
                realized_pnl = str(evt_o.get("rp", "0")) or None
                try:
                    quote_qty = str(Decimal(fill_price) * Decimal(fill_qty))
                except Exception:
                    quote_qty = "0"

                fill_stmt = select(Fill).where(
                    Fill.account_id == self._account_id,
                    Fill.exchange_trade_id == trade_id,
                )
                if not (await session.execute(fill_stmt)).scalar_one_or_none():
                    session.add(Fill(
                        order_id=order_db_id,
                        account_id=self._account_id,
                        exchange_trade_id=trade_id,
                        symbol=symbol,
                        side=side,
                        price=fill_price,
                        qty=fill_qty,
                        quote_qty=quote_qty,
                        commission=commission,
                        commission_asset=commission_asset,
                        realized_pnl=realized_pnl,
                        is_maker=bool(evt_o.get("m", False)),
                        timestamp_ms=updated_ms,
                    ))
            await session.commit()
