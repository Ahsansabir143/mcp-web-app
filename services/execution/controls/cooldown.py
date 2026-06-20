from __future__ import annotations

from shared.redis.client import RedisClient
from shared.redis.keys import RedisKeys


class CooldownControl:
    """Redis-backed symbol cooldown — prevents re-entry within a configurable window."""

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def is_on_cooldown(self, account_id: str, symbol: str) -> bool:
        return bool(await self._redis.exists(RedisKeys.symbol_cooldown(account_id, symbol)))

    async def set_cooldown(self, account_id: str, symbol: str, seconds: int) -> None:
        await self._redis.set(
            RedisKeys.symbol_cooldown(account_id, symbol), "1", ex=seconds
        )

    async def clear_cooldown(self, account_id: str, symbol: str) -> None:
        await self._redis.delete(RedisKeys.symbol_cooldown(account_id, symbol))

    async def remaining_ttl(self, account_id: str, symbol: str) -> int:
        """Return remaining cooldown seconds (-1 = no TTL, -2 = key not found)."""
        return await self._redis.ttl(RedisKeys.symbol_cooldown(account_id, symbol))
