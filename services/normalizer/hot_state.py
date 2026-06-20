from __future__ import annotations

from shared.redis.client import RedisClient, get_redis_client

from services.normalizer.handlers.base import HotStateWrite


class HotStateWriter:
    """Applies a list of hot-state writes to Redis using a pipeline."""

    def __init__(self, redis: RedisClient | None = None) -> None:
        self._redis = redis or get_redis_client()

    async def write_all(self, writes: list[HotStateWrite]) -> None:
        if not writes:
            return
        async with self._redis.pipeline(transaction=False) as pipe:
            for w in writes:
                if w.ttl_s is not None:
                    pipe.set(w.key, w.value, ex=w.ttl_s)
                else:
                    pipe.set(w.key, w.value)
            await pipe.execute()

    async def close(self) -> None:
        await self._redis.aclose()
