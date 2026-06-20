from __future__ import annotations

import json

from shared.redis.client import RedisClient, stream_publish
from shared.redis.streams import StreamNames


class DerivedEventPublisher:
    """Publishes UnifiedDecisionSnapshot dicts to stream:analytics:derived."""

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def publish(self, snapshot_dict: dict) -> None:
        await stream_publish(
            self._redis,
            StreamNames.ANALYTICS_DERIVED,
            {"snapshot": json.dumps(snapshot_dict, default=str)},
        )
