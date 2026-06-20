from __future__ import annotations

import asyncio
import uuid

from shared.redis.client import RedisClient, get_redis_client, stream_read_group
from shared.redis.keys import RedisKeys
from shared.redis.streams import StreamNames
from shared.schemas.enums import ApprovalLevel, TradingMode
from shared.schemas.execution import ExecutionRequest
from shared.schemas.strategy import TradeIntent
from shared.utils.logging import get_logger
from services.execution.account.context import AccountContextLoader
from services.execution.adapter.base import ExecutionAdapterBase
from services.execution.adapter.paper import PaperExecutionAdapter
from services.execution.config import ExecutionSettings
from services.execution.controls.cooldown import CooldownControl
from services.execution.events.publisher import ExecutionEventPublisher
from services.execution.jobs.client_order_id import make_client_order_id
from services.execution.persistence.repository import ExecutionRepository
from services.execution.risk.engine import ExecutionRiskEngine

log = get_logger("execution.consumer")


class ExecutionConsumer:
    """Reads TradeIntents from stream:strategy:intents, gates them through risk
    checks, submits to the adapter, persists all state, and publishes structured
    events to stream:execution:events.

    The repository and adapter are optional so unit tests can operate without
    a real database or exchange connection.
    """

    def __init__(
        self,
        settings: ExecutionSettings | None = None,
        redis: RedisClient | None = None,
        repository: ExecutionRepository | None = None,
        adapter: ExecutionAdapterBase | None = None,
        risk_engine: ExecutionRiskEngine | None = None,
        context_loader: AccountContextLoader | None = None,
    ) -> None:
        self._settings = settings or ExecutionSettings()
        self._redis = redis or get_redis_client()
        self._repository = repository
        self._adapter = adapter or PaperExecutionAdapter()
        self._risk_engine = risk_engine or ExecutionRiskEngine(self._redis)
        self._context_loader = context_loader
        self._publisher = ExecutionEventPublisher(self._redis)
        self._cooldown = CooldownControl(self._redis)
        self._running = False
        self._jobs_processed: int = 0
        self._jobs_blocked: int = 0

    # ── Public ────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        log.info("execution consumer starting")
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("consumer tick error", exc_info=exc)
                await asyncio.sleep(1.0)

    async def stop(self) -> None:
        self._running = False

    @property
    def jobs_processed(self) -> int:
        return self._jobs_processed

    @property
    def jobs_blocked(self) -> int:
        return self._jobs_blocked

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        messages = await stream_read_group(
            self._redis,
            StreamNames.STRATEGY_INTENTS,
            self._settings.consumer_group,
            self._settings.consumer_name,
            count=self._settings.batch_size,
            block_ms=self._settings.block_ms,
        )
        for _stream, entries in (messages or []):
            for msg_id, fields in entries:
                await self._process(msg_id, fields)

    async def _process(self, msg_id: str, fields: dict) -> None:
        raw = fields.get("intent", "")
        if not raw:
            await self._ack(msg_id)
            return

        try:
            intent = TradeIntent.model_validate_json(raw)
        except Exception as exc:
            log.warning("malformed intent", exc_info=exc)
            await self._ack(msg_id)
            return

        account_id = self._settings.default_account_id or "default"
        client_order_id = make_client_order_id(
            str(intent.intent_id), account_id, intent.symbol, intent.side.value
        )
        job_id = uuid.uuid4()

        # ── Deduplication via Redis SETNX ─────────────────────────────────────
        lock_key = RedisKeys.job_lock(client_order_id)
        acquired = await self._redis.set(
            lock_key, str(job_id), nx=True, ex=self._settings.job_lock_ttl_s
        )
        if not acquired:
            await self._publisher.publish(
                "duplicate_blocked",
                str(job_id),
                {
                    "client_order_id": client_order_id,
                    "intent_id": str(intent.intent_id),
                    "reason": "duplicate_job",
                },
            )
            await self._ack(msg_id)
            return

        # ── Build request ─────────────────────────────────────────────────────
        request = ExecutionRequest(
            job_id=job_id,
            trade_intent=intent,
            user_id=self._settings.default_user_id or "system",
            account_id=account_id,
            trading_mode=TradingMode(self._settings.trading_mode.value),
            approval_level=ApprovalLevel(self._settings.default_approval_level),
        )

        # ── Persist job (queued) ──────────────────────────────────────────────
        if self._repository:
            try:
                await self._repository.create_job(job_id, request, client_order_id)
            except Exception as exc:
                log.error("job persist error", exc_info=exc)

        await self._publisher.publish("job_queued", str(job_id), {
            "client_order_id": client_order_id,
            "symbol": intent.symbol,
            "side": intent.side.value,
            "intent_id": str(intent.intent_id),
        })

        # ── Account context + live risk data ──────────────────────────────────
        daily_loss_usd = None
        open_positions = None
        limits_override = None

        if self._context_loader is not None:
            try:
                ctx = await self._context_loader.load(account_id)
                if ctx is not None:
                    limits_override = ctx.limits
            except Exception as exc:
                log.warning("account context load failed", exc_info=exc)

        if self._repository is not None:
            try:
                daily_loss_usd = await self._repository.get_daily_realized_loss_usd(account_id)
                open_positions = await self._repository.get_open_positions_count(account_id)
            except Exception as exc:
                log.warning("risk data query failed", exc_info=exc)

        # ── Risk gate ─────────────────────────────────────────────────────────
        try:
            decision = await self._risk_engine.evaluate(
                request,
                daily_loss_usd=daily_loss_usd,
                open_positions=open_positions,
                limits_override=limits_override,
            )
        except Exception as exc:
            log.error("risk engine error", exc_info=exc)
            await self._on_failed(job_id, client_order_id, "risk_engine_error", str(exc))
            await self._ack(msg_id)
            return

        if not decision.passed:
            self._jobs_blocked += 1
            await self._on_blocked(job_id, client_order_id, decision.failures, decision.metadata)
            await self._ack(msg_id)
            return

        # ── Approved ──────────────────────────────────────────────────────────
        if self._repository:
            try:
                await self._repository.update_job_status(
                    job_id, "approved", {"checks": decision.checks}
                )
            except Exception as exc:
                log.error("status update error (approved)", exc_info=exc)

        await self._publisher.publish("job_approved", str(job_id), {
            "checks_passed": list(decision.checks.keys()),
        })

        # ── Submit to adapter ─────────────────────────────────────────────────
        try:
            response = await self._adapter.submit(request, client_order_id)
        except Exception as exc:
            log.error("adapter submit error", exc_info=exc)
            await self._on_failed(job_id, client_order_id, "adapter_error", str(exc))
            await self._ack(msg_id)
            return

        if not response.success:
            await self._on_failed(
                job_id, client_order_id, "adapter_rejected", response.error or "unknown"
            )
            await self._ack(msg_id)
            return

        # ── submitted → acknowledged → filled ─────────────────────────────────
        for status in ("submitted", "acknowledged"):
            if self._repository:
                try:
                    await self._repository.update_job_status(job_id, status)
                except Exception as exc:
                    log.error("status update error (%s)", status, exc_info=exc)
            await self._publisher.publish(f"job_{status}", str(job_id), {
                "exchange_order_id": response.exchange_order_id,
            })

        # ── Persist order + fill ──────────────────────────────────────────────
        if self._repository:
            try:
                order_id = await self._repository.create_order(
                    job_id, request, client_order_id, response
                )
                await self._repository.save_fill(order_id, request, response)
                await self._repository.update_job_status(
                    job_id,
                    "filled",
                    result_json={
                        "exchange_order_id": response.exchange_order_id,
                        "fill_price": str(response.fill_price),
                        "fill_quantity": str(response.fill_quantity),
                        "adapter": self._adapter.adapter_name(),
                    },
                )
            except Exception as exc:
                log.error("fill persist error", exc_info=exc)

        await self._publisher.publish("job_filled", str(job_id), {
            "exchange_order_id": response.exchange_order_id,
            "fill_price": str(response.fill_price),
            "fill_quantity": str(response.fill_quantity),
            "commission": str(response.commission),
            "adapter": self._adapter.adapter_name(),
        })

        # ── Post-fill cooldown ────────────────────────────────────────────────
        await self._cooldown.set_cooldown(
            account_id, intent.symbol, self._settings.symbol_cooldown_s
        )

        self._jobs_processed += 1
        await self._ack(msg_id)

    async def _on_blocked(
        self,
        job_id: uuid.UUID,
        client_order_id: str,
        failures: list[str],
        metadata: dict,
    ) -> None:
        event_type = "job_blocked"
        if "kill_switch_active" in failures:
            event_type = "kill_switch_blocked"
        elif "symbol_on_cooldown" in failures:
            event_type = "cooldown_blocked"

        if self._repository:
            try:
                await self._repository.update_job_status(
                    job_id,
                    "blocked",
                    event_data={"failures": failures, "metadata": metadata},
                )
            except Exception as exc:
                log.error("blocked status persist error", exc_info=exc)

        await self._publisher.publish(event_type, str(job_id), {
            "failures": failures,
            "client_order_id": client_order_id,
            **metadata,
        })

    async def _on_failed(
        self,
        job_id: uuid.UUID,
        client_order_id: str,
        reason: str,
        error: str,
    ) -> None:
        if self._repository:
            try:
                await self._repository.update_job_status(
                    job_id, "failed",
                    event_data={"reason": reason, "error": error},
                    error=error,
                )
            except Exception as exc:
                log.error("failed status persist error", exc_info=exc)

        await self._publisher.publish("job_failed", str(job_id), {
            "reason": reason,
            "error": error,
            "client_order_id": client_order_id,
        })

    async def _ack(self, msg_id: str) -> None:
        try:
            await self._redis.xack(
                StreamNames.STRATEGY_INTENTS,
                self._settings.consumer_group,
                msg_id,
            )
        except Exception as exc:
            log.warning("xack failed", exc_info=exc)
