from __future__ import annotations

import time

from shared.redis.client import RedisClient, get_redis_client, stream_publish
from shared.redis.streams import StreamNames
from shared.schemas.enums import MarketType, Venue
from shared.schemas.events import RawEvent


class RawEventPublisher:
    """Publishes RawEvent envelopes to stream:binance:raw."""

    def __init__(self, redis: RedisClient | None = None) -> None:
        self._redis = redis or get_redis_client()

    async def publish(
        self,
        market_type: MarketType,
        source_stream: str,
        payload: dict,
    ) -> None:
        event = RawEvent(
            venue=Venue.BINANCE,
            market_type=market_type,
            source_stream=source_stream,
            received_ms=int(time.time() * 1000),
            payload=payload,
        )
        await stream_publish(
            self._redis,
            StreamNames.RAW,
            {"event": event.model_dump_json()},
        )

    async def close(self) -> None:
        await self._redis.aclose()
