"""In-process SSE session registry for the MCP web gateway.

Each client that connects to GET /sse gets a :class:`GatewaySession`.
Outbound MCP responses are enqueued on the session's asyncio.Queue and
streamed back to the client by the open SSE generator in main.py.

Single-replica limitation: sessions live in process memory.  If the gateway is
scaled beyond one replica, route /messages requests to the same replica that
owns the session (sticky routing), or migrate to Redis Pub/Sub.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .auth import TokenClaims


@dataclass
class GatewaySession:
    session_id: str
    claims: TokenClaims
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    created_at: float = field(default_factory=time.monotonic)
    _initialized: bool = False

    def mark_initialized(self) -> None:
        self._initialized = True

    @property
    def is_initialized(self) -> bool:
        return self._initialized


# ── Global session store ──────────────────────────────────────────────────────

_sessions: Dict[str, GatewaySession] = {}


def create_session(claims: TokenClaims) -> GatewaySession:
    session_id = secrets.token_urlsafe(24)
    session = GatewaySession(session_id=session_id, claims=claims)
    _sessions[session_id] = session
    return session


def get_session(session_id: str) -> Optional[GatewaySession]:
    return _sessions.get(session_id)


def remove_session(session_id: str) -> None:
    _sessions.pop(session_id, None)


def session_count() -> int:
    return len(_sessions)


async def enqueue(session_id: str, message: dict[str, Any]) -> bool:
    """Enqueue a JSON-RPC message for delivery on the client's SSE stream.

    Returns False if the session is not found (client already disconnected).
    """
    session = _sessions.get(session_id)
    if session is None:
        return False
    await session.queue.put(message)
    return True
