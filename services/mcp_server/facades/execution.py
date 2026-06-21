"""Execution façade — history reads + paper trade submission."""
from __future__ import annotations

import time
import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.db.models.account import Fill as FillModel, Order as OrderModel
from shared.db.models.audit import IncidentLog
from shared.db.models.execution import ExecutionJob as ExecutionJobModel
from shared.redis.client import RedisClient, stream_publish
from shared.redis.keys import RedisKeys
from shared.redis.streams import StreamNames
from shared.schemas.enums import MarketType, OrderSide, OrderType, StrategyState
from shared.schemas.strategy import TradeIntent
from shared.utils.logging import get_logger

log = get_logger("mcp_server.facades.execution")

# States from which intents can be manually requested via MCP
_EMIT_STATES = frozenset({
    StrategyState.PAPER_ACTIVE.value,
    StrategyState.ASSISTED_LIVE.value,
    StrategyState.BOUNDED_AUTO_LIVE.value,
})


# ── Execution history ─────────────────────────────────────────────────────────


async def get_recent_executions(
    session_factory: async_sessionmaker[AsyncSession],
    strategy_id: str | None = None,
    symbol: str | None = None,
    limit: int = 20,
) -> list[dict]:
    limit = min(max(1, limit), 100)
    async with session_factory() as session:
        stmt = (
            select(ExecutionJobModel)
            .order_by(desc(ExecutionJobModel.created_at))
            .limit(limit)
        )
        if symbol:
            stmt = stmt.where(ExecutionJobModel.symbol == symbol)
        if strategy_id:
            try:
                sid = uuid.UUID(strategy_id)
                stmt = stmt.where(ExecutionJobModel.strategy_id == sid)
            except ValueError:
                pass

        result = await session.execute(stmt)
        jobs = result.scalars().all()

        rows = []
        for job in jobs:
            row: dict = {
                "job_id": str(job.id),
                "status": job.status,
                "symbol": job.symbol,
                "side": job.side,
                "trading_mode": job.trading_mode,
                "created_at": job.created_at.isoformat() if job.created_at else None,
            }
            if job.result_json:
                row["result"] = {
                    k: v
                    for k, v in job.result_json.items()
                    if k in ("fill_price", "fill_quantity", "exchange_order_id", "adapter")
                }
            if job.error:
                row["error"] = job.error
            rows.append(row)
        return rows


# ── Incidents ─────────────────────────────────────────────────────────────────


async def get_incidents(
    session_factory: async_sessionmaker[AsyncSession],
    symbol: str | None = None,
    since_ts: int | None = None,
    limit: int = 50,
) -> list[dict]:
    limit = min(max(1, limit), 200)
    async with session_factory() as session:
        stmt = (
            select(IncidentLog)
            .order_by(desc(IncidentLog.created_at))
            .limit(limit)
        )
        if since_ts:
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(since_ts / 1000, tz=timezone.utc)
            stmt = stmt.where(IncidentLog.created_at >= dt)

        result = await session.execute(stmt)
        incidents = result.scalars().all()

    rows = []
    for inc in incidents:
        # symbol filter applied in Python since context is in JSON column
        if symbol and inc.context.get("symbol", "") != symbol:
            continue
        rows.append({
            "id": str(inc.id),
            "incident_type": inc.incident_type,
            "severity": inc.severity,
            "description": inc.description,
            "job_id": str(inc.job_id) if inc.job_id else None,
            "context": inc.context,
            "resolved": inc.resolved,
            "created_at": inc.created_at.isoformat() if inc.created_at else None,
        })
    return rows


# ── Price resolution helper ───────────────────────────────────────────────────


async def _resolve_price_from_redis(
    redis: RedisClient,
    market_type: str,
    symbol: str,
) -> str | None:
    """Return price string for market_type/symbol from Redis, or None.

    Priority order matches get_symbol_snapshot and PaperExecutionAdapter:
      1. analytics snapshot  (market_state.price — canonical, refreshed every ~1 s)
      2. market price key    (per-trade write, 60 s TTL)
      3. book ticker mid     (10 s TTL)
    """
    import json as _json

    # 1. Analytics snapshot (same source as get_symbol_snapshot)
    analytics_raw = await redis.get(RedisKeys.analytics_snapshot(market_type, symbol))
    if analytics_raw:
        try:
            data = _json.loads(analytics_raw)
            ms = data.get("market_state") or {}
            p = ms.get("price")
            if p is not None and str(p) not in ("", "0", "None"):
                candidate = Decimal(str(p))
                if candidate > 0:
                    return str(candidate)
        except Exception:
            pass

    # 2. Market price key
    price_raw = await redis.get(RedisKeys.market_price(market_type, symbol))
    if price_raw:
        try:
            pd = _json.loads(price_raw)
            price_str = str(pd.get("price") or pd.get("mark_price") or "")
            if price_str and price_str not in ("", "0"):
                candidate = Decimal(price_str)
                if candidate > 0:
                    return price_str
        except Exception:
            pass

    # 3. Book ticker mid-price
    book_raw = await redis.get(RedisKeys.market_book_ticker(market_type, symbol))
    if book_raw:
        try:
            bk = _json.loads(book_raw)
            bid = bk.get("bid_price")
            ask = bk.get("ask_price")
            if bid and ask:
                mid = (Decimal(str(bid)) + Decimal(str(ask))) / 2
                if mid > 0:
                    return str(mid)
        except Exception:
            pass

    return None


# ── Paper trade ───────────────────────────────────────────────────────────────


async def request_paper_trade(
    session_factory: async_sessionmaker[AsyncSession],
    redis: RedisClient,
    strategy_id: str,
    symbol: str,
    side: str,
    size_usd: float | None = None,
    size: float | None = None,
    reason: str = "",
) -> dict:
    """Construct and publish a paper-mode TradeIntent to stream:strategy:intents.

    Validates:
    - strategy exists and is in an intent-emit state
    - side is BUY or SELL
    - at least one of size_usd or size is provided
    - if only size_usd given, the current market price is read from Redis to
      derive size; if price is unavailable the caller must provide size

    Returns: intent metadata dict or error dict.
    """
    try:
        sid = uuid.UUID(strategy_id)
    except ValueError:
        return {"error": "invalid_strategy_id", "message": f"Invalid UUID: {strategy_id}"}

    # Validate side
    side_upper = side.upper()
    if side_upper not in ("BUY", "SELL"):
        return {"error": "invalid_side", "message": f"side must be BUY or SELL, got '{side}'"}

    # Validate size inputs
    if size_usd is None and size is None:
        return {
            "error": "missing_size",
            "message": "Provide at least one of size_usd or size.",
        }

    # Load strategy from DB
    from shared.db.models.strategy import Strategy as StrategyModel

    async with session_factory() as session:
        strategy = await session.get(StrategyModel, sid)

    if strategy is None:
        return {"error": "not_found", "message": f"Strategy {strategy_id} not found"}

    if strategy.state not in _EMIT_STATES:
        return {
            "error": "strategy_not_active",
            "message": (
                f"Strategy is in state '{strategy.state}'. "
                f"Must be in one of: {sorted(_EMIT_STATES)} to request paper trades."
            ),
        }

    # Derive size if only size_usd given
    try:
        decimal_size_usd = Decimal(str(size_usd)) if size_usd is not None else None
        decimal_size = Decimal(str(size)) if size is not None else None
    except InvalidOperation as exc:
        return {"error": "invalid_size", "message": str(exc)}

    if decimal_size is None:
        # Try to get current price from Redis for size derivation.
        # Priority: analytics snapshot → market price → book ticker.
        # On miss, also try the complementary market type (spot ↔ futures) as a
        # paper-mode fallback, since prices are nearly identical.
        market_type = strategy.market_type or "futures"
        price_str = await _resolve_price_from_redis(redis, market_type, symbol)

        if not price_str:
            # Cross-market fallback (spot ↔ futures) — paper mode only
            _FALLBACK = {"spot": "futures", "futures": "spot"}
            fallback_mtype = _FALLBACK.get(market_type)
            if fallback_mtype:
                price_str = await _resolve_price_from_redis(redis, fallback_mtype, symbol)

        if not price_str:
            try:
                async with session_factory() as _session:
                    incident = IncidentLog(
                        incident_type="paper_price_unavailable",
                        severity="error",
                        strategy_id=sid,
                        description=(
                            f"Price unavailable for {symbol}/{market_type}: "
                            "cannot derive size from size_usd"
                        ),
                        context={
                            "symbol": symbol,
                            "market_type": market_type,
                            "side": side_upper,
                            "size_usd": size_usd,
                            "strategy_id": strategy_id,
                            "sources_tried": [
                                "analytics_snapshot",
                                "market_price_key",
                                "book_ticker",
                                "analytics_snapshot_fallback_mtype",
                                "market_price_key_fallback_mtype",
                                "book_ticker_fallback_mtype",
                            ],
                        },
                    )
                    _session.add(incident)
                    await _session.commit()
            except Exception as exc:
                log.error("failed to log price_unavailable incident", exc_info=exc)
            return {
                "error": "price_unavailable",
                "message": (
                    f"Cannot derive size from size_usd={size_usd} — "
                    f"no price data available for {symbol}. "
                    "Please provide the 'size' parameter directly."
                ),
            }

        try:
            price = Decimal(price_str)
            if price <= 0:
                raise ValueError("price must be positive")
            decimal_size = (decimal_size_usd / price).quantize(Decimal("0.00001"))
        except Exception as exc:
            return {"error": "size_derivation_error", "message": str(exc)}

    # Build TradeIntent
    try:
        market_type_enum = MarketType(strategy.market_type or "futures")
    except ValueError:
        market_type_enum = MarketType.FUTURES

    intent = TradeIntent(
        strategy_id=sid,
        strategy_version=strategy.current_version,
        symbol=symbol,
        market_type=market_type_enum,
        side=OrderSide(side_upper),
        order_type=OrderType.MARKET,
        size=decimal_size,
        size_usd=decimal_size_usd,
        metadata={
            "source": "mcp_paper_trade",
            "reason": reason,
        },
    )

    # Publish to stream
    await stream_publish(
        redis,
        StreamNames.STRATEGY_INTENTS,
        {
            "intent": intent.model_dump_json(),
            "evaluation_id": "",
            "strategy_id": str(sid),
        },
    )

    return {
        "status": "queued",
        "mode": "paper",
        "intent_id": str(intent.intent_id),
        "strategy_id": strategy_id,
        "symbol": symbol,
        "side": side_upper,
        "size": str(decimal_size),
        "size_usd": str(decimal_size_usd) if decimal_size_usd else None,
        "note": (
            "Intent has been published to stream:strategy:intents and will be "
            "processed by the execution service. Paper mode enforced by the risk engine."
        ),
    }
