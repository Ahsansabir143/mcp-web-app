"""Normalized private-event consumer for execution reconciliation.

Reads USER_ORDER events from stream:binance:normalized and matches them against
execution jobs to update fill records, partial-fill state, and terminal status.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

from shared.redis.client import RedisClient, stream_read_group
from shared.redis.streams import StreamNames
from shared.schemas.enums import EventType
from shared.schemas.events import NormalizedEvent
from shared.utils.logging import get_logger
from services.execution.config import ExecutionSettings
from services.execution.events.publisher import ExecutionEventPublisher
from services.execution.jobs.lifecycle import can_transition, is_terminal
from services.execution.persistence.repository import ExecutionRepository
from services.execution.reconciliation.incident import IncidentLogger

log = get_logger("execution.reconciliation.event_consumer")


class NormalizedEventConsumer:
    """Reads USER_ORDER events from the normalized stream and drives execution
    state from acknowledged → partially_filled → filled (or canceled/failed).

    The repository is optional so this class can be constructed in test mode.
    """

    def __init__(
        self,
        settings: ExecutionSettings,
        redis: RedisClient,
        publisher: ExecutionEventPublisher,
        repository: ExecutionRepository | None = None,
        incident_logger: IncidentLogger | None = None,
    ) -> None:
        self._settings = settings
        self._redis = redis
        self._publisher = publisher
        self._repository = repository
        self._incident_logger = incident_logger
        self._running = False
        self._orphans_seen: int = 0
        self._fills_processed: int = 0

    @property
    def orphans_seen(self) -> int:
        return self._orphans_seen

    @property
    def fills_processed(self) -> int:
        return self._fills_processed

    async def start(self) -> None:
        self._running = True
        log.info("normalized event consumer starting")
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("reconcile consumer tick error", exc_info=exc)
                await asyncio.sleep(1.0)

    async def stop(self) -> None:
        self._running = False

    # ── Tick ──────────────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        messages = await stream_read_group(
            self._redis,
            StreamNames.NORMALIZED,
            self._settings.recon_consumer_group,
            self._settings.recon_consumer_name,
            count=self._settings.batch_size,
            block_ms=self._settings.block_ms,
        )
        for _stream, entries in (messages or []):
            for msg_id, fields in entries:
                await self._process(msg_id, fields)

    async def _process(self, msg_id: str, fields: dict) -> None:
        raw = fields.get("event", "")
        if not raw:
            await self._ack(msg_id)
            return
        try:
            event = NormalizedEvent.model_validate_json(raw)
        except Exception as exc:
            log.warning("malformed normalized event", exc_info=exc)
            await self._ack(msg_id)
            return

        if event.event_type == EventType.USER_ORDER:
            await self._handle_user_order(event)

        await self._ack(msg_id)

    # ── USER_ORDER handling ───────────────────────────────────────────────────

    async def _handle_user_order(self, event: NormalizedEvent) -> None:
        data = event.data
        client_order_id: str = data.get("client_order_id", "")
        exchange_order_id: str = data.get("exchange_order_id", "")
        order_status: str = data.get("order_status", "")

        # Only reconcile orders we submitted (tp2- prefix)
        if not client_order_id.startswith("tp2-"):
            self._orphans_seen += 1
            await self._publisher.publish(
                "orphan_exchange_update",
                "unknown",
                {
                    "client_order_id": client_order_id,
                    "exchange_order_id": exchange_order_id,
                    "order_status": order_status,
                    "reason": "not_our_order",
                },
            )
            return

        if not self._repository:
            return

        job = await self._repository.get_job_by_client_order_id(client_order_id)
        if job is None:
            self._orphans_seen += 1
            await self._publisher.publish(
                "orphan_exchange_update",
                "unknown",
                {
                    "client_order_id": client_order_id,
                    "order_status": order_status,
                    "reason": "job_not_found",
                },
            )
            if self._incident_logger:
                await self._incident_logger.log_incident(
                    "orphan_exchange_update",
                    f"Exchange update for unknown client_order_id={client_order_id}",
                    severity="warning",
                    context={
                        "client_order_id": client_order_id,
                        "order_status": order_status,
                    },
                )
            return

        if is_terminal(job.status):
            self._orphans_seen += 1
            await self._publisher.publish(
                "orphan_exchange_update",
                str(job.id),
                {
                    "reason": "already_terminal",
                    "current_status": job.status,
                    "exchange_status": order_status,
                    "client_order_id": client_order_id,
                },
            )
            return

        cumulative_filled_qty = Decimal(data.get("filled_qty", "0"))
        orig_qty = Decimal(data.get("orig_qty", "0"))
        avg_price = Decimal(data.get("avg_price", "0"))
        commission = Decimal(data.get("commission", "0"))
        commission_asset: str = data.get("commission_asset", "USDT")
        realized_pnl = Decimal(data.get("realized_pnl", "0"))
        trade_time_ms: int = int(data.get("trade_time_ms", 0))

        if order_status in ("CANCELED", "REJECTED", "EXPIRED"):
            await self._handle_cancel(
                job, exchange_order_id, order_status, trade_time_ms
            )
        elif order_status == "PARTIALLY_FILLED":
            await self._handle_partial_fill(
                job,
                client_order_id,
                exchange_order_id,
                cumulative_filled_qty,
                orig_qty,
                avg_price,
                commission,
                commission_asset,
                realized_pnl,
                trade_time_ms,
                event.symbol,
            )
        elif order_status == "FILLED":
            await self._handle_full_fill(
                job,
                client_order_id,
                exchange_order_id,
                cumulative_filled_qty,
                orig_qty,
                avg_price,
                commission,
                commission_asset,
                realized_pnl,
                trade_time_ms,
                event.symbol,
            )

    # ── Cancel / reject ───────────────────────────────────────────────────────

    async def _handle_cancel(
        self,
        job,
        exchange_order_id: str,
        order_status: str,
        trade_time_ms: int,
    ) -> None:
        if not can_transition(job.status, "canceled"):
            await self._publisher.publish(
                "reconciliation_mismatch",
                str(job.id),
                {
                    "reason": f"cannot_cancel_from_{job.status}",
                    "exchange_status": order_status,
                },
            )
            return

        if self._repository:
            await self._repository.update_job_status(
                job.id,
                "canceled",
                event_data={
                    "exchange_order_id": exchange_order_id,
                    "exchange_status": order_status,
                    "trade_time_ms": trade_time_ms,
                    "source": "reconciliation",
                },
            )
        await self._publisher.publish(
            "job_canceled",
            str(job.id),
            {
                "exchange_order_id": exchange_order_id,
                "exchange_status": order_status,
            },
        )

    # ── Partial fill ──────────────────────────────────────────────────────────

    async def _handle_partial_fill(
        self,
        job,
        client_order_id: str,
        exchange_order_id: str,
        cumulative_filled_qty: Decimal,
        orig_qty: Decimal,
        avg_price: Decimal,
        commission: Decimal,
        commission_asset: str,
        realized_pnl: Decimal,
        trade_time_ms: int,
        symbol: str,
    ) -> None:
        order = await self._repository.get_order_by_client_order_id(client_order_id)
        if order is None:
            await self._publisher.publish(
                "reconciliation_mismatch",
                str(job.id),
                {
                    "reason": "order_record_not_found",
                    "client_order_id": client_order_id,
                },
            )
            return

        prev_filled = Decimal(order.filled_qty or "0")
        fill_leg_qty = cumulative_filled_qty - prev_filled
        if fill_leg_qty <= Decimal("0"):
            # Duplicate event — already recorded this cumulative level
            return

        exchange_trade_id = f"{exchange_order_id}:t{trade_time_ms}"
        existing = await self._repository.get_fill_by_exchange_trade_id(
            job.account_id, exchange_trade_id
        )
        if existing is not None:
            return  # Idempotent — skip duplicate fill record

        await self._repository.insert_fill(
            order_id=order.id,
            account_id=job.account_id,
            exchange_trade_id=exchange_trade_id,
            symbol=symbol or order.symbol,
            side=order.side,
            price=avg_price,
            qty=fill_leg_qty,
            commission=commission,
            commission_asset=commission_asset,
            trade_time_ms=trade_time_ms,
            realized_pnl=realized_pnl,
        )
        self._fills_processed += 1

        new_order_status = (
            "FILLED" if cumulative_filled_qty >= orig_qty else "PARTIALLY_FILLED"
        )
        await self._repository.update_order_filled_qty(
            order.id, cumulative_filled_qty, avg_price, new_order_status
        )

        # Transition job status to partially_filled only if not already there
        if job.status not in ("partially_filled",) and can_transition(
            job.status, "partially_filled"
        ):
            await self._repository.update_job_status(
                job.id,
                "partially_filled",
                event_data={
                    "exchange_order_id": exchange_order_id,
                    "cumulative_filled_qty": str(cumulative_filled_qty),
                    "fill_leg_qty": str(fill_leg_qty),
                    "source": "reconciliation",
                },
            )

        await self._publisher.publish(
            "job_partially_filled",
            str(job.id),
            {
                "exchange_order_id": exchange_order_id,
                "fill_leg_qty": str(fill_leg_qty),
                "cumulative_filled_qty": str(cumulative_filled_qty),
                "orig_qty": str(orig_qty),
                "avg_price": str(avg_price),
                "exchange_trade_id": exchange_trade_id,
            },
        )

        if cumulative_filled_qty >= orig_qty:
            await self._finalize_fill(job, exchange_order_id, cumulative_filled_qty, avg_price)

    # ── Full fill ─────────────────────────────────────────────────────────────

    async def _handle_full_fill(
        self,
        job,
        client_order_id: str,
        exchange_order_id: str,
        cumulative_filled_qty: Decimal,
        orig_qty: Decimal,
        avg_price: Decimal,
        commission: Decimal,
        commission_asset: str,
        realized_pnl: Decimal,
        trade_time_ms: int,
        symbol: str,
    ) -> None:
        order = await self._repository.get_order_by_client_order_id(client_order_id)
        if order is None:
            await self._publisher.publish(
                "reconciliation_mismatch",
                str(job.id),
                {
                    "reason": "order_record_not_found",
                    "client_order_id": client_order_id,
                },
            )
            return

        prev_filled = Decimal(order.filled_qty or "0")
        fill_leg_qty = cumulative_filled_qty - prev_filled

        if fill_leg_qty > Decimal("0"):
            exchange_trade_id = f"{exchange_order_id}:t{trade_time_ms}"
            existing = await self._repository.get_fill_by_exchange_trade_id(
                job.account_id, exchange_trade_id
            )
            if existing is None:
                await self._repository.insert_fill(
                    order_id=order.id,
                    account_id=job.account_id,
                    exchange_trade_id=exchange_trade_id,
                    symbol=symbol or order.symbol,
                    side=order.side,
                    price=avg_price,
                    qty=fill_leg_qty,
                    commission=commission,
                    commission_asset=commission_asset,
                    trade_time_ms=trade_time_ms,
                    realized_pnl=realized_pnl,
                )
                self._fills_processed += 1

        await self._repository.update_order_filled_qty(
            order.id, cumulative_filled_qty, avg_price, "FILLED"
        )
        await self._finalize_fill(job, exchange_order_id, cumulative_filled_qty, avg_price)

    # ── Shared terminal-fill handler ──────────────────────────────────────────

    async def _finalize_fill(
        self,
        job,
        exchange_order_id: str,
        filled_qty: Decimal,
        avg_price: Decimal,
    ) -> None:
        if not can_transition(job.status, "filled"):
            return

        if self._repository:
            await self._repository.update_job_status(
                job.id,
                "filled",
                event_data={
                    "exchange_order_id": exchange_order_id,
                    "filled_qty": str(filled_qty),
                    "avg_price": str(avg_price),
                    "source": "reconciliation",
                },
                result_json={
                    "exchange_order_id": exchange_order_id,
                    "filled_qty": str(filled_qty),
                    "avg_price": str(avg_price),
                    "source": "reconciliation",
                },
            )
        await self._publisher.publish(
            "job_reconciled",
            str(job.id),
            {
                "exchange_order_id": exchange_order_id,
                "filled_qty": str(filled_qty),
                "avg_price": str(avg_price),
            },
        )

    # ── ACK ───────────────────────────────────────────────────────────────────

    async def _ack(self, msg_id: str) -> None:
        try:
            await self._redis.xack(
                StreamNames.NORMALIZED,
                self._settings.recon_consumer_group,
                msg_id,
            )
        except Exception as exc:
            log.warning("xack failed (recon consumer)", exc_info=exc)
