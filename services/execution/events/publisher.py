from __future__ import annotations

import json
import time
from typing import Any

from shared.redis.client import RedisClient, stream_publish
from shared.redis.streams import StreamNames


class ExecutionEventPublisher:
    """Publishes structured execution events to stream:execution:events."""

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def publish(
        self,
        event_type: str,
        job_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        await stream_publish(
            self._redis,
            StreamNames.EXECUTION_EVENTS,
            {
                "event_type": event_type,
                "job_id": job_id,
                "data": json.dumps(data or {}, default=str),
                "timestamp_ms": str(int(time.time() * 1000)),
            },
        )
