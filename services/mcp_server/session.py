"""In-memory SSE session registry for the MCP server."""
from __future__ import annotations

import asyncio
import uuid


class SessionRegistry:
    """Maps session IDs to per-session asyncio Queues used to route JSON-RPC
    responses from the POST /messages handler back to the SSE stream.

    One Queue is created when the SSE connection opens; it is removed on
    disconnect or timeout.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, asyncio.Queue] = {}

    def create(self) -> tuple[str, asyncio.Queue]:
        session_id = str(uuid.uuid4())
        q: asyncio.Queue = asyncio.Queue()
        self._sessions[session_id] = q
        return session_id, q

    def get(self, session_id: str) -> asyncio.Queue | None:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._sessions)


# Module-level singleton shared across the FastAPI app instance.
registry = SessionRegistry()
