"""Ops: Redis stream inspection.

GET /api/ops/streams returns length, consumer group lag, and pending counts
for every Redis stream in the platform. Useful for diagnosing consumer lag
and backlog accumulation without needing direct Redis access.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from services.gateway_api.ops.auth import verify_admin_api_key
from shared.redis.streams import StreamNames

router = APIRouter(dependencies=[Depends(verify_admin_api_key)])


@router.get("/api/ops/streams")
async def stream_stats(request: Request):
    """Return length and consumer group stats for all 6 platform streams.

    Fields per stream:
    - length: total messages in the stream (XLEN)
    - consumer_groups[].name: group name
    - consumer_groups[].consumers: active consumer count
    - consumer_groups[].pending: acknowledged but not yet ACKed messages
    - consumer_groups[].lag: undelivered messages to this group
    """
    redis = request.app.state.redis
    results = []

    for stream in StreamNames.all():
        entry: dict = {"stream": stream}
        try:
            entry["length"] = await redis.xlen(stream)
            try:
                raw_groups = await redis.xinfo_groups(stream)
                groups = []
                for g in raw_groups:
                    # redis-py returns dicts with str keys when decode_responses=True
                    groups.append({
                        "name": g.get("name", ""),
                        "consumers": g.get("consumers", 0),
                        "pending": g.get("pending", 0),
                        "last_delivered_id": str(g.get("last-delivered-id", "")),
                        "lag": g.get("lag"),
                    })
                entry["consumer_groups"] = groups
            except Exception:
                entry["consumer_groups"] = []
        except Exception as exc:
            entry["error"] = str(exc)
            entry["length"] = None
            entry["consumer_groups"] = []

        results.append(entry)

    return {"streams": results, "total_streams": len(results)}
