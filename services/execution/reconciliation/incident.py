"""Persistent incident logging for execution anomalies."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.db.models.audit import IncidentLog
from shared.utils.logging import get_logger

log = get_logger("execution.reconciliation.incident")


class IncidentLogger:
    """Persists execution incidents to the incident_log table.

    All incidents are warning-or-above severity. ``job_id`` may be None for
    incidents that cannot be attributed to a specific job (e.g. orphan fills).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def log_incident(
        self,
        incident_type: str,
        description: str,
        severity: str = "warning",
        job_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        strategy_id: uuid.UUID | None = None,
        context: dict | None = None,
    ) -> None:
        try:
            async with self._factory() as session:
                incident = IncidentLog(
                    incident_type=incident_type,
                    severity=severity,
                    description=description,
                    job_id=job_id,
                    user_id=user_id,
                    strategy_id=strategy_id,
                    context=context or {},
                )
                session.add(incident)
                await session.commit()
        except Exception as exc:
            log.error("incident log write failed: %s", incident_type, exc_info=exc)
