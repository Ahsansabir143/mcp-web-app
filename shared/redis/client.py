from __future__ import annotations

import os
from functools import lru_cache
from typing import AsyncGenerator

import redis.asyncio as aioredis

RedisClient = aioredis.Redis


@lru_cache(maxsize=1)
def _make_pool() -> aioredis.ConnectionPool:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return aioredis.ConnectionPool.from_url(
        url,
        max_connections=20,
        decode_responses=True,
    )


def get_redis_client() -> RedisClient:
    """Return a Redis client using the shared connection pool."""
    return aioredis.Redis(connection_pool=_make_pool())


async def stream_publish(
    client: RedisClient,
    stream: str,
    fields: dict,
    maxlen: int = 50_000,
) -> str:
    """Publish a message to a Redis stream with MAXLEN trimming."""
    return await client.xadd(stream, fields, maxlen=maxlen, approximate=True)


async def stream_read_group(
    client: RedisClient,
    stream: str,
    group: str,
    consumer: str,
    count: int = 100,
    block_ms: int = 1000,
    last_id: str = ">",
) -> list:
    """Read from a consumer group, creating the group if it does not exist."""
    try:
        await client.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception:
        pass

    return await client.xreadgroup(
        groupname=group,
        consumername=consumer,
        streams={stream: last_id},
        count=count,
        block=block_ms,
    )
