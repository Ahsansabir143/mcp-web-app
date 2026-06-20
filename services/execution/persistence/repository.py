from __future__ import annotations

import time
import uuid
from decimal import Decimal

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.db.models.execution import (
    ExecutionEvent as ExecutionEventModel,
    ExecutionJob as ExecutionJobModel,
)
from shared.db.models.account import (
    Fill as FillModel,
    Order as OrderModel,
    Position as PositionModel,
)
from shared.schemas.execution import ExecutionRequest
from services.execution.adapter.base import AdapterResponse


class ExecutionRepository:
    """All DB operations for the execution service.

    Each method opens its own short-lived session.  The repository is fully
    optional — all consumer logic is tested without it by passing None.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    # ── ExecutionJob ──────────────────────────────────────────────────────────

    async def create_job(
        self,
        job_id: uuid.UUID,
        request: ExecutionRequest,
        client_order_id: str,
    ) -> None:
        async with self._factory() as session:
            job = ExecutionJobModel(
                id=job_id,
                account_id=uuid.UUID(request.account_id) if request.account_id else uuid.uuid4(),
                trade_intent_id=request.trade_intent.intent_id,
                strategy_id=request.trade_intent.strategy_id,
                trading_mode=request.trading_mode.value,
                status="queued",
                deterministic_client_order_id=client_order_id,
                symbol=request.trade_intent.symbol,
                market_type=request.trade_intent.market_type.value,
                side=request.trade_intent.side.value,
                intent_json=request.trade_intent.model_dump(mode="json"),
            )
            session.add(job)
            event = ExecutionEventModel(
                job_id=job_id,
                event_type="job_queued",
                data={"client_order_id": client_order_id},
                timestamp_ms=int(time.time() * 1000),
            )
            session.add(event)
            await session.commit()

    async def get_job_by_client_order_id(
        self, client_order_id: str
    ) -> ExecutionJobModel | None:
        async with self._factory() as session:
            stmt = select(ExecutionJobModel).where(
                ExecutionJobModel.deterministic_client_order_id == client_order_id
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_job_status(
        self,
        job_id: uuid.UUID,
        new_status: str,
        event_data: dict | None = None,
        result_json: dict | None = None,
        error: str | None = None,
    ) -> None:
        async with self._factory() as session:
            job = await session.get(ExecutionJobModel, job_id)
            if job is None:
                return
            job.status = new_status
            if result_json is not None:
                job.result_json = result_json
            if error is not None:
                job.error = error
            event = ExecutionEventModel(
                job_id=job_id,
                event_type=f"job_{new_status}",
                data=event_data or {},
                timestamp_ms=int(time.time() * 1000),
            )
            session.add(event)
            await session.commit()

    # ── Order / Fill ──────────────────────────────────────────────────────────

    async def create_order(
        self,
        job_id: uuid.UUID,
        request: ExecutionRequest,
        client_order_id: str,
        response: AdapterResponse,
    ) -> uuid.UUID:
        async with self._factory() as session:
            order_id = uuid.uuid4()
            order = OrderModel(
                id=order_id,
                account_id=uuid.UUID(request.account_id) if request.account_id else uuid.uuid4(),
                job_id=job_id,
                exchange_order_id=response.exchange_order_id,
                client_order_id=client_order_id,
                symbol=request.trade_intent.symbol,
                market_type=request.trade_intent.market_type.value,
                side=request.trade_intent.side.value,
                order_type=request.trade_intent.order_type.value,
                status="FILLED" if response.success else "FAILED",
                quantity=str(request.trade_intent.size),
                filled_qty=str(response.fill_quantity or Decimal("0")),
                price=(
                    str(request.trade_intent.limit_price)
                    if request.trade_intent.limit_price
                    else None
                ),
                avg_fill_price=str(response.fill_price) if response.fill_price else None,
                reduce_only=request.trade_intent.reduce_only,
                time_in_force=request.trade_intent.time_in_force.value,
                created_at_ms=int(time.time() * 1000),
                updated_at_ms=int(time.time() * 1000),
            )
            session.add(order)
            await session.commit()
            return order_id

    async def save_fill(
        self,
        order_id: uuid.UUID,
        request: ExecutionRequest,
        response: AdapterResponse,
    ) -> None:
        if not response.fill_price or not response.fill_quantity:
            return
        async with self._factory() as session:
            fill_qty = response.fill_quantity
            fill_price = response.fill_price
            quote_qty = (fill_qty * fill_price).quantize(Decimal("0.00000001"))
            fill = FillModel(
                order_id=order_id,
                account_id=uuid.UUID(request.account_id) if request.account_id else uuid.uuid4(),
                exchange_trade_id=response.exchange_order_id or str(uuid.uuid4()),
                symbol=request.trade_intent.symbol,
                side=request.trade_intent.side.value,
                price=str(fill_price),
                qty=str(fill_qty),
                quote_qty=str(quote_qty),
                commission=str(response.commission or Decimal("0")),
                commission_asset=response.commission_asset,
                is_maker=False,
                timestamp_ms=int(time.time() * 1000),
            )
            session.add(fill)
            await session.commit()

    # ── Reconciliation queries ────────────────────────────────────────────────

    async def get_active_jobs(self) -> list[ExecutionJobModel]:
        """Return all non-terminal jobs for the reconciliation loop to inspect."""
        async with self._factory() as session:
            stmt = select(ExecutionJobModel).where(
                ExecutionJobModel.status.in_(
                    ["submitted", "acknowledged", "partially_filled"]
                )
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_order_by_client_order_id(
        self, client_order_id: str
    ) -> OrderModel | None:
        async with self._factory() as session:
            stmt = select(OrderModel).where(
                OrderModel.client_order_id == client_order_id
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_fill_by_exchange_trade_id(
        self, account_id: uuid.UUID, exchange_trade_id: str
    ) -> FillModel | None:
        async with self._factory() as session:
            stmt = select(FillModel).where(
                FillModel.account_id == account_id,
                FillModel.exchange_trade_id == exchange_trade_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def insert_fill(
        self,
        order_id: uuid.UUID,
        account_id: uuid.UUID,
        exchange_trade_id: str,
        symbol: str,
        side: str,
        price: Decimal,
        qty: Decimal,
        commission: Decimal,
        commission_asset: str,
        trade_time_ms: int,
        realized_pnl: Decimal | None = None,
    ) -> uuid.UUID:
        async with self._factory() as session:
            quote_qty = (price * qty).quantize(Decimal("0.00000001"))
            fill = FillModel(
                order_id=order_id,
                account_id=account_id,
                exchange_trade_id=exchange_trade_id,
                symbol=symbol,
                side=side,
                price=str(price),
                qty=str(qty),
                quote_qty=str(quote_qty),
                commission=str(commission),
                commission_asset=commission_asset,
                realized_pnl=str(realized_pnl) if realized_pnl is not None else None,
                is_maker=False,
                timestamp_ms=trade_time_ms,
            )
            session.add(fill)
            await session.commit()
            return fill.id

    async def update_order_filled_qty(
        self,
        order_id: uuid.UUID,
        filled_qty: Decimal,
        avg_fill_price: Decimal,
        new_status: str | None = None,
    ) -> None:
        async with self._factory() as session:
            order = await session.get(OrderModel, order_id)
            if order is None:
                return
            order.filled_qty = str(filled_qty)
            order.avg_fill_price = str(avg_fill_price)
            if new_status is not None:
                order.status = new_status
            order.updated_at_ms = int(time.time() * 1000)
            await session.commit()

    # ── Risk data queries ─────────────────────────────────────────────────────

    async def get_daily_realized_loss_usd(
        self, account_id: str, since_ms: int | None = None
    ) -> Decimal:
        """Return absolute USD loss from fills in [since_ms, now].

        Only negative realized_pnl rows are summed; NULL rows are excluded.
        Returns Decimal("0") when there are no loss fills or on invalid input.
        """
        try:
            account_uuid = uuid.UUID(account_id)
        except ValueError:
            return Decimal("0")

        if since_ms is None:
            now_utc = datetime.now(timezone.utc)
            midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            since_ms = int(midnight.timestamp() * 1000)

        async with self._factory() as session:
            stmt = select(
                func.coalesce(
                    func.sum(
                        case(
                            (FillModel.realized_pnl < 0, FillModel.realized_pnl),
                            else_=sa.literal(0),
                        )
                    ),
                    sa.literal(0),
                )
            ).where(
                FillModel.account_id == account_uuid,
                FillModel.timestamp_ms >= since_ms,
                FillModel.realized_pnl.isnot(None),
            )
            result = await session.execute(stmt)
            val = result.scalar() or 0
            # val is ≤ 0 (sum of losses); abs() gives the magnitude
            return abs(Decimal(str(val)))

    async def get_open_positions_count(self, account_id: str) -> int:
        """Return count of positions with quantity > 0 for the given account."""
        try:
            account_uuid = uuid.UUID(account_id)
        except ValueError:
            return 0

        async with self._factory() as session:
            stmt = (
                select(func.count())
                .select_from(PositionModel)
                .where(
                    PositionModel.account_id == account_uuid,
                    PositionModel.quantity > 0,
                )
            )
            result = await session.execute(stmt)
            return result.scalar() or 0
