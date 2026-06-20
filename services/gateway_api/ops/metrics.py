"""Ops: aggregated platform metrics.

GET /api/ops/metrics returns a JSON snapshot of platform-observable metrics:
- Redis stream lengths and consumer group lag (throughput proxy)
- Incident counts from DB by severity
- Active safety controls (kill switch, emergency stop, cooldown keys)
- In-process gateway counters via shared.metrics

This endpoint does not require running services — it reads from Redis and
Postgres directly, making it useful even when individual services are down.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from services.gateway_api.ops.auth import verify_admin_api_key
from shared.db.models.audit import IncidentLog
from shared.metrics import get_registry
from shared.redis.keys import RedisKeys
from shared.redis.streams import StreamNames

router = APIRouter(dependencies=[Depends(verify_admin_api_key)])


@router.get("/api/ops/metrics")
async def metrics(request: Request):
    """Return aggregated platform observability metrics.

    Sources:
    - Redis XLEN per stream (message throughput proxy)
    - Redis EXISTS for emergency stop / kill switch patterns
    - Postgres COUNT(incident_log) by severity
    - In-process MetricsRegistry counters (gateway-level only)
    """
    redis = request.app.state.redis
    session_factory = request.app.state.session_factory
    timestamp = int(time.time())

    # ── Stream lengths ─────────────────────────────────────────────────────────
    stream_lengths: dict[str, int | None] = {}
    stream_group_lag: dict[str, list] = {}
    for stream in StreamNames.all():
        try:
            stream_lengths[stream] = await redis.xlen(stream)
            try:
                groups = await redis.xinfo_groups(stream)
                stream_group_lag[stream] = [
                    {"group": g.get("name"), "lag": g.get("lag"), "pending": g.get("pending")}
                    for g in groups
                ]
            except Exception:
                stream_group_lag[stream] = []
        except Exception:
            stream_lengths[stream] = None
            stream_group_lag[stream] = []

    # ── Safety controls ────────────────────────────────────────────────────────
    trading_mode = await redis.get(RedisKeys.global_trading_mode()) or "paper_only"
    emergency_stop = bool(await redis.exists(RedisKeys.global_emergency_stop()))

    # ── Incident counts ────────────────────────────────────────────────────────
    incident_counts: dict[str, int] = {}
    try:
        async with session_factory() as session:
            rows = await session.execute(
                select(IncidentLog.severity, func.count(IncidentLog.id))
                .group_by(IncidentLog.severity)
            )
            for severity, count in rows:
                incident_counts[severity] = count

            unresolved = await session.execute(
                select(func.count(IncidentLog.id)).where(IncidentLog.resolved == False)
            )
            incident_counts["_unresolved_total"] = unresolved.scalar_one()
    except Exception:
        incident_counts["_db_error"] = 1

    # ── In-process counters (gateway) ─────────────────────────────────────────
    in_process = get_registry().snapshot()

    return {
        "timestamp": timestamp,
        "streams": {
            "lengths": stream_lengths,
            "consumer_lag": stream_group_lag,
        },
        "safety": {
            "trading_mode": trading_mode,
            "emergency_stop_active": emergency_stop,
        },
        "incidents": incident_counts,
        "gateway_counters": in_process,
    }
