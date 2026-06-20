from __future__ import annotations

from fastapi import APIRouter
from shared.utils.logging import get_logger

log = get_logger("analytics.health")

router = APIRouter()

# Set by main.py after the consumer is created
_consumer = None


def set_consumer(consumer) -> None:
    global _consumer
    _consumer = consumer


@router.get("/health")
async def health():
    return {"status": "ok", "service": "analytics"}


@router.get("/health/detail")
async def health_detail():
    if _consumer is None:
        return {"status": "starting", "service": "analytics", "symbols": []}

    store = _consumer._store
    states = store.all_states()

    symbols = []
    for s in states:
        symbols.append({
            "symbol": s.symbol,
            "market_type": s.market_type,
            "book_integrity": s.integrity.to_dict(),
            "last_update_ms": s.last_update_ms,
            "indicator_intervals": list(s.indicators.keys()),
            "cvd": s.flow.cvd,
        })

    return {
        "status": "ok",
        "service": "analytics",
        "active_symbols": len(symbols),
        "symbols": symbols,
    }
