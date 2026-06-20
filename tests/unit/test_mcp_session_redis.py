"""Tests for RedisSessionRegistry — Redis-backed MCP session metadata."""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.mcp_server.session import RedisSessionRegistry
from shared.redis.keys import RedisKeys


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_redis() -> tuple[AsyncMock, dict]:
    """Return (redis_mock, backing_store) where the store is mutated by the mock."""
    store: dict[str, str] = {}
    redis = AsyncMock()

    async def _setex(key, ttl, value):
        store[key] = value

    async def _get(key):
        return store.get(key)

    async def _delete(key):
        store.pop(key, None)
        return 1

    async def _exists(key):
        return 1 if key in store else 0

    redis.setex = _setex
    redis.get = _get
    redis.delete = _delete
    redis.exists = _exists
    # redis.expire left as AsyncMock so tests can assert call args on it
    return redis, store


# ── Session creation ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_returns_unique_session_ids():
    redis, _ = _make_redis()
    reg = RedisSessionRegistry(redis, timeout_s=300)

    sid1, _ = await reg.create(user_id="user-a")
    sid2, _ = await reg.create(user_id="user-b")
    assert sid1 != sid2


@pytest.mark.asyncio
async def test_create_stores_metadata_in_redis():
    redis, store = _make_redis()
    reg = RedisSessionRegistry(redis, timeout_s=300)

    sid, _ = await reg.create(user_id="user-1", client_id="claude")
    key = RedisKeys.mcp_session(sid)
    assert key in store

    metadata = json.loads(store[key])
    assert metadata["user_id"] == "user-1"
    assert metadata["client_id"] == "claude"
    assert "created_at" in metadata


@pytest.mark.asyncio
async def test_create_returns_asyncio_queue():
    redis, _ = _make_redis()
    reg = RedisSessionRegistry(redis, timeout_s=300)
    _, queue = await reg.create()
    assert isinstance(queue, asyncio.Queue)


@pytest.mark.asyncio
async def test_len_reflects_local_sessions():
    redis, _ = _make_redis()
    reg = RedisSessionRegistry(redis, timeout_s=300)
    assert len(reg) == 0
    await reg.create()
    await reg.create()
    assert len(reg) == 2


# ── Session retrieval ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_queue_for_live_session():
    redis, _ = _make_redis()
    reg = RedisSessionRegistry(redis, timeout_s=300)
    sid, original_queue = await reg.create(user_id="u")

    retrieved = await reg.get(sid)
    assert retrieved is original_queue


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown_session():
    redis, _ = _make_redis()
    reg = RedisSessionRegistry(redis, timeout_s=300)
    result = await reg.get("does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_get_returns_none_when_redis_ttl_expired():
    """If Redis key is gone (TTL expired), get() should evict from local and return None."""
    redis, store = _make_redis()
    reg = RedisSessionRegistry(redis, timeout_s=300)
    sid, _ = await reg.create(user_id="u")

    # Simulate TTL expiry by removing the Redis key manually
    del store[RedisKeys.mcp_session(sid)]

    result = await reg.get(sid)
    assert result is None
    assert len(reg) == 0  # evicted from local dict


# ── Session removal ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_clears_local_and_redis():
    redis, store = _make_redis()
    reg = RedisSessionRegistry(redis, timeout_s=300)
    sid, _ = await reg.create(user_id="u")
    key = RedisKeys.mcp_session(sid)
    assert key in store

    await reg.remove(sid)
    assert key not in store
    assert len(reg) == 0


@pytest.mark.asyncio
async def test_remove_nonexistent_session_is_safe():
    redis, _ = _make_redis()
    reg = RedisSessionRegistry(redis, timeout_s=300)
    # Should not raise
    await reg.remove("ghost-session")


# ── TTL refresh ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_ttl_calls_redis_expire():
    redis, _ = _make_redis()
    reg = RedisSessionRegistry(redis, timeout_s=300)
    sid, _ = await reg.create(user_id="u")

    await reg.refresh_ttl(sid)
    redis.expire.assert_called_once_with(RedisKeys.mcp_session(sid), 300)


# ── User ID retrieval ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_user_id_returns_stored_value():
    redis, _ = _make_redis()
    reg = RedisSessionRegistry(redis, timeout_s=300)
    sid, _ = await reg.create(user_id="user-xyz", client_id="claude")

    uid = await reg.get_user_id(sid)
    assert uid == "user-xyz"


@pytest.mark.asyncio
async def test_get_user_id_returns_none_for_missing_session():
    redis, _ = _make_redis()
    reg = RedisSessionRegistry(redis, timeout_s=300)

    uid = await reg.get_user_id("no-such-session")
    assert uid is None


# ── Queue isolation ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_each_session_has_independent_queue():
    redis, _ = _make_redis()
    reg = RedisSessionRegistry(redis, timeout_s=300)
    sid1, q1 = await reg.create()
    sid2, q2 = await reg.create()

    await q1.put("msg-for-session-1")
    assert q2.empty()
    assert not q1.empty()


# ── No module-level singleton ─────────────────────────────────────────────────


def test_no_module_level_registry_singleton():
    """Ensure the old module-level 'registry' singleton is gone (requires injection)."""
    import services.mcp_server.session as session_module

    assert not hasattr(session_module, "registry"), (
        "Module-level 'registry' singleton must be removed; "
        "use app.state.session_registry instead"
    )
