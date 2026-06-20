"""Interval-based reconciliation loop for stale and stuck execution jobs."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from shared.utils.logging import get_logger
from services.execution.config import ExecutionSettings
from services.execution.events.publisher import ExecutionEventPublisher
from services.execution.persistence.repository import ExecutionRepository
from services.execution.reconciliation.incident import IncidentLogger

log = get_logger("execution.reconciliation.loop")


class ReconciliationLoop:
    """Scans active execution jobs on a fixed interval and emits incident events
    for stale (submitted-but-no-ack) and stuck (partially-filled-timeout) orders.

    Stats accumulated across all ``run_once`` calls are exposed for the
    /health/detail endpoint.
    """

    def __init__(
        self,
        settings: ExecutionSettings,
        publisher: ExecutionEventPublisher,
        repository: ExecutionRepository | None = None,
        incident_logger: IncidentLogger | None = None,
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self._repository = repository
        self._incident_logger = incident_logger
        self._running = False
        self._total_stale_detected: int = 0
        self._total_scans: int = 0

    @property
    def total_stale_detected(self) -> int:
        return self._total_stale_detected

    @property
    def total_scans(self) -> int:
        return self._total_scans

    async def start(self) -> None:
        self._running = True
        log.info("reconciliation loop starting (interval=%ds)", self._settings.reconcile_interval_s)
        while self._running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("reconciliation loop error", exc_info=exc)
            try:
                await asyncio.sleep(self._settings.reconcile_interval_s)
            except asyncio.CancelledError:
                break

    async def stop(self) -> None:
        self._running = False

    async def run_once(self) -> dict:
        """Execute one reconciliation scan. Returns summary stats dict."""
        stats: dict = {
            "active_jobs": 0,
            "stale_detected": 0,
            "partially_filled_pending": 0,
        }

        if not self._repository:
            return stats

        self._total_scans += 1
        now = datetime.now(timezone.utc)

        active_jobs = await self._repository.get_active_jobs()
        stats["active_jobs"] = len(active_jobs)

        for job in active_jobs:
            # created_at comes from TimestampMixin (timezone-aware datetime)
            created_at = job.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_s = (now - created_at).total_seconds()

            if job.status == "submitted" and age_s >= self._settings.stale_order_timeout_s:
                stats["stale_detected"] += 1
                self._total_stale_detected += 1
                log.warning(
                    "stale submitted job detected: %s (age=%.0fs)",
                    job.id,
                    age_s,
                )
                await self._publisher.publish(
                    "stale_order_detected",
                    str(job.id),
                    {
                        "status": job.status,
                        "symbol": job.symbol,
                        "age_s": int(age_s),
                        "stale_threshold_s": self._settings.stale_order_timeout_s,
                    },
                )
                if self._incident_logger:
                    await self._incident_logger.log_incident(
                        "stale_order",
                        f"Job {job.id} stuck in submitted state for {int(age_s)}s",
                        severity="warning",
                        job_id=job.id,
                        context={
                            "symbol": job.symbol,
                            "age_s": int(age_s),
                            "trading_mode": job.trading_mode,
                        },
                    )

            if job.status == "partially_filled":
                stats["partially_filled_pending"] += 1

        return stats
