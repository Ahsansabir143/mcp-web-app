from __future__ import annotations

from fastapi import APIRouter

from services.binance_ingest.connection.manager import ConnectionManager

router = APIRouter()

_manager: ConnectionManager | None = None


def set_manager(m: ConnectionManager) -> None:
    global _manager
    _manager = m


@router.get("/health/detail")
async def health_detail() -> dict:
    if _manager is None:
        return {"status": "starting", "connections": {}}

    conns = {
        cid: {
            "market_type": info.market_type,
            "stream_type": info.stream_type,
            "state": info.state.value,
            "reconnect_count": info.reconnect_count,
            "messages_received": info.messages_received,
            "error": info.error,
        }
        for cid, info in _manager.connections.items()
    }
    all_connected = all(c["state"] == "connected" for c in conns.values()) if conns else False
    return {
        "status": "ok" if all_connected else "degraded",
        "connections": conns,
    }
