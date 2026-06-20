from __future__ import annotations

from shared.redis.client import RedisClient
from shared.redis.keys import RedisKeys


class KillSwitchControl:
    """Redis-backed kill switch, user-pause, symbol-pause, and circuit-breaker controls."""

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def is_kill_switch_active(self, account_id: str) -> bool:
        return bool(await self._redis.exists(RedisKeys.kill_switch(account_id)))

    async def activate(self, account_id: str, ttl_s: int = 86400) -> None:
        await self._redis.set(RedisKeys.kill_switch(account_id), "1", ex=ttl_s)

    async def clear(self, account_id: str) -> None:
        await self._redis.delete(RedisKeys.kill_switch(account_id))

    async def is_user_paused(self, account_id: str) -> bool:
        return bool(await self._redis.exists(RedisKeys.user_pause(account_id)))

    async def pause_user(self, account_id: str, ttl_s: int = 86400) -> None:
        await self._redis.set(RedisKeys.user_pause(account_id), "1", ex=ttl_s)

    async def resume_user(self, account_id: str) -> None:
        await self._redis.delete(RedisKeys.user_pause(account_id))

    async def is_symbol_paused(self, account_id: str, symbol: str) -> bool:
        return bool(await self._redis.exists(RedisKeys.symbol_pause(account_id, symbol)))

    async def pause_symbol(self, account_id: str, symbol: str, ttl_s: int = 3600) -> None:
        await self._redis.set(RedisKeys.symbol_pause(account_id, symbol), "1", ex=ttl_s)

    async def resume_symbol(self, account_id: str, symbol: str) -> None:
        await self._redis.delete(RedisKeys.symbol_pause(account_id, symbol))

    async def is_circuit_breaker_active(self, account_id: str) -> bool:
        return bool(await self._redis.exists(RedisKeys.circuit_breaker(account_id)))

    async def trip_circuit_breaker(self, account_id: str, window_s: int = 3600) -> None:
        await self._redis.set(RedisKeys.circuit_breaker(account_id), "1", ex=window_s)

    async def reset_circuit_breaker(self, account_id: str) -> None:
        await self._redis.delete(RedisKeys.circuit_breaker(account_id))
