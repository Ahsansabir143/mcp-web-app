"""Redis-backed SSE session registry for the MCP server.

Architecture
------------
Message delivery between the POST /messages handler and the GET /sse handler is
inherently in-process (asyncio.Queue), because the SSE TCP connection lives in one
process.  Redis stores session *metadata* (user_id, TTL, created_at) so that:

  * Sessions expire automatically when the SSE connection drops and TTL elapses.
  * Any code path (auth, health, logging) can look up the user_id for a session ID.
  * POST /messages validates session existence against Redis rather than only local state.

Cross-replica message delivery (if Railway scales to >1 replica) requires Redis
Pub/Sub in the SSE event loop — that is the natural next step and is documented
in the comment below.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from shared.redis.keys import RedisKeys


class RedisSessionRegistry:
    """SSE session registry with Redis-backed metadata and in-process queue delivery."""

    def __init__(self, redis, timeout_s: int = 300) -> None:
        self._redis = redis
        self._timeout_s = timeout_s
        self._local: dict[str, asyncio.Queue] = {}

    async def create(
        self,
        *,
        user_id: str = "",
        client_id: str = "",
    ) -> tuple[str, asyncio.Queue]:
        """Create a new session, register metadata in Redis, return (session_id, queue)."""
        session_id = str(uuid.uuid4())
        q: asyncio.Queue = asyncio.Queue()
        self._local[session_id] = q
        await self._persist(session_id, user_id=user_id, client_id=client_id)
        return session_id, q

    async def _persist(self, session_id: str, *, user_id: str, client_id: str) -> None:
        payload = json.dumps({
            "user_id": user_id,
            "client_id": client_id,
            "created_at": int(time.time()),
        })
        await self._redis.setex(RedisKeys.mcp_session(session_id), self._timeout_s, payload)

    async def get(self, session_id: str) -> asyncio.Queue | None:
        """Return the in-process queue if the session is live and not expired in Redis."""
        if session_id not in self._local:
            return None
        exists = await self._redis.exists(RedisKeys.mcp_session(session_id))
        if not exists:
            self._local.pop(session_id, None)
            return None
        return self._local[session_id]

    async def remove(self, session_id: str) -> None:
        """Remove session from both local state and Redis."""
        self._local.pop(session_id, None)
        await self._redis.delete(RedisKeys.mcp_session(session_id))

    async def refresh_ttl(self, session_id: str) -> None:
        """Reset the Redis TTL on each keepalive tick so active sessions don't expire."""
        await self._redis.expire(RedisKeys.mcp_session(session_id), self._timeout_s)

    async def get_user_id(self, session_id: str) -> str | None:
        raw = await self._redis.get(RedisKeys.mcp_session(session_id))
        if not raw:
            return None
        return json.loads(raw).get("user_id")

    def __len__(self) -> int:
        return len(self._local)
