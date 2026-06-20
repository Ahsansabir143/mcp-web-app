from __future__ import annotations

from shared.redis.client import RedisClient, get_redis_client, stream_publish
from shared.redis.streams import StreamNames
from shared.schemas.events import NormalizedEvent


class NormalizedEventPublisher:
    """Publishes NormalizedEvent envelopes to stream:binance:normalized."""

    def __init__(self, redis: RedisClient | None = None) -> None:
        self._redis = redis or get_redis_client()

    async def publish(self, event: NormalizedEvent) -> None:
        await stream_publish(
            self._redis,
            StreamNames.NORMALIZED,
            {"event": event.model_dump_json()},
        )

    async def close(self) -> None:
        await self._redis.aclose()
